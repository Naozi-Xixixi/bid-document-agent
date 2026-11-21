"""Repair historical source-page metadata without deleting chunks or running AI."""
from __future__ import annotations

import re

from app.services import storage


def plan(item_id: int) -> dict:
    from . import service as s
    s.ensure_schema()
    with storage.connect() as conn:
        item = conn.execute(
            "SELECT file_type,content_text,status FROM knowledge_item WHERE item_id=?", (item_id,),
        ).fetchone()
        if not item:
            raise s.KnowledgeModuleError(40401, "资料不存在", 404)
        if item["status"] != "completed":
            raise s.KnowledgeModuleError(40901, "请先完成基础解析", 409)
        rows = conn.execute(
            "SELECT chunk_id,content,page_number FROM knowledge_chunk WHERE item_id=? AND chunk_index<100000 ORDER BY chunk_index",
            (item_id,),
        ).fetchall()
    kind = str(item["file_type"]).lower().lstrip(".")
    changes, unresolved = [], 0
    if kind in {"doc", "docx"}:
        # Word layout is printer/font dependent; XML section numbers are not pages.
        changes = [{"chunk_id": r["chunk_id"], "before": r["page_number"], "after": None}
                   for r in rows if r["page_number"] is not None]
    elif kind == "pdf":
        pages = []
        current = None
        for line in str(item["content_text"] or "").splitlines():
            match = re.match(r"^【第(\d+)页】", line.strip())
            if match:
                current = int(match.group(1))
                pages.append((current, []))
                line = line.strip()[match.end():]
            if current is not None and line.strip() and not s._looks_like_heading(line.strip()):
                pages[-1][1].append(re.sub(r"\s+", "", line))
        combined, spans = "", []
        for number, parts in pages:
            body = "".join(parts)
            spans.append((len(combined), len(combined) + len(body), number))
            combined += body
        if not pages:
            return {"item_id": item_id, "changes": [], "unresolved": len(rows),
                    "message": "历史文本缺少 PDF 页标记，需要从原始 PDF 重新解析才能确定页码"}
        for row in rows:
            needle = re.sub(r"\s+", "", str(row["content"] or ""))
            start = combined.find(needle) if needle else -1
            unique = start >= 0 and combined.find(needle, start + 1) < 0
            actual = next((number for lo, hi, number in spans if lo <= start < hi), None) if unique else None
            if actual is None:
                unresolved += 1
            if actual != row["page_number"]:
                changes.append({"chunk_id": row["chunk_id"], "before": row["page_number"], "after": actual})
    return {"item_id": item_id, "changes": changes, "unresolved": unresolved,
            "message": "页码指向片段起始页；无法唯一定位的片段不显示推测页码"}


def apply(item_id: int) -> dict:
    """Optimistic update preserves concurrent edits; vector sync is durable."""
    from . import service as s
    result = plan(item_id)
    applied = 0
    with storage.connect() as conn:
        for change in result["changes"]:
            cursor = conn.execute(
                "UPDATE knowledge_chunk SET page_number=? WHERE item_id=? AND chunk_id=? "
                "AND (page_number=? OR (page_number IS NULL AND ? IS NULL))",
                (change["after"], item_id, change["chunk_id"], change["before"], change["before"]),
            )
            applied += cursor.rowcount
        if applied:
            conn.execute(
                "UPDATE knowledge_item SET vector_status='pending',vector_error=NULL,vector_updated_at=? WHERE item_id=?",
                (s._now(), item_id),
            )
        conn.commit()
    return {**result, "applied": applied}
