from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.services import qwen_client


_CLIENT: Any | None = None
_CLIENT_ERROR: str | None = None
_READY_COLLECTIONS: set[tuple[str, int]] = set()
_QDRANT_CLIENT: Any | None = None
_QDRANT_ERROR: str | None = None
_QDRANT_READY_COLLECTIONS: set[tuple[str, int]] = set()


def backend_name() -> str:
    return (settings.vector_backend or "local").strip().lower()


def embedding_provider() -> str:
    return (settings.vector_embedding_provider or "local").strip().lower()


def milvus_enabled() -> bool:
    return backend_name() == "milvus"


def qdrant_enabled() -> bool:
    return backend_name() == "qdrant"


def enabled() -> bool:
    """Whether an external vector backend is selected and can be indexed."""
    return milvus_enabled() or qdrant_enabled()


def status() -> dict[str, Any]:
    client = _client() if milvus_enabled() else (_qdrant_client() if qdrant_enabled() else None)
    error = _CLIENT_ERROR if milvus_enabled() else (_QDRANT_ERROR if qdrant_enabled() else None)
    return {
        "vector_backend": backend_name(),
        "vector_store_enabled": bool(client),
        "vector_store_available": bool(client),
        "vector_store_error": error,
        "embedding_provider": embedding_provider(),
        "embedding_model": (
            qwen_client.selected_model("embedding")
            if embedding_provider() == "qwen"
            else settings.ollama_embedding_model
            if embedding_provider() == "ollama"
            else "local-hash"
        ),
        "embedding_dimension": _target_dimension(),
        "milvus_uri": settings.milvus_uri if milvus_enabled() else "",
        "milvus_collection_knowledge": _tenant_collection(settings.milvus_collection_knowledge),
        "milvus_collection_rag_cases": _tenant_collection(settings.milvus_collection_rag_cases),
        "qdrant_url": (
            f"local:{settings.resolve_local_path(settings.qdrant_path)}"
            if qdrant_enabled() and settings.qdrant_path
            else settings.qdrant_url if qdrant_enabled() else ""
        ),
        "qdrant_collection_knowledge": settings.qdrant_collection_knowledge if qdrant_enabled() else "",
        "qdrant_collection_rag_cases": settings.qdrant_collection_rag_cases if qdrant_enabled() else "",
        "local_vector_fallback": True,
    }


def upsert_knowledge_chunks(records: list[dict[str, Any]]) -> bool:
    result = upsert_knowledge_chunks_with_stats(records)
    return bool(records) and bool(result["complete"])


def upsert_knowledge_chunks_with_stats(records: list[dict[str, Any]]) -> dict[str, int | bool]:
    if qdrant_enabled():
        return _qdrant_upsert_with_stats(settings.qdrant_collection_knowledge, records, id_key="chunk_id")
    succeeded = _upsert_records(_tenant_collection(settings.milvus_collection_knowledge), records, id_key="chunk_id")
    indexed = len(records) if succeeded else 0
    return {"attempted": len(records), "indexed": indexed, "complete": succeeded}


def delete_knowledge_chunks(chunk_ids: Iterable[int]) -> bool:
    if qdrant_enabled():
        return _qdrant_delete(settings.qdrant_collection_knowledge, chunk_ids)
    return _delete_ids(_tenant_collection(settings.milvus_collection_knowledge), chunk_ids)


