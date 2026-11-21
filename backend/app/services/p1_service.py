from __future__ import annotations

import io
import json
import mimetypes
import re
import sqlite3
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz
from docx import Document
from fastapi import UploadFile

from app.core.config import settings
from app.services import qwen_client, storage


class P1ServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yml", ".yaml"}
DOC_EXTENSIONS = {".pdf", ".docx", ".doc"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOC_EXTENSIONS | IMAGE_EXTENSIONS | {".zip"}

QUALIFICATION_TERMS = {
    "营业执照": "营业执照",
    "食品经营": "食品经营许可",
    "食品生产": "食品生产许可",
    "许可证": "许可资质",
    "HACCP": "HACCP认证",
    "ISO": "体系认证",
    "认证": "体系认证",
    "业绩": "项目业绩",
    "财务": "财务要求",
    "人员": "人员配置",
    "设备": "设备能力",
}

DEFAULT_SCORING = [
    ("S1", "项目理解与响应", 15, "响应采购需求、服务范围、供货组织与重点难点。"),
    ("S2", "食品安全与质量控制", 25, "覆盖验收、检测、留样、追溯、应急召回等食品安全措施。"),
    ("S3", "冷链仓储与配送能力", 25, "说明冷库、冷藏车辆、温控记录、配送时效与应急调度。"),
    ("S4", "服务团队与履约保障", 20, "说明人员分工、培训、考核、售后响应和投诉处理。"),
    ("S5", "企业资质与案例证明", 15, "展示资质证书、类似项目案例和履约证明。"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def connect() -> sqlite3.Connection:
    conn = storage.connect()
    init_p1_db(conn)
    return conn


def init_p1_db(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    db = conn or storage.connect()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                purchaser TEXT,
                industry TEXT,
                region TEXT,
                deadline TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_tender_documents (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                is_ocr INTEGER NOT NULL DEFAULT 0,
                parse_status TEXT NOT NULL DEFAULT 'parsed',
                parse_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES p1_projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_outlines (
                project_id TEXT PRIMARY KEY,
                nodes_json TEXT NOT NULL,
                uncovered_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES p1_projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_knowledge_items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'proposal',
                industry TEXT,
                region TEXT,
                year INTEGER,
                project_type TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                valid_until TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                source_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                content_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_proposal_sections (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, node_id),
                FOREIGN KEY(project_id) REFERENCES p1_projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_audit_findings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                evidence TEXT,
                suggestion TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES p1_projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS p1_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 100,
                label TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_p1_tender_project ON p1_tender_documents(project_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_p1_knowledge_status ON p1_knowledge_items(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_p1_sections_project ON p1_proposal_sections(project_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_p1_findings_project ON p1_audit_findings(project_id)")
        db.commit()
    finally:
        if owns_conn:
            db.close()


def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "purchaser": row["purchaser"],
        "industry": row["industry"],
        "region": row["region"],
        "deadline": row["deadline"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_project(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _new_id("proj")
    now = _now()
    name = str(payload.get("name") or "食材配送投标项目").strip()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO p1_projects (id, name, purchaser, industry, region, deadline, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                project_id,
                name,
                str(payload.get("purchaser") or "").strip() or None,
                str(payload.get("industry") or "学校").strip() or None,
                str(payload.get("region") or "").strip() or None,
                str(payload.get("deadline") or "").strip() or None,
                now,
                now,
            ),
        )
        conn.commit()
    return get_project(project_id)


def list_projects() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM p1_projects ORDER BY updated_at DESC").fetchall()
    return {"items": [_hydrate_project(row["id"]) for row in rows]}


def get_project(project_id: str) -> dict[str, Any]:
    return _hydrate_project(project_id)


def _hydrate_project(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        project_row = conn.execute("SELECT * FROM p1_projects WHERE id = ?", (project_id,)).fetchone()
        if project_row is None:
            raise P1ServiceError(40401, "项目不存在", 404)
        tender = conn.execute(
            "SELECT id, file_name, file_size, is_ocr, parse_status, created_at FROM p1_tender_documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        outline = conn.execute("SELECT nodes_json, uncovered_json FROM p1_outlines WHERE project_id = ?", (project_id,)).fetchone()
        sections = conn.execute("SELECT id, node_id, title, status, updated_at FROM p1_proposal_sections WHERE project_id = ? ORDER BY rowid", (project_id,)).fetchall()
        findings = conn.execute("SELECT severity, status FROM p1_audit_findings WHERE project_id = ?", (project_id,)).fetchall()
    project = _project_from_row(project_row)
    project["tender_document"] = dict(tender) if tender else None
    project["outline"] = {
        "nodes": _parse_json(outline["nodes_json"], []) if outline else [],
        "uncovered_scoring_items": _parse_json(outline["uncovered_json"], []) if outline else [],
    }
    project["proposal"] = {
        "section_count": len(sections),
        "generated_count": sum(1 for row in sections if row["status"] == "generated"),
        "sections": [dict(row) for row in sections],
    }
    project["review"] = {
        "open_count": sum(1 for row in findings if row["status"] == "open"),
        "fatal_count": sum(1 for row in findings if row["status"] == "open" and row["severity"] == "fatal"),
    }
    return project


async def upload_tender_document(project_id: str, upload: UploadFile) -> dict[str, Any]:
    _ensure_project(project_id)
    file_name = upload.filename or "tender"
    suffix = Path(file_name).suffix.lower()
    if suffix and suffix not in SUPPORTED_EXTENSIONS:
        raise P1ServiceError(40001, f"不支持的招标文件格式：{suffix}")
    content = await upload.read()
    target_dir = settings.upload_dir / "p1" / "tenders"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"{uuid.uuid4().hex}{suffix or '.bin'}"
    stored.write_bytes(content)
    extracted = _extract_upload_text(stored, file_name)
    parse_result = parse_tender_text(extracted["text"], file_name, bool(extracted["is_ocr"]))
    parse_result = await enhance_parse_result_with_ai(parse_result, extracted["text"], file_name, bool(extracted["is_ocr"]))
    doc_id = _new_id("doc")
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO p1_tender_documents (
                id, project_id, file_name, file_path, file_size, text, is_ocr, parse_status, parse_result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'parsed', ?, ?, ?)
            """,
            (doc_id, project_id, file_name, _local_path(stored), len(content), extracted["text"], 1 if extracted["is_ocr"] else 0, _json(parse_result), now, now),
        )
        conn.execute("UPDATE p1_projects SET status = 'parsing', updated_at = ? WHERE id = ?", (now, project_id))
        conn.commit()
    job_id = _record_job("completed", "招标文件解读完成", {"doc_id": doc_id, "parse_result": parse_result})
    return {"job_id": job_id, "status": "completed", "doc_id": doc_id, "parse_result": parse_result}


def get_parse_result(doc_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_tender_documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40402, "招标文件不存在", 404)
    result = _parse_json(row["parse_result_json"], {})
    result.update({"doc_id": doc_id, "project_id": row["project_id"], "file_name": row["file_name"], "is_ocr": bool(row["is_ocr"])})
    return result


def qualification_match(doc_id: str) -> dict[str, Any]:
    result = get_parse_result(doc_id)
    requirements = result.get("qualification_requirements", [])
    active_knowledge = search_knowledge(q=" ".join(QUALIFICATION_TERMS), item_type="cert", limit=200)["items"]
    matches = []
    for item in requirements:
        description = str(item.get("description") or "")
        tokens = _important_terms(description)
        best = _best_knowledge_match(tokens, active_knowledge)
        if best and best["score"] >= 1.0:
            status = "satisfied"
            gap = "已在知识库中找到可佐证资质。"
            matched_ids = [best["item"]["id"]]
        elif any(term in description for term in ["必须", "须", "具备", "提供"]):
            status = "unsatisfied"
            gap = "暂未匹配到对应资质，请补充证书或证明材料。"
            matched_ids = []
        else:
            status = "need_supplement"
            gap = "建议补充证明材料或人工确认适用性。"
            matched_ids = []
        next_item = dict(item)
        next_item.update({"match_result": status, "gap_note": gap, "matched_knowledge_ids": matched_ids})
        matches.append(next_item)
    return {"doc_id": doc_id, "matches": matches, "summary": _match_summary(matches)}


def generate_outline(project_id: str) -> dict[str, Any]:
    doc = _latest_doc(project_id)
    parse_result = _parse_json(doc["parse_result_json"], {})
    scoring = parse_result.get("scoring_items") or []
    nodes = _build_outline_nodes(scoring)
    covered = {code for node in _flatten_nodes(nodes) for code in node.get("scoring_item_codes", [])}
    uncovered = [item for item in scoring if item.get("code") not in covered]
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO p1_outlines (project_id, nodes_json, uncovered_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET nodes_json = excluded.nodes_json, uncovered_json = excluded.uncovered_json, updated_at = excluded.updated_at
            """,
            (project_id, _json(nodes), _json(uncovered), now),
        )
        conn.execute("UPDATE p1_projects SET status = 'drafting', updated_at = ? WHERE id = ?", (now, project_id))
        conn.commit()
    return {"project_id": project_id, "nodes": nodes, "uncovered_scoring_items": uncovered}


def save_outline(project_id: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    _ensure_project(project_id)
    normalized = _normalize_nodes(nodes)
    now = _now()
    with connect() as conn:
        doc = conn.execute("SELECT parse_result_json FROM p1_tender_documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
        scoring = _parse_json(doc["parse_result_json"], {}).get("scoring_items", []) if doc else []
        covered = {code for node in _flatten_nodes(normalized) for code in node.get("scoring_item_codes", [])}
        uncovered = [item for item in scoring if item.get("code") not in covered]
        conn.execute(
            """
            INSERT INTO p1_outlines (project_id, nodes_json, uncovered_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET nodes_json = excluded.nodes_json, uncovered_json = excluded.uncovered_json, updated_at = excluded.updated_at
            """,
            (project_id, _json(normalized), _json(uncovered), now),
        )
        conn.commit()
    return {"project_id": project_id, "nodes": normalized, "uncovered_scoring_items": uncovered}


async def ingest_knowledge(files: list[UploadFile], payload: dict[str, Any]) -> dict[str, Any]:
    if not files:
        raise P1ServiceError(40010, "请至少选择一个知识库文件")
    target_dir = settings.upload_dir / "p1" / "knowledge"
    target_dir.mkdir(parents=True, exist_ok=True)
    item_type = str(payload.get("type") or "proposal")
    tags = _parse_tags(payload.get("tags"))
    successes = []
    failures = []
    now = _now()
    for upload in files:
        file_name = upload.filename or "knowledge-file"
        suffix = Path(file_name).suffix.lower()
        if suffix and suffix not in SUPPORTED_EXTENSIONS:
            failures.append({"file_name": file_name, "reason": "文件格式暂不支持"})
            continue
        try:
            content = await upload.read()
            target = target_dir / f"{uuid.uuid4().hex}{suffix or '.bin'}"
            target.write_bytes(content)
            extracted = _extract_upload_text(target, file_name)
            item_id = _new_id("kb")
            warning = ""
            if item_type == "cert" and not payload.get("valid_until"):
                warning = "证书类条目建议补充有效期。"
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO p1_knowledge_items (
                        id, title, item_type, industry, region, year, project_type, tags_json, valid_until,
                        status, source_name, file_path, file_size, content_text, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        str(payload.get("title") or Path(file_name).stem),
                        item_type,
                        _clean(payload.get("industry")),
                        _clean(payload.get("region")),
                        _as_int(payload.get("year")),
                        _clean(payload.get("project_type")),
                        _json(tags),
                        _clean(payload.get("valid_until")),
                        file_name,
                        _local_path(target),
                        len(content),
                        extracted["text"],
                        now,
                        now,
                    ),
                )
                conn.commit()
            item = get_knowledge_item(item_id)
            if warning:
                item["warning"] = warning
            successes.append(item)
        except Exception as exc:  # pragma: no cover - defensive per-file isolation
            failures.append({"file_name": file_name, "reason": str(exc) or "入库失败"})
    return {"success_count": len(successes), "failure_count": len(failures), "items": successes, "failures": failures}


def search_knowledge(
    *,
    q: str = "",
    industry: str = "",
    year: int | None = None,
    item_type: str = "",
    project_type: str = "",
    include_expired: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    conditions = [] if include_expired else ["status = 'active'"]
    params: list[Any] = []
    for column, value in (("industry", industry), ("item_type", item_type), ("project_type", project_type)):
        if value:
            conditions.append(f"{column} = ?")
            params.append(value)
    if year is not None:
        conditions.append("year = ?")
        params.append(year)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM p1_knowledge_items {where} ORDER BY updated_at DESC LIMIT 500", params).fetchall()
    tokens = _query_tokens(q)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = _knowledge_from_row(row)
        haystack = " ".join(
            str(part or "")
            for part in [item["title"], item["type"], item["industry"], item["region"], item["project_type"], " ".join(item["tags"]), item["content_text"]]
        ).lower()
        score = _score(tokens, haystack)
        if tokens and score <= 0:
            continue
        item["relevance_score"] = round(score, 4)
        item.pop("content_text", None)
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
    items = [item for _, item in scored[: max(1, min(limit, 100))]]
    return {"items": items, "total": len(items), "query": q}


def get_knowledge_item(item_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_knowledge_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40410, "知识条目不存在", 404)
    return _knowledge_from_row(row)


def reuse_knowledge(item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = get_knowledge_item(item_id)
    project_id = str(payload.get("project_id") or "")
    node_id = str(payload.get("node_id") or "")
    if not project_id or not node_id:
        raise P1ServiceError(40011, "缺少 project_id 或 node_id")
    node = _find_outline_node(project_id, node_id)
    section = _get_or_create_section(project_id, node)
    source_line = f"【知识库来源：{item['source_name']}｜条目ID：{item['id']}】"
    snippet = (item.get("content_text") or item.get("content_preview") or "").strip()
    if not snippet:
        snippet = f"该素材为图片或附件，请在导出前确认插入位置：{item['source_name']}。"
    block = "\n".join([source_line, snippet[:3500]])
    next_body = block if payload.get("insert_mode") == "replace" else "\n\n".join(part for part in [section["body"].strip(), block] if part)
    refs = section.get("source_refs", []) + [{"knowledge_item_id": item["id"], "file_name": item["source_name"], "snippet": snippet[:300]}]
    saved = save_section(section["id"], {"body": next_body, "source_refs": refs})
    return {"item": item, "section": saved}


def generate_proposal(project_id: str) -> dict[str, Any]:
    outline = _get_outline(project_id)
    leaves = [node for node in _flatten_nodes(outline["nodes"]) if not node.get("children")]
    if not leaves:
        raise P1ServiceError(40020, "请先生成或保存大纲")
    sections = []
    for node in leaves:
        existing = _find_section_by_node(project_id, node["id"])
        body = existing["body"] if existing and existing["body"].strip() else _render_section_body(project_id, node)
        section = _upsert_section(project_id, node, body, existing.get("source_refs", []) if existing else [])
        sections.append(section)
    job_id = _record_job("completed", "标书正文生成完成", {"project_id": project_id, "section_count": len(sections)})
    with connect() as conn:
        conn.execute("UPDATE p1_projects SET status = 'reviewing', updated_at = ? WHERE id = ?", (_now(), project_id))
        conn.commit()
    return {"job_id": job_id, "status": "completed", "sections": sections}


def list_sections(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM p1_proposal_sections WHERE project_id = ? ORDER BY rowid", (project_id,)).fetchall()
    return {"items": [_section_from_row(row) for row in rows]}


def get_section(section_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_proposal_sections WHERE id = ?", (section_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40420, "章节不存在", 404)
    return _section_from_row(row)


def save_section(section_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_proposal_sections WHERE id = ?", (section_id,)).fetchone()
        if row is None:
            raise P1ServiceError(40420, "章节不存在", 404)
        conn.execute(
            """
            UPDATE p1_proposal_sections SET body = ?, source_refs_json = ?, status = 'generated', updated_at = ? WHERE id = ?
            """,
            (
                str(payload.get("body") if payload.get("body") is not None else row["body"]),
                _json(payload.get("source_refs") if payload.get("source_refs") is not None else _parse_json(row["source_refs_json"], [])),
                now,
                section_id,
            ),
        )
        conn.commit()
    return get_section(section_id)


def regenerate_section(section_id: str) -> dict[str, Any]:
    section = get_section(section_id)
    node = _find_outline_node(section["project_id"], section["node_id"])
    body = _render_section_body(section["project_id"], node)
    saved = save_section(section_id, {"body": body, "source_refs": section.get("source_refs", [])})
    job_id = _record_job("completed", "章节重生成完成", {"section_id": section_id})
    return {"job_id": job_id, "status": "completed", "section": saved}


def review_project(project_id: str) -> dict[str, Any]:
    _ensure_project(project_id)
    findings = _build_review_findings(project_id)
    now = _now()
    with connect() as conn:
        conn.execute("DELETE FROM p1_audit_findings WHERE project_id = ?", (project_id,))
        for finding in findings:
            conn.execute(
                """
                INSERT INTO p1_audit_findings (id, project_id, category, severity, description, location, evidence, suggestion, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    finding["id"],
                    project_id,
                    finding["category"],
                    finding["severity"],
                    finding["description"],
                    finding["location"],
                    finding.get("evidence"),
                    finding["suggestion"],
                    now,
                    now,
                ),
            )
        conn.commit()
    return {"project_id": project_id, "findings": list_findings(project_id)["items"], "summary": _finding_summary(findings)}


def list_findings(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM p1_audit_findings WHERE project_id = ? ORDER BY CASE severity WHEN 'fatal' THEN 0 WHEN 'important' THEN 1 ELSE 2 END, rowid", (project_id,)).fetchall()
    return {"items": [_finding_from_row(row) for row in rows]}


def update_finding(finding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "open")
    if status not in {"open", "fixed", "ignored"}:
        raise P1ServiceError(40030, "问题状态不合法")
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_audit_findings WHERE id = ?", (finding_id,)).fetchone()
        if row is None:
            raise P1ServiceError(40430, "审核问题不存在", 404)
        conn.execute("UPDATE p1_audit_findings SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), finding_id))
        conn.commit()
    return _finding_from_row({**dict(row), "status": status, "updated_at": _now()})


def get_job(job_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40440, "任务不存在", 404)
    return {**dict(row), "result": _parse_json(row["result_json"], {})}


def export_docx(project_id: str) -> tuple[str, bytes]:
    project = get_project(project_id)
    sections = list_sections(project_id)["items"]
    if not sections:
        sections = generate_proposal(project_id)["sections"]
    doc = Document()
    doc.add_heading(project["name"], level=0)
    meta = doc.add_paragraph()
    meta.add_run("采购人：").bold = True
    meta.add_run(project.get("purchaser") or "待补充")
    meta.add_run("    业态：").bold = True
    meta.add_run(project.get("industry") or "待补充")
    meta.add_run("    区域：").bold = True
    meta.add_run(project.get("region") or "待补充")
    doc.add_paragraph("本文件由 P1 工作台生成，正文段落保留知识库来源标注，导出后请进行商务与法务复核。")
    for section in sections:
        doc.add_heading(section["title"], level=1)
        for block in [part.strip() for part in section["body"].split("\n\n") if part.strip()]:
            if block.startswith("|") and block.endswith("|"):
                _add_markdown_table(doc, block)
            else:
                doc.add_paragraph(block)
    buffer = io.BytesIO()
    doc.save(buffer)
    filename = f"{_safe_filename(project['name']) or 'proposal'}.docx"
    return filename, buffer.getvalue()


def parse_tender_text(text: str, file_name: str, is_ocr: bool = False) -> dict[str, Any]:
    lines = _clean_lines(text)
    qualifications = _extract_qualification_requirements(lines)
    scoring = _extract_scoring_items(lines)
    disquals = _extract_disqualification_clauses(lines)
    policies = _extract_policy_hints(lines, file_name)
    summary = _summarize_tender(lines, qualifications, scoring, disquals)
    return {
        "summary": summary,
        "qualification_requirements": qualifications,
        "scoring_items": scoring,
        "disqualification_clauses": disquals,
        "policy_hints": policies,
        "warnings": ["本文件经 OCR 或图片占位识别，建议人工复核关键条款。"] if is_ocr else [],
        "source_preview": "\n".join(lines[:20])[:3000],
    }


async def enhance_parse_result_with_ai(base_result: dict[str, Any], source_text: str, file_name: str, is_ocr: bool = False) -> dict[str, Any]:
    """Use Qwen to refine the BiaoShu-SKILL style rule extraction.

    The local rule path remains the source of truth when the API key is absent
    or the model response cannot be parsed, so demos never fail to render.
    """

    result = dict(base_result)
    result["analysis_method"] = "biaoshu_skill_rules"
    result["llm"] = _llm_meta(None)
    if not settings.enable_llm_parse_optimize or not settings.qwen_api_key.strip():
        return result

    compact_text = _clip_for_llm(source_text, 36000)
    fallback = _json({"summary": result.get("summary", ""), "scoring_items": result.get("scoring_items", [])})
    llm_result = await qwen_client.chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是专业标书拆标顾问，按 BiaoShu-SKILL 的方法工作：先通读招标文件，"
                    "再提取评分标准、资格门槛、关键技术/商务要求、废标条款，最后为大纲生成提供依据。"
                    "必须只输出合法 JSON，不要 Markdown，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请基于以下招标文件文本进行结构化解读。\n"
                    "输出 JSON 字段：\n"
                    "summary: 一句话概括已识别的资格、评分、废标风险；\n"
                    "industry: 行业判断，食材配送/物流运输/教育服务/医疗健康/建筑工程/IT信息化/通用；\n"
                    "project_name: 项目名称，未知则为空；\n"
                    "qualification_requirements: 数组，每项含 category, description, page_anchor{page,text}, gap_note；\n"
                    "scoring_items: 数组，每项含 code, name, score, criteria, page_anchor{page,text}；\n"
                    "disqualification_clauses: 数组，每项含 description, avoidance_advice, page_anchor{page,text}；\n"
                    "policy_hints: 数组，每项含 description, source_ref；\n"
                    "outline_strategy_notes: 3-6条大纲生成建议。\n"
                    "约束：评分项必须逐条拆分，不能把多个得分点合并成一句；code 使用 S1/S2/S3 顺序；"
                    "分值必须为数字；原文定位没有页码时 page 填 1；不要编造文件中没有的要求。\n\n"
                    f"文件名：{file_name}\nOCR标记：{is_ocr}\n"
                    f"规则初提取结果：{_json(_compact_parse_for_prompt(base_result))}\n\n"
                    f"招标文件文本：\n{compact_text}"
                ),
            },
        ], scene="tender_parse",
        temperature=0.1,
        max_tokens=5000,
        fallback=fallback,
        json_mode=True,
    )
    result["llm"] = _llm_meta(llm_result)
    parsed = _json_from_llm_content(llm_result.content)
    if not parsed:
        warnings = list(result.get("warnings") or [])
        warnings.append("千问智能解读未返回可解析结构，已使用本地规则结果。")
        result["warnings"] = warnings
        return result
    merged = _merge_ai_parse_result(result, parsed, file_name)
    merged["analysis_method"] = "biaoshu_skill_rules_qwen_refined" if llm_result.usedApi else "biaoshu_skill_rules"
    merged["llm"] = _llm_meta(llm_result)
    return merged


async def generate_outline_with_ai(project_id: str) -> dict[str, Any]:
    fallback = generate_outline(project_id)
    if not settings.enable_llm_parse_optimize or not settings.qwen_api_key.strip():
        fallback["outline_method"] = "biaoshu_skill_rules"
        fallback["llm"] = _llm_meta(None)
        return fallback

    doc = _latest_doc(project_id)
    parse_result = _parse_json(doc["parse_result_json"], {})
    scoring = parse_result.get("scoring_items") or []
    source_text = str(doc["text"] or "")
    llm_result = await qwen_client.chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是资深标书目录策划专家。按 BiaoShu-SKILL 的大纲生成方法："
                    "以评分标准为靶心，每个评分点必须在末级章节中被响应；"
                    "章节层级清晰，通常使用一级章、二级节、三级要点；"
                    "资格证明和商务附件单独归入附件或商务响应。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请为当前招标文件生成可编辑投标大纲。\n"
                    "输出 JSON 字段：nodes。nodes 为树形数组，每个节点含 title, scoring_item_codes, children。\n"
                    "要求：1）一级章建议 4-7 个；2）每个评分项 code 至少出现在一个末级节点；"
                    "3）标题必须是正式标书目录风格，不要出现解释性语句；4）食材配送项目应突出食品安全、冷链仓储、配送履约、团队管理、应急保障、资质业绩；"
                    "5）不要生成正文内容，只生成目录标题。\n\n"
                    f"结构化解读：{_json(_compact_parse_for_prompt(parse_result))}\n\n"
                    f"招标文件摘录：\n{_clip_for_llm(source_text, 22000)}"
                ),
            },
        ], scene="tender_parse",
        temperature=0.15,
        max_tokens=3600,
        fallback=_json({"nodes": fallback.get("nodes", [])}),
        json_mode=True,
    )
    parsed = _json_from_llm_content(llm_result.content)
    raw_nodes = parsed.get("nodes") if isinstance(parsed, dict) else None
    normalized = _normalize_nodes(raw_nodes or []) if isinstance(raw_nodes, list) else []
    if not normalized:
        fallback["outline_method"] = "biaoshu_skill_rules"
        fallback["llm"] = _llm_meta(llm_result)
        return fallback

    covered = {code for node in _flatten_nodes(normalized) for code in node.get("scoring_item_codes", [])}
    missing = [str(item.get("code")) for item in scoring if item.get("code") and item.get("code") not in covered]
    if missing:
        normalized = _attach_missing_scoring_items(normalized, scoring, missing)
    covered = {code for node in _flatten_nodes(normalized) for code in node.get("scoring_item_codes", [])}
    uncovered = [item for item in scoring if item.get("code") not in covered]
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO p1_outlines (project_id, nodes_json, uncovered_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET nodes_json = excluded.nodes_json, uncovered_json = excluded.uncovered_json, updated_at = excluded.updated_at
            """,
            (project_id, _json(normalized), _json(uncovered), now),
        )
        conn.execute("UPDATE p1_projects SET status = 'drafting', updated_at = ? WHERE id = ?", (now, project_id))
        conn.commit()
    return {
        "project_id": project_id,
        "nodes": normalized,
        "uncovered_scoring_items": uncovered,
        "outline_method": "biaoshu_skill_qwen_outline" if llm_result.usedApi else "biaoshu_skill_rules",
        "llm": _llm_meta(llm_result),
    }


def _llm_meta(llm_result: Any | None) -> dict[str, Any]:
    """Return a compact, frontend-safe record of the LLM enhancement status."""

    configured = bool(settings.qwen_api_key.strip())
    model = qwen_client.selected_model("tender_parse")
    if llm_result is None:
        return {
            "provider": "qwen",
            "model": model,
            "enabled": bool(settings.enable_llm_parse_optimize),
            "configured": configured,
            "usedApi": False,
            "error": None if configured else "QWEN_API_KEY is not configured",
        }
    return {
        "provider": getattr(llm_result, "provider", "qwen"),
        "model": getattr(llm_result, "model", model),
        "enabled": bool(settings.enable_llm_parse_optimize),
        "configured": configured,
        "usedApi": bool(getattr(llm_result, "usedApi", False)),
        "error": getattr(llm_result, "error", None),
    }


def _clip_for_llm(text: str, limit: int = 32000) -> str:
    """Keep the most useful tender text for Qwen without losing scoring tables."""

    source = re.sub(r"\r\n?", "\n", text or "").strip()
    if len(source) <= limit:
        return source
    lines = _clean_lines(source)
    priority_terms = [
        "评分",
        "评审",
        "分值",
        "得分",
        "资格",
        "资质",
        "必须",
        "须提供",
        "废标",
        "无效投标",
        "否决",
        "技术要求",
        "商务要求",
        "服务要求",
        "食品安全",
        "冷链",
        "配送",
    ]
    priority: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if any(term in line for term in priority_terms):
            key = re.sub(r"\s+", "", line)[:120]
            if key not in seen:
                seen.add(key)
                priority.append(line)
        if len("\n".join(priority)) > int(limit * 0.42):
            break
    head = source[: int(limit * 0.34)]
    tail = source[-int(limit * 0.18) :]
    compact = "\n".join(
        part
        for part in [
            "【招标文件开头摘录】\n" + head,
            "【评分/资格/废标关键摘录】\n" + "\n".join(priority),
            "【招标文件结尾摘录】\n" + tail,
        ]
        if part.strip()
    )
    return compact[:limit]


def _compact_parse_for_prompt(parse_result: dict[str, Any]) -> dict[str, Any]:
    """Shrink local extraction into a JSON block suitable for prompt context."""

    def trim_items(items: Any, fields: list[str], limit: int) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in (items or [])[:limit]:
            if not isinstance(item, dict):
                continue
            next_item: dict[str, Any] = {}
            for field in fields:
                value = item.get(field)
                if value not in (None, "", []):
                    next_item[field] = value
            anchor = item.get("page_anchor")
            if isinstance(anchor, dict):
                next_item["page_anchor"] = {"page": anchor.get("page") or 1, "text": str(anchor.get("text") or "")[:120]}
            if next_item:
                output.append(next_item)
        return output

    return {
        "summary": str(parse_result.get("summary") or "")[:500],
        "industry": parse_result.get("industry") or "",
        "project_name": parse_result.get("project_name") or "",
        "qualification_requirements": trim_items(parse_result.get("qualification_requirements"), ["category", "description", "gap_note"], 24),
        "scoring_items": trim_items(parse_result.get("scoring_items"), ["code", "name", "score", "criteria"], 40),
        "disqualification_clauses": trim_items(parse_result.get("disqualification_clauses"), ["description", "avoidance_advice"], 18),
        "policy_hints": trim_items(parse_result.get("policy_hints"), ["description", "source_ref"], 12),
        "source_preview": str(parse_result.get("source_preview") or "")[:800],
    }


def _json_from_llm_content(content: Any) -> dict[str, Any]:
    """Parse JSON returned by Qwen, tolerating code fences and extra whitespace."""

    if isinstance(content, dict):
        return content
    raw = str(content or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _merge_ai_parse_result(base: dict[str, Any], parsed: dict[str, Any], file_name: str) -> dict[str, Any]:
    """Merge Qwen refined tender analysis while preserving local fallbacks."""

    merged = dict(base)
    if isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
        merged["summary"] = parsed["summary"].strip()[:900]
    for key in ["industry", "project_name"]:
        value = str(parsed.get(key) or "").strip()
        if value:
            merged[key] = value[:80]
    if isinstance(parsed.get("outline_strategy_notes"), list):
        merged["outline_strategy_notes"] = [str(item).strip()[:220] for item in parsed["outline_strategy_notes"] if str(item).strip()][:8]

    scoring = _normalize_ai_scoring(parsed.get("scoring_items"), base.get("scoring_items") or [])
    qualifications = _normalize_ai_qualifications(parsed.get("qualification_requirements"), base.get("qualification_requirements") or [])
    disquals = _normalize_ai_disqualification(parsed.get("disqualification_clauses"), base.get("disqualification_clauses") or [])
    policies = _normalize_ai_policy(parsed.get("policy_hints"), base.get("policy_hints") or [], file_name)
    if scoring:
        merged["scoring_items"] = scoring
    if qualifications:
        merged["qualification_requirements"] = qualifications
    if disquals:
        merged["disqualification_clauses"] = disquals
    if policies:
        merged["policy_hints"] = policies
    warnings = list(merged.get("warnings") or [])
    if not parsed:
        warnings.append("千问智能解读未返回结构化结果，已使用本地规则。")
    merged["warnings"] = list(dict.fromkeys(warnings))
    return merged


def _normalize_ai_scoring(ai_items: Any, fallback_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ai_items if isinstance(ai_items, list) else []:
        if not isinstance(raw, dict):
            continue
        name = _clean_scoring_title(str(raw.get("name") or raw.get("title") or raw.get("criteria") or "评分项"))[:80]
        criteria = str(raw.get("criteria") or raw.get("description") or name).strip()[:800]
        score = _as_float(raw.get("score"))
        if score is None or score <= 0:
            continue
        key = f"{name}-{score}"
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "id": _new_id("score"),
                "code": f"S{len(items) + 1}",
                "name": name[-32:] or "评分项",
                "score": score,
                "criteria": criteria,
                "page_anchor": _normalize_page_anchor(raw.get("page_anchor"), criteria),
            }
        )
        if len(items) >= 40:
            break
    for fallback in fallback_items:
        score = _as_float(fallback.get("score"))
        name = str(fallback.get("name") or "评分项").strip()
        key = f"{name}-{score}"
        if score is None or key in seen or len(items) >= 40:
            continue
        next_item = dict(fallback)
        next_item["id"] = str(next_item.get("id") or _new_id("score"))
        next_item["code"] = f"S{len(items) + 1}"
        items.append(next_item)
        seen.add(key)
    return items or fallback_items


def _normalize_ai_qualifications(ai_items: Any, fallback_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ai_items if isinstance(ai_items, list) else []:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or raw.get("requirement") or "").strip()
        if len(description) < 4:
            continue
        key = re.sub(r"\s+", "", description)[:90]
        if key in seen:
            continue
        seen.add(key)
        category = str(raw.get("category") or "资质要求").strip()[:30]
        items.append(
            {
                "id": _new_id("qual"),
                "description": description[:900],
                "category": category or "资质要求",
                "page_anchor": _normalize_page_anchor(raw.get("page_anchor"), description),
                "match_result": str(raw.get("match_result") or "need_supplement"),
                "gap_note": str(raw.get("gap_note") or "待与企业知识库比对。")[:260],
                "matched_knowledge_ids": [],
            }
        )
        if len(items) >= 40:
            break
    return items or fallback_items


def _normalize_ai_disqualification(ai_items: Any, fallback_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ai_items if isinstance(ai_items, list) else []:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or raw.get("clause") or "").strip()
        if len(description) < 4:
            continue
        key = re.sub(r"\s+", "", description)[:90]
        if key in seen:
            continue
        seen.add(key)
        advice = str(raw.get("avoidance_advice") or "在目录和正文中逐项响应，并在提交前复核。")
        items.append(
            {
                "id": _new_id("risk"),
                "description": description[:900],
                "page_anchor": _normalize_page_anchor(raw.get("page_anchor"), description),
                "avoidance_advice": advice[:320],
                "avoidance_status": "open",
            }
        )
        if len(items) >= 30:
            break
    return items or fallback_items


def _normalize_ai_policy(ai_items: Any, fallback_items: list[dict[str, Any]], file_name: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ai_items if isinstance(ai_items, list) else []:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or raw.get("policy") or "").strip()
        if len(description) < 4:
            continue
        key = re.sub(r"\s+", "", description)[:90]
        if key in seen:
            continue
        seen.add(key)
        source_ref = str(raw.get("source_ref") or f"{file_name}｜1页")[:120]
        items.append({"id": _new_id("policy"), "description": description[:700], "source_ref": source_ref})
        if len(items) >= 18:
            break
    return items or fallback_items


def _normalize_page_anchor(value: Any, fallback_text: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        page = _as_int(value.get("page")) or 1
        text = str(value.get("text") or fallback_text or "").strip()[:220]
        return {"page": max(1, page), "text": text}
    return {"page": 1, "text": str(fallback_text or "")[:220]}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("分", "").replace("%", "").strip()
        return float(text)
    except (TypeError, ValueError):
        return None


def _attach_missing_scoring_items(nodes: list[dict[str, Any]], scoring: list[dict[str, Any]], missing: list[str]) -> list[dict[str, Any]]:
    """Ensure every scoring code has at least one editable outline node."""

    if not missing:
        return nodes
    normalized = _normalize_nodes(nodes)
    target = next((node for node in normalized if any(term in node.get("title", "") for term in ["技术", "服务", "方案"])), None)
    if target is None:
        target = {"id": _new_id("node"), "level": 1, "title": "评分点补充响应", "scoring_item_codes": [], "gen_status": "pending", "children": []}
        normalized.append(target)
    scoring_by_code = {str(item.get("code")): item for item in scoring}
    existing_titles = {node.get("title") for node in _flatten_nodes(normalized)}
    for code in missing:
        item = scoring_by_code.get(str(code))
        if not item:
            continue
        name = str(item.get("name") or item.get("criteria") or code).strip()[:36]
        title = f"{name}响应方案"
        if title in existing_titles:
            continue
        target.setdefault("children", []).append(
            {
                "id": _new_id("node"),
                "level": int(target.get("level") or 1) + 1,
                "title": title,
                "scoring_item_codes": [str(code)],
                "gen_status": "pending",
                "children": [],
            }
        )
        existing_titles.add(title)
    return _normalize_nodes(normalized)


def _ensure_project(project_id: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM p1_projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40401, "项目不存在", 404)


def _latest_doc(project_id: str) -> sqlite3.Row:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_tender_documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
    if row is None:
        raise P1ServiceError(40002, "请先上传并解读招标文件")
    return row


def _get_outline(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_outlines WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        return generate_outline(project_id)
    return {"project_id": project_id, "nodes": _parse_json(row["nodes_json"], []), "uncovered_scoring_items": _parse_json(row["uncovered_json"], [])}


def _record_job(status: str, label: str, result: dict[str, Any]) -> str:
    job_id = _new_id("job")
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO p1_jobs (id, status, progress, label, result_json, created_at, updated_at) VALUES (?, ?, 100, ?, ?, ?, ?)",
            (job_id, status, label, _json(result), now, now),
        )
        conn.commit()
    return job_id


def _extract_upload_text(path: Path, file_name: str) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return {"text": path.read_text(encoding="utf-8", errors="ignore")[:120000], "is_ocr": False}
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    if suffix == ".docx":
        return {"text": _extract_docx_text(path), "is_ocr": False}
    if suffix == ".zip":
        return _extract_zip_text(path)
    content_type = mimetypes.guess_type(file_name)[0] or ""
    if suffix in IMAGE_EXTENSIONS or content_type.startswith("image/"):
        return {"text": f"图片扫描件：{file_name}\n系统已保留原文件，后续可接入 OCR 识别。", "is_ocr": True}
    return {"text": f"资料已上传：{file_name}\n当前格式仅保留文件，建议转为 PDF/DOCX 后解析。", "is_ocr": suffix == ".doc"}


def _extract_pdf_text(path: Path) -> dict[str, Any]:
    parts: list[str] = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            if text:
                parts.append(f"[第{page_index}页] {text}")
            if sum(len(part) for part in parts) > 120000:
                break
    return {"text": "\n".join(parts)[:120000], "is_ocr": not bool(parts)}


def _extract_docx_text(path: Path) -> str:
    doc = Document(str(path))
    parts = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
    for table in doc.tables[:40]:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)[:120000]


def _extract_zip_text(path: Path) -> dict[str, Any]:
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist()[:30]:
            suffix = Path(item.filename).suffix.lower()
            if suffix not in TEXT_EXTENSIONS:
                continue
            with archive.open(item) as fh:
                parts.append(f"--- {item.filename} ---\n" + fh.read(120000).decode("utf-8", errors="ignore"))
    text = "\n\n".join(parts)
    return {"text": text or "压缩包已上传，未发现可直接抽取的文本文件。", "is_ocr": False}


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) >= 3:
            lines.append(line[:600])
    return lines[:3000]


def _line_anchor(line: str, fallback_index: int) -> dict[str, Any]:
    match = re.search(r"第\s*(\d+)\s*页", line)
    return {"page": int(match.group(1)) if match else max(1, fallback_index // 45 + 1), "text": line[:220]}


def _extract_qualification_requirements(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()
    for index, line in enumerate(lines):
        if not any(term in line for term in QUALIFICATION_TERMS):
            continue
        if not any(flag in line for flag in ["资格", "资质", "须", "必须", "提供", "具备", "要求", "证明"]):
            continue
        normalized = re.sub(r"[，。；;,. ]", "", line)[:80]
        if normalized in seen:
            continue
        seen.add(normalized)
        category = next((label for term, label in QUALIFICATION_TERMS.items() if term in line), "资质要求")
        items.append(
            {
                "id": _new_id("qual"),
                "description": line,
                "category": category,
                "page_anchor": _line_anchor(line, index),
                "match_result": "need_supplement",
                "gap_note": "待与企业知识库比对。",
                "matched_knowledge_ids": [],
            }
        )
        if len(items) >= 30:
            break
    if not items:
        items.append(
            {
                "id": _new_id("qual"),
                "description": "请人工确认招标文件中的营业执照、食品经营许可、体系认证、业绩等资格要求。",
                "category": "待确认资质",
                "page_anchor": {"page": 1, "text": "未自动识别到明确资格条款"},
                "match_result": "need_supplement",
                "gap_note": "未自动识别到明确资格条款。",
                "matched_knowledge_ids": [],
            }
        )
    return items


def _extract_scoring_items(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(title: str, score: float, criteria: str, line_index: int) -> None:
        if score <= 0 or score > 100 or len(items) >= 40:
            return
        cleaned_title = _clean_scoring_title(title)
        if not cleaned_title:
            return
        key = f"{cleaned_title}-{score}-{criteria[:80]}"
        if key in seen:
            return
        seen.add(key)
        code = f"S{len(items) + 1}"
        items.append(
            {
                "id": _new_id("score"),
                "code": code,
                "name": cleaned_title[-32:],
                "score": score,
                "criteria": criteria,
                "page_anchor": _line_anchor(criteria, line_index),
            }
        )

    try:
        from app.utils.bid_extractor.scoring_extractor import extract_scoring

        extracted = extract_scoring("\n".join(lines))
        for index, item in enumerate(extracted.get("items") or []):
            score = item.get("score")
            if score is None:
                continue
            add_item(str(item.get("title") or item.get("name") or "评分项"), float(score), str(item.get("text") or item.get("criteria") or ""), index)
    except Exception:
        pass

    line_score_pattern = re.compile(r"(?P<title>[\u4e00-\u9fa5A-Za-z0-9（）()《》、.．·\s:：-]{2,80}?)(?P<score>\d+(?:\.\d+)?)\s*分")
    keyword_terms = ["评分", "得分", "分值", "评审", "方案", "服务", "配送", "质量", "资质", "安全", "冷链", "团队", "业绩"]
    for index, line in enumerate(lines):
        if "分" not in line or not any(term in line for term in keyword_terms):
            continue
        for match in line_score_pattern.finditer(line):
            add_item(match.group("title"), float(match.group("score")), line, index)
        if len(items) >= 40:
            break
    if not items:
        items = [
            {"id": _new_id("score"), "code": code, "name": name, "score": score, "criteria": criteria, "page_anchor": {"page": 1, "text": "未识别到结构化评分表，采用食材配送常用技术评分框架作为初始大纲。"}}
            for code, name, score, criteria in DEFAULT_SCORING
        ]
    return items


def _clean_scoring_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    cleaned = re.split(r"[。；;]\s*", cleaned)[-1]
    if "：" in cleaned or ":" in cleaned:
        prefix, suffix = re.split(r"[：:]", cleaned, maxsplit=1)
        if any(term in prefix for term in ["评分", "评审", "分值", "标准"]):
            cleaned = suffix
    cleaned = re.sub(r"^(评分标准|评分办法|评分细则|评审因素|评审项目|分值|得分)[：:、\s]*", "", cleaned)
    cleaned = re.sub(r"^[\d\.、)）\s]+", "", cleaned).strip(" ：:|，,、")
    cleaned = re.sub(r"[（(]?\d+(?:\.\d+)?\s*分[）)]?", "", cleaned).strip(" ：:|，,、")
    return cleaned[:80] or "评分项"


def _extract_disqualification_clauses(lines: list[str]) -> list[dict[str, Any]]:
    items = []
    seen = set()
    for index, line in enumerate(lines):
        if not any(term in line for term in ["废标", "无效投标", "否决", "不予受理", "取消资格", "投标无效"]):
            continue
        normalized = re.sub(r"[，。；;,. ]", "", line)[:90]
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            {
                "id": _new_id("risk"),
                "description": line,
                "page_anchor": _line_anchor(line, index),
                "avoidance_advice": "在目录和正文中逐项响应，并在签章、报价、资质附件处设置提交前复核。",
                "avoidance_status": "open",
            }
        )
        if len(items) >= 30:
            break
    return items


def _extract_policy_hints(lines: list[str], file_name: str) -> list[dict[str, Any]]:
    items = []
    for index, line in enumerate(lines):
        if any(term in line for term in ["中小企业", "政府采购", "节能", "环保", "食品安全", "农产品", "冷链"]):
            items.append({"id": _new_id("policy"), "description": line, "source_ref": f"{file_name}｜{_line_anchor(line, index)['page']}页"})
        if len(items) >= 12:
            break
    return items


def _summarize_tender(lines: list[str], qualifications: list[dict[str, Any]], scoring: list[dict[str, Any]], disquals: list[dict[str, Any]]) -> str:
    first = " ".join(lines[:8])[:260]
    score_total = sum(float(item.get("score") or 0) for item in scoring)
    return f"已识别资格要求 {len(qualifications)} 条、评分项 {len(scoring)} 条（合计约 {score_total:g} 分）、废标/否决风险 {len(disquals)} 条。{first}"


def _build_outline_nodes(scoring: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = [
        {
            "id": _new_id("node"),
            "level": 1,
            "title": "一、投标响应与资格证明",
            "scoring_item_codes": [],
            "gen_status": "pending",
            "children": [
                {"id": _new_id("node"), "level": 2, "title": "投标函及法定代表人授权", "scoring_item_codes": [], "gen_status": "pending", "children": []},
                {"id": _new_id("node"), "level": 2, "title": "企业资质证书与履约能力", "scoring_item_codes": [item["code"] for item in scoring if "资质" in item.get("name", "") or "案例" in item.get("name", "")], "gen_status": "pending", "children": []},
            ],
        },
        {"id": _new_id("node"), "level": 1, "title": "二、技术服务方案", "scoring_item_codes": [], "gen_status": "pending", "children": []},
        {"id": _new_id("node"), "level": 1, "title": "三、质量安全与应急保障", "scoring_item_codes": [], "gen_status": "pending", "children": []},
    ]
    service_parent = nodes[1]
    safety_parent = nodes[2]
    for item in scoring:
        name = str(item.get("name") or item.get("criteria") or "评分响应")
        parent = safety_parent if any(term in name for term in ["安全", "质量", "应急", "检测", "冷链"]) else service_parent
        parent["children"].append({"id": _new_id("node"), "level": 2, "title": f"{name}响应方案", "scoring_item_codes": [item["code"]], "gen_status": "pending", "children": []})
    nodes.append({"id": _new_id("node"), "level": 1, "title": "四、商务承诺与附件", "scoring_item_codes": [], "gen_status": "pending", "children": [{"id": _new_id("node"), "level": 2, "title": "偏离表、承诺函与附件清单", "scoring_item_codes": [], "gen_status": "pending", "children": []}]})
    return nodes


def _normalize_nodes(nodes: list[dict[str, Any]], level: int = 1) -> list[dict[str, Any]]:
    normalized = []
    for node in nodes:
        children = _normalize_nodes(node.get("children") or [], level + 1)
        normalized.append(
            {
                "id": str(node.get("id") or _new_id("node")),
                "level": int(node.get("level") or level),
                "title": str(node.get("title") or "未命名章节").strip(),
                "scoring_item_codes": [str(code) for code in (node.get("scoring_item_codes") or []) if str(code).strip()],
                "gen_status": str(node.get("gen_status") or "pending"),
                "children": children,
            }
        )
    return normalized


def _flatten_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for node in nodes:
        flat.append(node)
        flat.extend(_flatten_nodes(node.get("children") or []))
    return flat


def _find_outline_node(project_id: str, node_id: str) -> dict[str, Any]:
    outline = _get_outline(project_id)
    for node in _flatten_nodes(outline["nodes"]):
        if node["id"] == node_id:
            return node
    raise P1ServiceError(40421, "大纲节点不存在", 404)


def _find_section_by_node(project_id: str, node_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM p1_proposal_sections WHERE project_id = ? AND node_id = ?", (project_id, node_id)).fetchone()
    return _section_from_row(row) if row else None


def _get_or_create_section(project_id: str, node: dict[str, Any]) -> dict[str, Any]:
    section = _find_section_by_node(project_id, node["id"])
    if section:
        return section
    return _upsert_section(project_id, node, "", [])


def _upsert_section(project_id: str, node: dict[str, Any], body: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    section_id = _new_id("sec")
    now = _now()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM p1_proposal_sections WHERE project_id = ? AND node_id = ?", (project_id, node["id"])).fetchone()
        if existing:
            section_id = existing["id"]
            conn.execute(
                "UPDATE p1_proposal_sections SET title = ?, body = ?, source_refs_json = ?, status = 'generated', updated_at = ? WHERE id = ?",
                (node["title"], body, _json(refs), now, section_id),
            )
        else:
            conn.execute(
                "INSERT INTO p1_proposal_sections (id, project_id, node_id, title, body, source_refs_json, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'generated', ?)",
                (section_id, project_id, node["id"], node["title"], body, _json(refs), now),
            )
        conn.commit()
    return get_section(section_id)


def _render_section_body(project_id: str, node: dict[str, Any]) -> str:
    project = get_project(project_id)
    title = node.get("title") or "章节"
    codes = node.get("scoring_item_codes") or []
    query = " ".join([title, " ".join(codes), project.get("industry") or "", "食材配送 冷链 食品安全"])
    refs = search_knowledge(q=query, include_expired=False, limit=3)["items"]
    source_line = ""
    if refs:
        source_line = "\n".join(f"【知识库来源：{item['source_name']}｜条目ID：{item['id']}】" for item in refs[:2])
    score_note = f"本节响应评分项：{', '.join(codes)}。" if codes else "本节用于补齐招标响应和附件说明。"
    paragraphs = [
        score_note,
        f"围绕“{title}”，我方将以食品安全、稳定供应、准时配送和过程可追溯为核心组织投标响应，确保方案内容与采购需求逐项对应。",
        "执行层面建立从供应商准入、采购验收、仓储温控、分拣复核、车辆配送到售后反馈的闭环机制，每个环节明确责任人、记录表单和异常升级路径。",
        "质量控制采用日常巡检、批次留样、农残快检、温度记录和客户满意度回访相结合的方式，确保食材来源真实、过程可查、风险可控。",
        "| 对比维度 | 招标要求 | 我方响应 | 佐证材料 |\n| --- | --- | --- | --- |\n| 食品安全 | 全流程管控 | 建立验收、检测、留样、追溯制度 | 资质证书/检测记录 |\n| 配送时效 | 按时送达 | 冷链车辆与备用线路保障 | 车辆/仓储图片 |\n| 服务响应 | 快速处理投诉 | 项目经理负责制与应急预案 | 服务案例 |",
    ]
    if source_line:
        paragraphs.insert(1, source_line)
    return "\n\n".join(paragraphs)


def _section_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "project_id": data["project_id"],
        "node_id": data["node_id"],
        "title": data["title"],
        "body": data["body"],
        "source_refs": _parse_json(data.get("source_refs_json"), []),
        "status": data["status"],
        "updated_at": data["updated_at"],
    }


def _build_review_findings(project_id: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    doc = None
    try:
        doc = _latest_doc(project_id)
    except P1ServiceError:
        findings.append(_finding("资质", "fatal", "尚未上传或解读招标文件", "项目入口", "无招标原文", "先上传招标文件并完成解读。"))
    parse_result = _parse_json(doc["parse_result_json"], {}) if doc else {}
    match_result = qualification_match(doc["id"]) if doc else {"matches": []}
    for item in match_result["matches"]:
        if item.get("match_result") == "unsatisfied":
            findings.append(_finding("资质", "fatal", f"缺失必需资质：{item.get('category')}", f"招标文件第{item.get('page_anchor', {}).get('page', 1)}页", item.get("description", ""), item.get("gap_note") or "补充对应证书后重新审核。"))
    outline = _get_outline(project_id) if doc else {"nodes": [], "uncovered_scoring_items": []}
    for item in outline.get("uncovered_scoring_items", []):
        findings.append(_finding("评分覆盖", "important", f"评分点未响应：{item.get('code')} {item.get('name')}", "大纲", item.get("criteria", ""), "在技术服务方案中新增对应章节或绑定到现有章节。"))
    sections = list_sections(project_id)["items"] if doc else []
    section_text = "\n".join(section["body"] for section in sections)
    if not sections:
        findings.append(_finding("评分覆盖", "important", "尚未生成标书正文", "正文", "无正文内容", "生成标书正文后再执行提交前审核。"))
    for clause in parse_result.get("disqualification_clauses", [])[:8]:
        keyword = _first_keyword(clause.get("description", ""))
        if keyword and keyword not in section_text:
            findings.append(_finding("废标", "important", f"废标条款未见规避说明：{keyword}", f"招标文件第{clause.get('page_anchor', {}).get('page', 1)}页", clause.get("description", ""), "在响应章节或投标文件检查清单中补充规避措施。"))
    if sections and not any(term in section_text for term in ["签字", "盖章", "密封", "页码", "目录"]):
        findings.append(_finding("格式", "hint", "格式要件说明不足", "商务承诺与附件", "未见签字盖章/密封/页码目录等提交检查说明", "在附件清单中补充签章、页码、密封、目录更新的提交前复核表。"))
    return findings


def _finding(category: str, severity: str, description: str, location: str, evidence: str, suggestion: str) -> dict[str, Any]:
    return {"id": _new_id("finding"), "category": category, "severity": severity, "description": description, "location": location, "evidence": evidence, "suggestion": suggestion, "status": "open"}


def _finding_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {key: data[key] for key in ["id", "project_id", "category", "severity", "description", "location", "evidence", "suggestion", "status", "created_at", "updated_at"]}


def _finding_summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(findings),
        "fatal": sum(1 for item in findings if item["severity"] == "fatal"),
        "important": sum(1 for item in findings if item["severity"] == "important"),
        "hint": sum(1 for item in findings if item["severity"] == "hint"),
    }


def _knowledge_from_row(row: sqlite3.Row) -> dict[str, Any]:
    content_text = row["content_text"] or ""
    return {
        "id": row["id"],
        "title": row["title"],
        "type": row["item_type"],
        "industry": row["industry"],
        "region": row["region"],
        "year": row["year"],
        "project_type": row["project_type"],
        "tags": _parse_json(row["tags_json"], []),
        "valid_until": row["valid_until"],
        "status": row["status"],
        "source_name": row["source_name"],
        "file_path": row["file_path"],
        "file_size": row["file_size"],
        "content_preview": content_text[:420],
        "content_text": content_text,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _best_knowledge_match(tokens: list[str], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for item in items:
        haystack = " ".join(str(part or "") for part in [item.get("title"), item.get("content_preview"), " ".join(item.get("tags", []))])
        score = sum(1 for token in tokens if token and token in haystack)
        if best is None or score > best["score"]:
            best = {"score": score, "item": item}
    return best


def _important_terms(text: str) -> list[str]:
    terms = [term for term in QUALIFICATION_TERMS if term in text]
    if not terms:
        terms = [token for token in _query_tokens(text) if len(token) >= 2][:5]
    return terms


def _query_tokens(q: str) -> list[str]:
    return [token for token in re.split(r"[,，;；\s]+", (q or "").lower().strip()) if token]


def _score(tokens: list[str], haystack: str) -> float:
    if not tokens:
        return 0.01
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1 + min(0.5, haystack.count(token) * 0.05)
        elif len(token) > 2:
            chars = [char for char in token if char.strip()]
            score += sum(1 for char in chars if char in haystack) / max(len(chars) * 2, 8)
    return score


def _parse_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw = re.split(r"[,，;；\s]+", text)
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))[:30]


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _match_summary(matches: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(matches),
        "satisfied": sum(1 for item in matches if item.get("match_result") == "satisfied"),
        "unsatisfied": sum(1 for item in matches if item.get("match_result") == "unsatisfied"),
        "need_supplement": sum(1 for item in matches if item.get("match_result") == "need_supplement"),
    }


def _first_keyword(text: str) -> str:
    for term in ["签字", "盖章", "保证金", "报价", "资质", "密封", "副本", "正本", "授权"]:
        if term in text:
            return term
    return text[:12]


def _local_path(path: Path) -> str:
    try:
        return str(path.relative_to(settings.backend_root))
    except ValueError:
        return str(path)


def _safe_filename(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", value).strip("_")[:80]


def _add_markdown_table(doc: Document, block: str) -> None:
    rows = []
    for line in block.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            rows.append(cells)
    if len(rows) < 2:
        doc.add_paragraph(block)
        return
    table = doc.add_table(rows=len(rows), cols=max(len(row) for row in rows))
    table.style = "Table Grid"
    for r_index, row in enumerate(rows):
        for c_index, cell in enumerate(row):
            table.cell(r_index, c_index).text = cell
