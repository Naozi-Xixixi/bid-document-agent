from __future__ import annotations

import json
import mimetypes
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF builds
    import fitz
from fastapi import UploadFile

from app.core.config import settings
from app.schemas.bid_content import ChapterContentSaveRequest
from app.schemas.knowledge import KnowledgeItemUpdateRequest, KnowledgeReuseRequest
from app.services import bid_content_service, storage


class KnowledgeServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yml", ".yaml"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".webp"}


def _now_text() -> str:
    return storage.now_utc().isoformat()


def _parse_tags(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                raw_items = parsed if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                raw_items = re.split(r"[,，;；\s]+", text)
        else:
            raw_items = re.split(r"[,，;；\s]+", text)
    return list(dict.fromkeys(str(item).strip() for item in raw_items if str(item).strip()))[:30]


def _row_to_item(row: Any) -> dict[str, Any]:
    tags = _parse_tags(row["tags_json"] or "[]")
    return {
        "item_id": int(row["item_id"]),
        "id": int(row["item_id"]),
        "title": row["title"],
        "type": row["item_type"],
        "item_type": row["item_type"],
        "industry": row["industry"],
        "region": row["region"],
        "year": int(row["year"]) if row["year"] is not None else None,
        "project_type": row["project_type"],
        "tags": tags,
        "valid_until": row["valid_until"],
        "status": row["status"],
        "source_name": row["source_name"],
        "file_path": row["file_path"],
        "file_size": int(row["file_size"] or 0),
        "content_preview": (row["content_text"] or "")[:420],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


async def ingest_items(
    files: list[UploadFile],
    *,
    item_type: str = "proposal",
    industry: str = "",
    region: str = "",
    year: int | None = None,
    project_type: str = "",
    tags: str | list[str] | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    if not files:
        raise KnowledgeServiceError(40001, "请至少上传一个知识库文件")

    upload_root = settings.upload_dir / "knowledge"
    upload_root.mkdir(parents=True, exist_ok=True)
    now = _now_text()
    normalized_tags = _parse_tags(tags)
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for upload in files:
        filename = upload.filename or "knowledge-file"
        suffix = Path(filename).suffix.lower()
        if suffix and suffix not in SUPPORTED_EXTENSIONS:
            failures.append({"file_name": filename, "reason": "文件格式暂不支持"})
            continue
        try:
            file_id = f"{uuid.uuid4().hex}{suffix or '.bin'}"
            target = upload_root / file_id
            content = await upload.read()
            target.write_bytes(content)
            content_text = _extract_text(target, filename)
            warning = ""
            if item_type == "cert" and not valid_until:
                warning = "证书类条目建议补充有效期，避免生成时误用过期资质。"
            with storage.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_items (
                        title, item_type, industry, region, year, project_type, tags_json,
                        valid_until, status, source_name, file_path, file_size, content_text,
                        create_time, update_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        Path(filename).stem,
                        item_type or "proposal",
                        industry or None,
                        region or None,
                        year,
                        project_type or None,
                        json.dumps(normalized_tags, ensure_ascii=False),
                        valid_until or None,
                        filename,
                        _stored_file_path(target),
                        len(content),
                        content_text,
                        now,
                        now,
                    ),
                )
                item_id = int(cursor.lastrowid)
                row = conn.execute("SELECT * FROM knowledge_items WHERE item_id = ?", (item_id,)).fetchone()
                conn.commit()
            item = _row_to_item(row)
            if warning:
                item["warning"] = warning
            successes.append(item)
        except Exception as exc:
            failures.append({"file_name": filename, "reason": str(exc) or "入库失败"})

    return {
        "success_count": len(successes),
        "failure_count": len(failures),
        "items": successes,
        "failures": failures,
    }


def search_items(
    *,
    q: str = "",
    industry: str = "",
    year: int | None = None,
    item_type: str = "",
    include_expired: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    conditions = [] if include_expired else ["status = 'active'"]
    params: list[Any] = []
    if industry:
        conditions.append("industry = ?")
        params.append(industry)
    if year is not None:
        conditions.append("year = ?")
        params.append(year)
    if item_type:
        conditions.append("item_type = ?")
        params.append(item_type)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with storage.connect() as conn:
        rows = conn.execute(f"SELECT * FROM knowledge_items {where} ORDER BY update_time DESC LIMIT 300", params).fetchall()

    query_tokens = _query_tokens(q)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = _row_to_item(row)
        haystack = " ".join(
            str(part or "")
            for part in [
                item["title"],
                item["type"],
                item["industry"],
                item["region"],
                item["project_type"],
                " ".join(item["tags"]),
                row["content_text"],
            ]
        ).lower()
        score = _score_item(query_tokens, haystack)
        if query_tokens and score <= 0:
            continue
        item["relevance_score"] = round(score, 4)
        scored.append((score, item))

    scored.sort(key=lambda pair: (pair[0], pair[1]["update_time"]), reverse=True)
    items = [item for _, item in scored[: max(1, min(limit, 100))]]
    return {"items": items, "total": len(items), "query": q, "include_expired": include_expired}


def update_item(item_id: int, payload: KnowledgeItemUpdateRequest) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True, by_alias=False)
    if not values:
        return get_item(item_id)
    allowed = {
        "title": "title",
        "item_type": "item_type",
        "industry": "industry",
        "region": "region",
        "year": "year",
        "project_type": "project_type",
        "valid_until": "valid_until",
        "status": "status",
    }
    assignments: list[str] = []
    params: list[Any] = []
    if "tags" in values and values["tags"] is not None:
        assignments.append("tags_json = ?")
        params.append(json.dumps(_parse_tags(values["tags"]), ensure_ascii=False))
    for key, column in allowed.items():
        if key in values and values[key] is not None:
            assignments.append(f"{column} = ?")
            params.append(values[key])
    if not assignments:
        return get_item(item_id)
    assignments.append("update_time = ?")
    params.append(_now_text())
    params.append(item_id)
    with storage.connect() as conn:
        row = conn.execute("SELECT 1 FROM knowledge_items WHERE item_id = ?", (item_id,)).fetchone()
        if row is None:
            raise KnowledgeServiceError(40401, f"知识条目不存在：{item_id}", 404)
        conn.execute(f"UPDATE knowledge_items SET {', '.join(assignments)} WHERE item_id = ?", params)
        conn.commit()
    return get_item(item_id)


def delete_item(item_id: int) -> None:
    with storage.connect() as conn:
        cursor = conn.execute("DELETE FROM knowledge_items WHERE item_id = ?", (item_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise KnowledgeServiceError(40401, f"知识条目不存在：{item_id}", 404)


def get_item(item_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM knowledge_items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise KnowledgeServiceError(40401, f"知识条目不存在：{item_id}", 404)
    item = _row_to_item(row)
    item["content_text"] = row["content_text"] or ""
    return item


def reuse_item(item_id: int, payload: KnowledgeReuseRequest) -> dict[str, Any]:
    item = get_item(item_id)
    content = (item.get("content_text") or item.get("content_preview") or "").strip()
    if not content:
        content = f"本条目为图片或非文本资料，请在导出前人工确认插图位置：{item['source_name']}。"
    snippet = content[:3500]
    source_line = f"【知识库来源：{item['source_name']}｜条目ID：{item_id}】"
    image_marker = f"[[knowledge-image:item_id={item_id}]]" if _is_exportable_image_item(item) else ""
    try:
        current = bid_content_service.get_chapter_content(payload.bid_id, payload.chapter_id)
        previous_text = (current.get("content_text") or "").strip()
        insert_text = "\n".join(part for part in [source_line, snippet, image_marker] if part)
        if payload.insert_mode == "replace":
            next_text = insert_text
        else:
            next_text = "\n\n".join(part for part in [previous_text, insert_text] if part).strip()
        result = bid_content_service.save_chapter_content(
            payload.bid_id,
            payload.chapter_id,
            ChapterContentSaveRequest(content_text=next_text, content_html="", expected_revision=current["revision"]),
        )
    except bid_content_service.BidContentServiceError as exc:
        raise KnowledgeServiceError(exc.code, exc.message, exc.status_code) from exc

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_source_refs (item_id, bid_id, chapter_id, snippet, create_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, payload.bid_id, payload.chapter_id, snippet[:500], _now_text()),
        )
        conn.commit()
    return {"item": item, "section": result, "source_ref": {"item_id": item_id, "bid_id": payload.bid_id, "chapter_id": payload.chapter_id}}


def _is_exportable_image_item(item: dict[str, Any]) -> bool:
    suffix = Path(str(item.get("file_path") or item.get("source_name") or "")).suffix.lower()
    return suffix in {".png", ".jpg", ".jpeg", ".webp"}


def _query_tokens(q: str) -> list[str]:
    text = (q or "").strip().lower()
    if not text:
        return []
    return [token for token in re.split(r"[,，;；\s]+", text) if token]


def _score_item(tokens: list[str], haystack: str) -> float:
    if not tokens:
        return 0.01
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1.0 + min(0.5, haystack.count(token) * 0.04)
        elif len(token) > 2:
            chars = [char for char in token if char.strip()]
            matched = sum(1 for char in chars if char in haystack)
            score += matched / max(8, len(chars) * 2)
    return score


def _extract_text(path: Path, filename: str) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="ignore")[:60000]
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    if suffix == ".docx":
        return _extract_docx_text(path)
    content_type = mimetypes.guess_type(filename)[0] or ""
    if content_type.startswith("image/"):
        return f"图片素材：{filename}。可作为真实场景、证书或佐证截图插入标书。"
    return f"资料已入库：{filename}。当前格式暂只保留文件与元数据，文本抽取将在后续版本增强。"


def _stored_file_path(path: Path) -> str:
    try:
        return str(path.relative_to(settings.backend_root))
    except ValueError:
        return str(path)


def _extract_pdf_text(path: Path) -> str:
    parts: list[str] = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            if text:
                parts.append(f"[第{page_index}页]\n{text}")
            if sum(len(part) for part in parts) > 60000:
                break
    return "\n\n".join(parts)[:60000]


def _extract_docx_text(path: Path) -> str:
    """Extract DOCX text from OOXML without loading the binary media parts.

    Large bid files can contain hundreds of megabytes of embedded images.
    python-docx walks the complete package and builds object models for all
    relationships; the text path only needs document.xml. Streaming the XML
    keeps text extraction fast and leaves image OCR to the dedicated asset
    pipeline.
    """
    paragraphs: list[str] = []
    table_depth = 0
    paragraph_text: list[str] | None = None
    cell_text: list[str] | None = None
    row_cells: list[str] | None = None
    with zipfile.ZipFile(path, "r") as archive, archive.open("word/document.xml", "r") as stream:
        for event, element in ET.iterparse(stream, events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start":
                if tag == "tbl":
                    table_depth += 1
                elif tag == "tr":
                    row_cells = []
                elif tag == "tc":
                    cell_text = []
                elif tag == "p":
                    paragraph_text = []
                continue
            if tag == "t":
                value = element.text or ""
                if cell_text is not None:
                    cell_text.append(value)
                elif paragraph_text is not None:
                    paragraph_text.append(value)
            elif tag == "tc" and row_cells is not None:
                value = "".join(cell_text or []).strip()
                row_cells.append(value)
                cell_text = None
            elif tag == "tr" and row_cells:
                row = " | ".join(value for value in row_cells if value)
                if row:
                    paragraphs.append(row)
                row_cells = None
            elif tag == "p":
                value = "".join(paragraph_text or []).strip()
                if value and table_depth == 0:
                    paragraphs.append(value)
                paragraph_text = None
            elif tag == "tbl":
                table_depth = max(0, table_depth - 1)
            element.clear()
    return "\n".join(paragraphs)[:60000]