def search_knowledge_chunks(query: str, *, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if qdrant_enabled():
        return _qdrant_search(settings.qdrant_collection_knowledge, query, limit=limit, result_id_key="chunk_id", filters=filters)
    return _search(_tenant_collection(settings.milvus_collection_knowledge), query, limit=limit, result_id_key="chunk_id")


def search_knowledge_chunks_batch(
    queries: list[str],
    *,
    limit: int = 24,
    filters: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Batch dense retrieval for chapter mapping while preserving query order."""
    if not qdrant_enabled():
        return [[] for _ in queries]
    return _qdrant_search_many(
        settings.qdrant_collection_knowledge,
        queries,
        limit=limit,
        result_id_key="chunk_id",
        filters=filters,
    )


def upsert_rag_case_chunks(records: list[dict[str, Any]]) -> bool:
    result = upsert_rag_case_chunks_with_stats(records)
    return bool(records) and bool(result["complete"])


def upsert_rag_case_chunks_with_stats(records: list[dict[str, Any]]) -> dict[str, int | bool]:
    if qdrant_enabled():
        return _qdrant_upsert_with_stats(settings.qdrant_collection_rag_cases, records, id_key="chunk_id")
    succeeded = _upsert_records(_tenant_collection(settings.milvus_collection_rag_cases), records, id_key="chunk_id")
    indexed = len(records) if succeeded else 0
    return {"attempted": len(records), "indexed": indexed, "complete": succeeded}


def delete_rag_case_chunks(chunk_ids: Iterable[int]) -> bool:
    if qdrant_enabled():
        return _qdrant_delete(settings.qdrant_collection_rag_cases, chunk_ids)
    return _delete_ids(_tenant_collection(settings.milvus_collection_rag_cases), chunk_ids)


def search_rag_case_chunks(query: str, *, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if qdrant_enabled():
        return _qdrant_search(settings.qdrant_collection_rag_cases, query, limit=limit, result_id_key="chunk_id", filters=filters)
    return _search(_tenant_collection(settings.milvus_collection_rag_cases), query, limit=limit, result_id_key="chunk_id")


def _tenant_collection(base_name: str) -> str:
    tenant = get_current_tenant()
    if tenant is None or tenant.company_id == "company_default":
        return base_name
    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", tenant.company_id)[:40]
    return f"{base_name}_{suffix}"


def _target_dimension() -> int:
    if embedding_provider() == "qwen":
        return max(1, int(settings.qwen_embedding_dimensions or 1024))
    if embedding_provider() == "ollama":
        return max(1, int(settings.ollama_embedding_dimensions or 1024))
    return max(1, int(settings.vector_embedding_dimension or 128))


def _qdrant_tenant_id() -> str:
    tenant = get_current_tenant()
    return str(tenant.company_id) if tenant is not None else "company_default"


def _qdrant_point_id(collection_name: str, chunk_id: int) -> str:
    """Use a tenant-scoped UUID so company databases can share collections safely."""
    seed = f"wordagent:{_qdrant_tenant_id()}:{collection_name}:{int(chunk_id)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _qdrant_client() -> Any | None:
    global _QDRANT_CLIENT, _QDRANT_ERROR
    if _QDRANT_CLIENT is not None:
        return _QDRANT_CLIENT
    try:
        from qdrant_client import QdrantClient  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        _QDRANT_ERROR = f"qdrant-client unavailable: {exc}"
        return None
    try:
        if settings.qdrant_path:
            local_path = settings.resolve_local_path(settings.qdrant_path)
            local_path.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, Any] = {"path": str(local_path)}
        else:
            kwargs = {"url": settings.qdrant_url, "timeout": settings.qdrant_timeout_seconds}
            if settings.qdrant_api_key:
                kwargs["api_key"] = settings.qdrant_api_key
        _QDRANT_CLIENT = QdrantClient(**kwargs)
        _QDRANT_ERROR = None
        return _QDRANT_CLIENT
    except Exception as exc:  # pragma: no cover - external service
        _QDRANT_ERROR = str(exc)[:500]
        return None


def _qdrant_filter(extra: dict[str, Any] | None = None) -> Any:
    from qdrant_client.http import models  # type: ignore

    conditions = [
        models.FieldCondition(
            key="company_id",
            match=models.MatchValue(value=_qdrant_tenant_id()),
        )
    ]
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            allowed = list(value)
            if allowed:
                conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=allowed)))
        else:
            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
    return models.Filter(
        must=conditions
    )


def _qdrant_ensure_collection(collection_name: str, dimension: int) -> bool:
    client = _qdrant_client()
    if client is None:
        return False
    key = (collection_name, dimension)
    if key in _QDRANT_READY_COLLECTIONS:
        return True
    try:
        from qdrant_client.http import models  # type: ignore

        if not client.collection_exists(collection_name=collection_name):
            try:
                client.create_collection(
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                )
            except Exception:
                # A second worker may create the shared collection between the
                # existence check and create call. Validate it below.
                if not client.collection_exists(collection_name=collection_name):
                    raise
        info = client.get_collection(collection_name=collection_name)
        vectors_config = info.config.params.vectors
        if isinstance(vectors_config, dict):
            raise ValueError("Qdrant collection uses named vectors; WordAgent expects one unnamed vector")
        existing_dimension = int(vectors_config.size)
        if existing_dimension != dimension:
            raise ValueError(
                f"Qdrant collection dimension mismatch: collection={collection_name}, "
                f"existing={existing_dimension}, requested={dimension}. Use a new versioned collection and rebuild."
            )
        # Payload indexes make company/category/project filters fast. Index
        # creation is idempotent for the same schema on Qdrant.
        payload_indexes = (
            ("company_id", models.PayloadSchemaType.KEYWORD),
            ("item_id", models.PayloadSchemaType.INTEGER),
            ("case_id", models.PayloadSchemaType.INTEGER),
            ("category_id", models.PayloadSchemaType.INTEGER),
            ("project_id", models.PayloadSchemaType.INTEGER),
            ("doc_role", models.PayloadSchemaType.KEYWORD),
            ("chunk_kind", models.PayloadSchemaType.KEYWORD),
        )
        for field_name, field_schema in (() if settings.qdrant_path else payload_indexes):
            try:
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
            except Exception as exc:
                # Existing indexes and older Qdrant builds are harmless here;
                # the vector collection remains usable without the index.
                if "already exists" not in str(exc).lower():
                    print(f"[Qdrant] payload index skipped field={field_name}: {exc}", flush=True)
        _QDRANT_READY_COLLECTIONS.add(key)
        return True
    except Exception as exc:  # pragma: no cover - external service
        global _QDRANT_ERROR
        _QDRANT_ERROR = f"collection {collection_name} unavailable: {str(exc)[:420]}"
        print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
        return False


def _qdrant_upsert(collection_name: str, records: list[dict[str, Any]], *, id_key: str) -> bool:
    result = _qdrant_upsert_with_stats(collection_name, records, id_key=id_key)
    return bool(records) and bool(result["complete"])


def _qdrant_upsert_with_stats(
    collection_name: str,
    records: list[dict[str, Any]],
    *,
    id_key: str,
) -> dict[str, int | bool]:
    global _QDRANT_ERROR
    attempted = len(records)
    if not records:
        return {"attempted": 0, "indexed": 0, "complete": True}
    if not qdrant_enabled() or _qdrant_client() is None:
        return {"attempted": attempted, "indexed": 0, "complete": False}
    indexed = 0
    try:
        from qdrant_client.http import models  # type: ignore

        tenant_id = _qdrant_tenant_id()
        batch_size = 100
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            batch_indexed = False
            last_error = ""
            embedding_unavailable = False
            retry_count = max(1, min(int(settings.qdrant_write_retries or 3), 5))
            for attempt in range(retry_count):
                texts = [str(record.get("content") or record.get("text") or "") for record in batch]
                vectors = _embed_texts(texts, allow_fallback=False)
                if len(vectors) != len(batch):
                    last_error = f"embedding count mismatch: expected {len(batch)}, got {len(vectors)}"
                    # An unavailable local Ollama endpoint returns no vectors;
                    # retrying every Qdrant batch would repeatedly wait for the
                    # same failure and hold up unrelated document operations.
                    embedding_unavailable = True
                    break
                elif not _qdrant_ensure_collection(collection_name, len(vectors[0])):
                    last_error = _QDRANT_ERROR or "collection unavailable"
                else:
                    points = []
                    for record, text, vector in zip(batch, texts, vectors):
                        chunk_id = int(record.get(id_key) or record.get("id") or 0)
                        if chunk_id <= 0 or not vector:
                            continue
                        points.append(
                            models.PointStruct(
                                id=_qdrant_point_id(collection_name, chunk_id),
                                vector=vector,
                                payload={
                                    "company_id": tenant_id,
                                    "chunk_id": chunk_id,
                                    "item_id": int(record.get("item_id") or 0),
                                    "category_id": int(record.get("category_id") or 0),
                                    "case_id": int(record.get("case_id") or 0),
                                    "document_id": int(record.get("document_id") or 0),
                                    "project_id": int(record.get("project_id") or 0),
                                    "doc_role": str(record.get("doc_role") or ""),
                                    "chunk_kind": str(record.get("chunk_kind") or ""),
                                    "section_title": str(record.get("section_title") or record.get("chunk_title") or "")[:500],
                                    "section_path": str(record.get("section_path") or record.get("section_title") or record.get("chunk_title") or "")[:1000],
                                    "page_number": int(record.get("page_number") or 0),
                                    "source_name": str(record.get("source_name") or record.get("case_name") or "")[:500],
                                    "content": text[:30000],
                                },
                            )
                        )
                    if points:
                        try:
                            _qdrant_client().upsert(collection_name=collection_name, points=points, wait=True)  # type: ignore[union-attr]
                            indexed += len(points)
                            batch_indexed = True
                            break
                        except Exception as exc:
                            last_error = str(exc)[:460]
                if attempt + 1 < retry_count:
                    time.sleep(min(2.0, 0.25 * (2**attempt)))
            if not batch_indexed:
                _QDRANT_ERROR = f"batch write failed after {retry_count} attempts: {last_error}"
                print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
                if embedding_unavailable:
                    break
                # Keep processing later batches. A transient Ollama/Qdrant
                # failure must not turn every remaining chunk of this item
                # into a reported failure; the next reindex can retry only
                # the genuinely missing points.
                continue
        if not indexed:
            return {"attempted": attempted, "indexed": 0, "complete": False}
        print(f"[Qdrant] upserted collection={collection_name} count={indexed} dim={_target_dimension()}", flush=True)
        return {"attempted": attempted, "indexed": indexed, "complete": indexed == attempted}
    except Exception as exc:  # pragma: no cover - external service
        _QDRANT_ERROR = f"upsert failed: {str(exc)[:460]}"
        print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
        return {"attempted": attempted, "indexed": indexed, "complete": False}


def _qdrant_delete(collection_name: str, ids: Iterable[int]) -> bool:
    values = [int(item) for item in ids if int(item or 0) > 0]
    client = _qdrant_client()
    if not values or client is None or not qdrant_enabled():
        return False
    try:
        from qdrant_client.http import models  # type: ignore

        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="company_id", match=models.MatchValue(value=_qdrant_tenant_id())),
                    models.FieldCondition(key="chunk_id", match=models.MatchAny(any=values)),
                ]
            )
        )
        client.delete(collection_name=collection_name, points_selector=selector, wait=True)
        print(f"[Qdrant] deleted collection={collection_name} count={len(values)}", flush=True)
        return True
    except Exception as exc:  # pragma: no cover - external service
        global _QDRANT_ERROR
        _QDRANT_ERROR = f"delete failed: {str(exc)[:460]}"
        print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
        return False


def _qdrant_search(
    collection_name: str,
    query: str,
    *,
    limit: int,
    result_id_key: str,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not query.strip() or not qdrant_enabled() or _qdrant_client() is None:
        return []
    vectors = _embed_texts([query], allow_fallback=False)
    if not vectors or not _qdrant_ensure_collection(collection_name, len(vectors[0])):
        return []
    try:
        client = _qdrant_client()
        query_filter = _qdrant_filter(filters)
        try:
            response = client.query_points(  # type: ignore[union-attr]
                collection_name=collection_name,
                query=vectors[0],
                query_filter=query_filter,
                limit=max(1, min(limit, 500)),
                with_payload=True,
            )
            points = response.points
        except AttributeError:
            points = client.search(  # type: ignore[union-attr]
                collection_name=collection_name,
                query_vector=vectors[0],
                query_filter=query_filter,
                limit=max(1, min(limit, 500)),
                with_payload=True,
            )
        results: list[dict[str, Any]] = []
        for point in points:
            payload = dict(point.payload or {})
            chunk_id = int(payload.get("chunk_id") or 0)
            if chunk_id <= 0:
                continue
            results.append({result_id_key: chunk_id, "score": float(point.score or 0.0), **payload})
        return results
    except Exception as exc:  # pragma: no cover - external service
        global _QDRANT_ERROR
        _QDRANT_ERROR = f"search failed: {str(exc)[:460]}"
        print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
        return []


def _qdrant_search_many(
    collection_name: str,
    queries: list[str],
    *,
    limit: int,
    result_id_key: str,
    filters: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    results: list[list[dict[str, Any]]] = [[] for _ in queries]
    valid = [(index, query.strip()) for index, query in enumerate(queries) if query and query.strip()]
    if not valid or not qdrant_enabled() or _qdrant_client() is None:
        return results
    vectors = _embed_texts([query for _, query in valid], allow_fallback=False)
    if len(vectors) != len(valid) or not vectors:
        return results
    if not _qdrant_ensure_collection(collection_name, len(vectors[0])):
        return results
    try:
        from qdrant_client.http import models  # type: ignore

        client = _qdrant_client()
        query_filter = _qdrant_filter(filters)
        pairs = list(zip(valid, vectors, strict=False))
        for start in range(0, len(pairs), 96):
            batch = pairs[start : start + 96]
            batch_results: list[Any]
            if hasattr(client, "query_batch_points"):
                requests = [
                    models.QueryRequest(
                        query=vector,
                        filter=query_filter,
                        limit=max(1, min(limit, 500)),
                        with_payload=True,
                    )
                    for _, vector in batch
                ]
                batch_results = client.query_batch_points(  # type: ignore[union-attr]
                    collection_name=collection_name,
                    requests=requests,
                )
            else:
                batch_results = []
                for _, vector in batch:
                    try:
                        response = client.query_points(  # type: ignore[union-attr]
                            collection_name=collection_name,
                            query=vector,
                            query_filter=query_filter,
                            limit=max(1, min(limit, 500)),
                            with_payload=True,
                        )
                        batch_results.append(response)
                    except AttributeError:
                        batch_results.append(client.search(  # type: ignore[union-attr]
                            collection_name=collection_name,
                            query_vector=vector,
                            query_filter=query_filter,
                            limit=max(1, min(limit, 500)),
                            with_payload=True,
                        ))
            for ((query_index, _query), _vector), response in zip(batch, batch_results, strict=False):
                points = getattr(response, "points", response)
                for point in points or []:
                    payload = dict(getattr(point, "payload", None) or {})
                    chunk_id = int(payload.get("chunk_id") or 0)
                    if chunk_id > 0:
                        results[query_index].append({
                            result_id_key: chunk_id,
                            "score": float(getattr(point, "score", 0.0) or 0.0),
                            **payload,
                        })
        return results
    except Exception as exc:  # pragma: no cover - external service
        global _QDRANT_ERROR
        _QDRANT_ERROR = f"batch search failed: {str(exc)[:460]}"
        print(f"[Qdrant] {_QDRANT_ERROR}", flush=True)
        return results


def _client() -> Any | None:
    global _CLIENT, _CLIENT_ERROR
    if _CLIENT is not None:
        return _CLIENT
    if not milvus_enabled():
        return None
    try:
        from pymilvus import MilvusClient  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        _CLIENT_ERROR = f"pymilvus unavailable: {exc}"
        return None

    kwargs: dict[str, Any] = {"uri": settings.milvus_uri, "timeout": settings.milvus_timeout_seconds}
    if settings.milvus_token:
        kwargs["token"] = settings.milvus_token
    if settings.milvus_db_name:
        kwargs["db_name"] = settings.milvus_db_name
    try:
        _CLIENT = MilvusClient(**kwargs)
        _CLIENT_ERROR = None
        return _CLIENT
    except TypeError:
        # Older PyMilvus builds may not support db_name/timeout in the
        # constructor.  Retry with the stable minimal argument set.
        kwargs.pop("db_name", None)
        kwargs.pop("timeout", None)
        try:
            _CLIENT = MilvusClient(**kwargs)
            _CLIENT_ERROR = None
            return _CLIENT
        except Exception as exc:  # pragma: no cover - external service
            _CLIENT_ERROR = str(exc)[:500]
            return None
    except Exception as exc:  # pragma: no cover - external service
        _CLIENT_ERROR = str(exc)[:500]
        return None


def _ensure_collection(collection_name: str, dimension: int) -> bool:
    client = _client()
    if client is None:
        return False
    key = (collection_name, dimension)
    if key in _READY_COLLECTIONS:
        return True
    try:
        if not client.has_collection(collection_name=collection_name):
            try:
                from pymilvus import DataType  # type: ignore

                schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
                schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
                schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
                schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=30000)
                schema.add_field(field_name="item_id", datatype=DataType.INT64)
                schema.add_field(field_name="category_id", datatype=DataType.INT64)
                schema.add_field(field_name="case_id", datatype=DataType.INT64)
                schema.add_field(field_name="document_id", datatype=DataType.INT64)
                schema.add_field(field_name="doc_role", datatype=DataType.VARCHAR, max_length=64)
                schema.add_field(field_name="chunk_kind", datatype=DataType.VARCHAR, max_length=128)
                schema.add_field(field_name="section_title", datatype=DataType.VARCHAR, max_length=512)
                schema.add_field(field_name="page_number", datatype=DataType.INT64)
                schema.add_field(field_name="source_name", datatype=DataType.VARCHAR, max_length=512)
                schema.add_field(field_name="updated_at", datatype=DataType.VARCHAR, max_length=64)
                index_params = client.prepare_index_params()
                index_params.add_index(field_name="vector", metric_type="COSINE", index_type="AUTOINDEX")
                client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params, consistency_level="Strong")
            except Exception:
                try:
                    client.create_collection(
                        collection_name=collection_name,
                        dimension=dimension,
                        metric_type="COSINE",
                        consistency_level="Strong",
                        enable_dynamic_field=True,
                    )
                except TypeError:
                    client.create_collection(collection_name=collection_name, dimension=dimension, metric_type="COSINE")
        try:
            client.load_collection(collection_name=collection_name)
        except Exception:
            pass
        _READY_COLLECTIONS.add(key)
        return True
    except Exception as exc:  # pragma: no cover - external service
        global _CLIENT_ERROR
        _CLIENT_ERROR = f"collection {collection_name} unavailable: {str(exc)[:420]}"
        print(f"[VectorStore] {_CLIENT_ERROR}", flush=True)
        return False


def _upsert_records(collection_name: str, records: list[dict[str, Any]], *, id_key: str) -> bool:
    if not records or not milvus_enabled():
        return False
    texts = [str(record.get("content") or record.get("text") or "") for record in records]
    vectors = _embed_texts(texts)
    if not vectors:
        return False
    dimension = len(vectors[0])
    if not _ensure_collection(collection_name, dimension):
        return False
    now = datetime.now(timezone.utc).isoformat()
    data: list[dict[str, Any]] = []
    for record, vector in zip(records, vectors):
        record_id = int(record.get(id_key) or record.get("id") or 0)
        if record_id <= 0 or not vector:
            continue
        data.append(
            {
                "id": record_id,
                "vector": vector,
                "content": str(record.get("content") or record.get("text") or "")[:30000],
                "item_id": int(record.get("item_id") or 0),
                "category_id": int(record.get("category_id") or 0),
                "case_id": int(record.get("case_id") or 0),
                "document_id": int(record.get("document_id") or 0),
                "doc_role": str(record.get("doc_role") or ""),
                "chunk_kind": str(record.get("chunk_kind") or ""),
                "section_title": str(record.get("section_title") or record.get("chunk_title") or "")[:500],
                "page_number": int(record.get("page_number") or 0),
                "source_name": str(record.get("source_name") or record.get("case_name") or "")[:500],
                "updated_at": now,
            }
        )
    if not data:
        return False
    try:
        _client().upsert(collection_name=collection_name, data=data)  # type: ignore[union-attr]
        print(f"[VectorStore] upserted collection={collection_name} count={len(data)} dim={dimension}", flush=True)
        return True
    except Exception as exc:  # pragma: no cover - external service
        global _CLIENT_ERROR
        _CLIENT_ERROR = f"upsert failed: {str(exc)[:460]}"
        print(f"[VectorStore] {_CLIENT_ERROR}", flush=True)
        return False


def _delete_ids(collection_name: str, ids: Iterable[int]) -> bool:
    values = [int(item) for item in ids if int(item or 0) > 0]
    if not values or not milvus_enabled() or _client() is None:
        return False
    try:
        if not _client().has_collection(collection_name=collection_name):  # type: ignore[union-attr]
            return False
        try:
            _client().delete(collection_name=collection_name, ids=values)  # type: ignore[union-attr]
        except TypeError:
            expr = f"id in [{','.join(str(item) for item in values)}]"
            _client().delete(collection_name=collection_name, filter=expr)  # type: ignore[union-attr]
        print(f"[VectorStore] deleted collection={collection_name} count={len(values)}", flush=True)
        return True
    except Exception as exc:  # pragma: no cover - external service
        global _CLIENT_ERROR
        _CLIENT_ERROR = f"delete failed: {str(exc)[:460]}"
        print(f"[VectorStore] {_CLIENT_ERROR}", flush=True)
        return False


def _search(collection_name: str, query: str, *, limit: int, result_id_key: str) -> list[dict[str, Any]]:
    if not query.strip() or not milvus_enabled():
        return []
    vectors = _embed_texts([query])
    if not vectors:
        return []
    query_vector = vectors[0]
    if not _ensure_collection(collection_name, len(query_vector)):
        return []
    try:
        response = _client().search(  # type: ignore[union-attr]
            collection_name=collection_name,
            data=[query_vector],
            limit=max(1, min(limit, 500)),
            output_fields=["content", "item_id", "category_id", "case_id", "document_id", "doc_role", "chunk_kind", "section_title", "source_name"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
    except Exception as exc:  # pragma: no cover - external service
        global _CLIENT_ERROR
        _CLIENT_ERROR = f"search failed: {str(exc)[:460]}"
        print(f"[VectorStore] {_CLIENT_ERROR}", flush=True)
        return []
    hits = response[0] if response else []
    results: list[dict[str, Any]] = []
    for hit in hits:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
        record_id = hit.get("id") if isinstance(hit, dict) else None
        if record_id is None and isinstance(entity, dict):
            record_id = entity.get("id")
        try:
            chunk_id = int(record_id)
        except Exception:
            continue
        score = hit.get("distance", hit.get("score", 0.0)) if isinstance(hit, dict) else 0.0
        result = {result_id_key: chunk_id, "score": float(score or 0.0)}
        if isinstance(entity, dict):
            result.update(entity)
        results.append(result)
    return results


def _embed_texts(texts: list[str], *, allow_fallback: bool = True) -> list[list[float]]:
    provider = embedding_provider()
    target_dim = _target_dimension()
    if provider == "qwen":
        result = qwen_client.embedding_vectors(texts, dimensions=target_dim)
        if result.vectors:
            return [_normalize_vector(vector) for vector in result.vectors]
        if result.error:
            print(f"[VectorStore] qwen embedding fallback: {result.error}", flush=True)
            if not allow_fallback:
                return []
    elif provider == "ollama":
        result = qwen_client.ollama_embedding_vectors(texts)
        if result.vectors:
            return [_normalize_vector(vector) for vector in result.vectors]
        if result.error:
            print(f"[VectorStore] ollama embedding unavailable: {result.error}", flush=True)
            if not allow_fallback:
                return []
    if not allow_fallback:
        return []
    return [_local_hash_embedding(text, target_dim) for text in texts]


def close() -> None:
    """Release local Qdrant file locks during graceful application shutdown."""
    global _QDRANT_CLIENT
    client = _QDRANT_CLIENT
    _QDRANT_CLIENT = None
    _QDRANT_READY_COLLECTIONS.clear()
    if client is not None:
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            print(f"[Qdrant] close failed: {str(exc)[:240]}", flush=True)


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector)) or 1.0
    return [round(float(value) / norm, 8) for value in vector]


def _local_hash_embedding(text: str, dim: int) -> list[float]:
    tokens = _local_tokens(text)
    vec = [0.0] * dim
    for token in tokens:
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [round(value / norm, 8) for value in vec]


def _local_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", "", text.lower())
    tokens = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,8}", text.lower())
    for index in range(max(0, len(cleaned) - 1)):
        pair = cleaned[index : index + 2]
        if re.match(r"^[\u4e00-\u9fff]{2}$", pair):
            tokens.append(pair)
    return list(dict.fromkeys(tokens))[:1200]
