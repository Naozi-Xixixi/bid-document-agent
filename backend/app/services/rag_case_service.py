from __future__ import annotations

import json
import ast
import io
import logging
import math
import re
import sqlite3
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services import storage, vector_store
from app.services.qwen_client import chat_completion


logger = logging.getLogger(__name__)

# Page text is extracted during evidence lookup only once per process. This
# avoids reopening a 100+ page PDF for every scoring-point comparison.
_PDF_PAGE_TEXT_CACHE: dict[str, tuple[tuple[int, str], ...]] = {}

DEFAULT_CASE_FOLDER = settings.resolve_local_path(settings.data_dir) / "imports" / "rag_cases"
MAX_CHUNK_CHARS = 1600
MAX_FRAGMENT_CHARS = 6200

CASE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("yaohai_state_capital", ("示例区", "国有资本")),
    ("wenlv_bolan", ("文旅", "博览")),
    ("fangmiao_street", ("示例街道",)),
    ("huainan_mining", ("示例", "示例煤矿")),
    ("tonghang_group", ("通航",)),
)

CASE_NAMES = {
    "yaohai_state_capital": "示例市示例区国有资本运营控股集团有限公司食堂食材配送",
    "wenlv_bolan": "示例文旅博览集团有限公司食堂食品原材料采购",
    "fangmiao_street": "示例街道2024年食堂食材采购",
    "huainan_mining": "示例矿业集团煤矿食堂食材及杂品配送供应商项目",
    "tonghang_group": "示例通航控股集团有限公司职工餐厅食材供应商采购",
}

SCORING_KEYWORDS = (
    "评分", "评审", "分值", "满分", "技术分", "商务分", "综合评分", "评分标准", "评分办法", "详细评审",
)
QUALIFICATION_KEYWORDS = ("资格", "资质", "许可证", "营业执照", "供应商资格", "投标人资格", "资格审查")
INVALID_KEYWORDS = ("无效", "废标", "否决", "响应无效", "投标无效", "重大偏差")
CATALOG_KEYWORDS = ("目录", "资料清单", "响应文件资料清单", "投标文件资料清单")
COMPANY_KEYWORDS = (
    "示例配送", "公司简介", "企业简介", "投标人情况", "供应商情况", "综合简介", "注册资本",
    "经营范围", "配送能力", "服务能力", "团队", "人员", "业绩", "荣誉", "认证", "资质", "仓储", "物流",
)
PROJECT_TYPE_KEYWORDS = ("食堂", "食材", "原材料", "配送", "餐厅", "农产品")


class RagCaseServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(slots=True)
class LocalPdfItem:
    path: Path
    case_key: str
    case_name: str
    doc_role: str


def _now_text() -> str:
    return storage.now_utc().isoformat()


def _default_folder() -> Path:
    return DEFAULT_CASE_FOLDER


def _safe_folder(folder_path: str | None = None) -> Path:
    folder = Path(folder_path).expanduser() if folder_path else _default_folder()
    if not folder.is_absolute():
        folder = (settings.backend_root.parent / folder).resolve()
    if not folder.exists() or not folder.is_dir():
        raise RagCaseServiceError(f"历史案例文件夹不存在：{folder}", 404)
    return folder


def _case_key_for_name(name: str) -> str:
    for key, keywords in CASE_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return key
    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", Path(name).stem).strip("_")
    return compact[:80] or "unknown_case"


def _role_for_name(name: str) -> str:
    stem = Path(name).stem
    if any(keyword in stem for keyword in ("招标文件", "磋商文件", "竞争性磋商文件")) and not any(
        keyword in stem for keyword in ("投标", "响应", "制作文件")
    ):
        return "tender"
    return "proposal"


def scan_case_folder(folder_path: str | None = None) -> dict[str, Any]:
    folder = _safe_folder(folder_path)
    items: list[LocalPdfItem] = []
    for path in sorted(folder.glob("*.pdf")):
        key = _case_key_for_name(path.name)
        items.append(
            LocalPdfItem(
                path=path,
                case_key=key,
                case_name=CASE_NAMES.get(key, Path(path).stem),
                doc_role=_role_for_name(path.name),
            )
        )

    cases: dict[str, dict[str, Any]] = {}
    for item in items:
        case = cases.setdefault(
            item.case_key,
            {"case_key": item.case_key, "case_name": item.case_name, "tender_files": [], "proposal_files": []},
        )
        bucket = "tender_files" if item.doc_role == "tender" else "proposal_files"
        case[bucket].append({"file_name": item.path.name, "file_path": str(item.path), "file_size": item.path.stat().st_size})

    return {
        "folder": str(folder),
        "pdf_count": len(items),
        "case_count": len(cases),
        "cases": list(cases.values()),
    }


def _extract_pdf_text(path: Path) -> tuple[str, int, tuple[tuple[int, str], ...]]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RagCaseServiceError("缺少 pdfplumber，无法解析 PDF。请先安装项目依赖。", 500) from exc

    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        page_cache: list[tuple[int, str]] = []
        for page_index, page in enumerate(pdf.pages, 1):
            try:
                text = page.extract_text(x_tolerance=1.5, y_tolerance=3, layout=False) or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("RAG PDF page parse skipped file=%s page=%s error=%s", path.name, page_index, exc)
                text = ""
            if text.strip():
                cleaned_page = _clean_text(text)
                page_cache.append((page_index, cleaned_page))
                parts.append(f"\n\n--- 第{page_index}页 ---\n{cleaned_page}")
        _PDF_PAGE_TEXT_CACHE[str(path)] = tuple(page_cache)
    return _clean_text("\n".join(parts)), page_count, tuple(page_cache)


def _clean_text(text: str) -> str:
    value = (text or "").replace("\r", "\n")
    value = re.sub(r"[ \t\u3000]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _unwrap_text_value(value: object) -> str:
    """Turn parser/PDF artifacts into readable text for the case report."""
    if isinstance(value, dict):
        for key in ("content", "criteria", "requirement", "description", "text", "value"):
            if value.get(key):
                return _unwrap_text_value(value[key])
        return ""
    text = str(value or "").strip()
    # qualification_requirements can be persisted as a Python repr string.
    for _ in range(2):
        if not (text.startswith("{") and text.endswith("}")):
            break
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            break
        if parsed == text:
            break
        text = _unwrap_text_value(parsed)
    return text


def _display_text(value: object, limit: int = 1800) -> str:
    text = _clean_text(_unwrap_text_value(value))
    if not text:
        return ""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-—_ ]{3,}", line):
            continue
        if re.match(r"^---\s*第\d+页\s*---$", line):
            continue
        # PDF table-of-contents rows and page leaders are not response methods.
        if re.search(r"(?:\.{4,}|…{3,}|·{5,})\s*\d{1,4}\s*$", line):
            continue
        if re.search(r"(?:汇集四方绿色|配送万家新鲜|第\s*\d+\s*页)\s*\d{1,4}\s*$", line):
            continue
        if re.fullmatch(r"P?\s*\d{1,4}(?:\s*[-—]\s*\d{1,4})?", line):
            continue
        if re.match(r"^(?:目录|投标文件目录|响应文件目录)\s*$", line):
            continue
        lines.append(line)
    text = _clean_text("\n".join(lines))
    text = re.sub(r"\s+P\s*\d{1,4}(?:\s*[-—]\s*\d{1,4})?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?", "", text)
    text = re.sub(r"\s*([,，;；])\s*", r"\1", text)
    return text[:limit].strip()


def _focus_text(value: object, query: str = "", limit: int = 1800) -> str:
    """Keep the small evidence window around the terms that caused a match."""
    text = _display_text(value, 5000)
    lines = [segment.strip() for line in text.splitlines() for segment in re.split(r"(?<=[。；;！？?])\s+", line) if segment.strip()]
    query_tokens = {
        token for token in _search_tokens(query)
        if len(token) >= 3 and token not in _RAG_REPORT_STOPWORDS
    }
    if len(text) <= limit and not query_tokens:
        return text
    if not query_tokens or not lines:
        return text[:limit].strip()
    scored = sorted(
        ((sum(len(token) for token in query_tokens & _search_tokens(line)), index) for index, line in enumerate(lines)),
        reverse=True,
    )
    best_score, best_index = scored[0]
    if best_score <= 0:
        return text[:limit].strip()
    selected = lines[max(0, best_index - 1): best_index + 2]
    focused = _clean_text("\n".join(selected))
    return focused[:limit].strip()


def _is_display_noise(title: object, content: object) -> bool:
    title_text = _unwrap_text_value(title)
    content_text = _unwrap_text_value(content)
    if not content_text:
        return True
    leader_count = len(re.findall(r"(?:\.{4,}|…{3,}|·{5,})\s*\d{1,4}", content_text))
    if leader_count >= 2:
        return True
    if "目录" in title_text and len(content_text) < 2600:
        return True
    if "响应文件格式及提交资料清单" in content_text and leader_count >= 1:
        return True
    return False


def _search_tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    latin = set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,30}", compact))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", compact)
    chinese: set[str] = set()
    for run in chinese_runs:
        chinese.update(run[index : index + 2] for index in range(len(run) - 1))
        chinese.update(run[index : index + 3] for index in range(len(run) - 2))
        chinese.update(run[index : index + 4] for index in range(len(run) - 3))
    return latin | chinese


def _is_heading(line: str) -> bool:
    value = line.strip()
    if not value or len(value) > 80:
        return False
    if re.match(r"^(?:第[一二三四五六七八九十百0-9]+[章节部分]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.．])", value):
        return True
    return any(keyword in value for keyword in ("评分", "资格", "响应", "方案", "服务", "目录", "报价", "承诺", "业绩")) and len(value) <= 36


def _chunk_kind(doc_role: str, title: str, content: str) -> str:
    source = f"{title}\n{content}"
    if doc_role == "tender":
        if any(keyword in source for keyword in SCORING_KEYWORDS):
            return "tender_scoring"
        if any(keyword in source for keyword in QUALIFICATION_KEYWORDS):
            return "tender_qualification"
        if any(keyword in source for keyword in INVALID_KEYWORDS):
            return "tender_invalid"
        return "tender_general"
    if any(keyword in source for keyword in CATALOG_KEYWORDS) and len(content) <= 2600:
        return "proposal_catalog"
    if any(keyword in source for keyword in COMPANY_KEYWORDS):
        return "proposal_company"
    return "proposal_response"


def _build_chunks(text: str, doc_role: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    title = "全文"
    buffer: list[str] = []
    buffer_chars = 0

    def flush() -> None:
        nonlocal buffer, buffer_chars
        content = _clean_text("\n".join(buffer))
        buffer = []
        buffer_chars = 0
        if len(re.sub(r"\s+", "", content)) < 80:
            return
        kind = _chunk_kind(doc_role, title, content)
        tokens = sorted(_search_tokens(f"{title}\n{content}"))[:1400]
        chunks.append(
            {
                "chunk_title": title[:200],
                "chunk_kind": kind,
                "content": content[:MAX_CHUNK_CHARS],
                "tokens_json": json.dumps(tokens, ensure_ascii=False),
                "char_count": len(content),
                "sort_order": len(chunks) + 1,
            }
        )

    for raw_line in re.split(r"\n+", text or ""):
        line = raw_line.strip()
        if not line:
            if buffer and buffer[-1] != "":
                buffer.append("")
                buffer_chars += 1
            continue
        if _is_heading(line):
            if buffer_chars >= 260:
                flush()
            title = line[:200]
        candidate_len = buffer_chars + len(line)
        if buffer and candidate_len > MAX_CHUNK_CHARS:
            flush()
        buffer.append(line)
        buffer_chars += len(line)
    if buffer:
        flush()
    return chunks


def _snippet_lines(text: str, keywords: tuple[str, ...], limit: int) -> str:
    lines = []
    for line in re.split(r"\n+", text or ""):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if 12 <= len(cleaned) <= 420 and any(keyword in cleaned for keyword in keywords):
            lines.append(cleaned)
    return "\n".join(dict.fromkeys(lines))[:limit]


def _project_type_from_text(text: str) -> str:
    if any(keyword in text for keyword in PROJECT_TYPE_KEYWORDS):
        return "食堂食材配送"
    return "通用服务采购"


def _style_profile_from_text(text: str) -> str:
    paragraphs = [line.strip() for line in re.split(r"\n+", text or "") if len(line.strip()) >= 40]
    avg_len = round(sum(len(item) for item in paragraphs[:500]) / max(1, min(len(paragraphs), 500)))
    has_catalog = any(re.match(r"^(?:第[一二三四五六七八九十百0-9]+[章节]|\d+(?:\.\d+)*)", item) for item in paragraphs[:500])
    return (
        f"历史标书以正式、稳健、细节充分的投标表达为主；平均段落约{avg_len or 120}字；"
        f"{'常使用分级编号和目录化结构组织内容' if has_catalog else '多使用连续段落说明服务措施'}。"
        "生成时可学习其论证顺序、服务承诺表达和措施展开方式，但不得照搬历史项目专属事实。"
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _upsert_case(conn: sqlite3.Connection, item: LocalPdfItem, source_folder: Path) -> int:
    now = _now_text()
    conn.execute(
        """
        INSERT INTO rag_cases (case_key, case_name, source_folder, ingest_status, create_time, update_time)
        VALUES (?, ?, ?, 'processing', ?, ?)
        ON CONFLICT(case_key) DO UPDATE SET
            case_name = excluded.case_name,
            source_folder = excluded.source_folder,
            ingest_status = 'processing',
            ingest_error = NULL,
            update_time = excluded.update_time
        """,
        (item.case_key, item.case_name, str(source_folder), now, now),
    )
    row = conn.execute("SELECT case_id FROM rag_cases WHERE case_key = ?", (item.case_key,)).fetchone()
    return int(row["case_id"])


def _clear_case_payload(conn: sqlite3.Connection, case_id: int) -> None:
    chunk_ids = [int(row["chunk_id"]) for row in conn.execute("SELECT chunk_id FROM rag_case_chunks WHERE case_id = ?", (case_id,)).fetchall()]
    vector_store.delete_rag_case_chunks(chunk_ids)
    conn.execute("DELETE FROM rag_scoring_mappings WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM rag_case_chunks WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM rag_case_documents WHERE case_id = ?", (case_id,))


def _insert_document(conn: sqlite3.Connection, case_id: int, item: LocalPdfItem, text: str, page_count: int) -> int:
    now = _now_text()
    conn.execute(
        """
        INSERT INTO rag_case_documents (
            case_id, doc_role, file_name, file_path, file_size, file_mtime, page_count, raw_text, text_chars,
            parse_status, parse_error, create_time, update_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', NULL, ?, ?)
        ON CONFLICT(case_id, file_path) DO UPDATE SET
            doc_role = excluded.doc_role,
            file_name = excluded.file_name,
            file_size = excluded.file_size,
            file_mtime = excluded.file_mtime,
            page_count = excluded.page_count,
            raw_text = excluded.raw_text,
            text_chars = excluded.text_chars,
            parse_status = 'completed',
            parse_error = NULL,
            update_time = excluded.update_time
        """,
        (
            case_id,
            item.doc_role,
            item.path.name,
            str(item.path),
            item.path.stat().st_size,
            item.path.stat().st_mtime,
            page_count,
            text,
            len(text),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT document_id FROM rag_case_documents WHERE case_id = ? AND file_path = ?", (case_id, str(item.path))).fetchone()
    return int(row["document_id"])


def _replace_page_index(
    conn: sqlite3.Connection,
    document_id: int,
    pages: tuple[tuple[int, str], ...],
) -> None:
    """Persist page text and token sets used by fast evidence lookup."""
    conn.execute("DELETE FROM rag_case_pages WHERE document_id = ?", (document_id,))
    now = _now_text()
    conn.executemany(
        """
        INSERT INTO rag_case_pages (document_id, page_number, content, tokens_json, create_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (document_id, page_number, content, json.dumps(sorted(_search_tokens(content)), ensure_ascii=False), now)
            for page_number, content in pages
        ],
    )


def _insert_chunks(conn: sqlite3.Connection, case_id: int, document_id: int, doc_role: str, chunks: list[dict[str, Any]]) -> None:
    now = _now_text()
    conn.executemany(
        """
        INSERT INTO rag_case_chunks (
            case_id, document_id, doc_role, chunk_kind, chunk_title, content,
            tokens_json, char_count, sort_order, create_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                case_id,
                document_id,
                doc_role,
                chunk["chunk_kind"],
                chunk["chunk_title"],
                chunk["content"],
                chunk["tokens_json"],
                chunk["char_count"],
                chunk["sort_order"],
                now,
            )
            for chunk in chunks
        ],
    )


def _sync_rag_document_chunks_to_vector_store(conn: sqlite3.Connection, document_id: int) -> None:
    if not vector_store.enabled():
        return
    rows = conn.execute(
        """
        SELECT ch.*, c.case_name
        FROM rag_case_chunks ch
        JOIN rag_cases c ON c.case_id = ch.case_id
        WHERE ch.document_id = ?
        ORDER BY ch.sort_order, ch.chunk_id
        """,
        (document_id,),
    ).fetchall()
    records = [
        {
            "chunk_id": int(row["chunk_id"]),
            "case_id": int(row["case_id"]),
            "document_id": int(row["document_id"]),
            "doc_role": row["doc_role"] or "",
            "chunk_kind": row["chunk_kind"] or "",
            "chunk_title": row["chunk_title"] or "",
            "case_name": row["case_name"] or "",
            "content": row["content"] or "",
        }
        for row in rows
    ]
    vector_store.upsert_rag_case_chunks(records)


def _token_set(row: sqlite3.Row) -> set[str]:
    try:
        return set(json.loads(str(row["tokens_json"] or "[]")))
    except json.JSONDecodeError:
        return set()


def _score_overlap(query_tokens: set[str], chunk_tokens: set[str], title: str = "", content: str = "") -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    overlap = query_tokens & chunk_tokens
    if not overlap:
        return 0.0
    base = len(overlap) / math.sqrt(max(1, len(query_tokens)) * max(1, len(chunk_tokens)))
    title_tokens = _search_tokens(title)
    title_boost = 0.18 * len(query_tokens & title_tokens)
    scoring_boost = 0.10 if any(keyword in content for keyword in SCORING_KEYWORDS) else 0.0
    return round(base + title_boost + scoring_boost, 4)


def _build_scoring_mappings(conn: sqlite3.Connection, case_id: int) -> int:
    tender_rows = conn.execute(
        """
        SELECT * FROM rag_case_chunks
        WHERE case_id = ? AND doc_role = 'tender' AND chunk_kind IN ('tender_scoring', 'tender_qualification')
        """,
        (case_id,),
    ).fetchall()
    proposal_rows = conn.execute(
        """
        SELECT * FROM rag_case_chunks
        WHERE case_id = ? AND doc_role = 'proposal' AND chunk_kind IN ('proposal_response', 'proposal_company')
        """,
        (case_id,),
    ).fetchall()
    now = _now_text()
    created = 0
    proposal_tokens = [(row, _token_set(row)) for row in proposal_rows]
    for tender in tender_rows[:80]:
        query_tokens = _token_set(tender)
        ranked: list[tuple[float, sqlite3.Row]] = []
        for proposal, tokens in proposal_tokens:
            score = _score_overlap(query_tokens, tokens, str(proposal["chunk_title"] or ""), str(proposal["content"] or ""))
            if score > 0:
                ranked.append((score, proposal))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for score, proposal in ranked[:3]:
            if score < 0.035:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO rag_scoring_mappings (case_id, tender_chunk_id, proposal_chunk_id, match_score, create_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (case_id, int(tender["chunk_id"]), int(proposal["chunk_id"]), score, now),
            )
            created += 1
    return created


def _refresh_case_profiles(conn: sqlite3.Connection, case_id: int) -> None:
    docs = conn.execute("SELECT doc_role, raw_text FROM rag_case_documents WHERE case_id = ?", (case_id,)).fetchall()
    tender_text = "\n".join(str(row["raw_text"] or "")[:120000] for row in docs if row["doc_role"] == "tender")
    proposal_text = "\n".join(str(row["raw_text"] or "")[:180000] for row in docs if row["doc_role"] == "proposal")
    tender_summary = _snippet_lines(tender_text, SCORING_KEYWORDS + QUALIFICATION_KEYWORDS + INVALID_KEYWORDS, 4500)
    proposal_summary = _snippet_lines(proposal_text, ("方案", "服务", "配送", "质量", "安全", "应急", "承诺"), 4500)
    company_profile = _snippet_lines(proposal_text, COMPANY_KEYWORDS, 6000)
    style_profile = _style_profile_from_text(proposal_text)
    project_type = _project_type_from_text(f"{tender_text[:5000]}\n{proposal_text[:5000]}")
    conn.execute(
        """
        UPDATE rag_cases
        SET project_type = ?, tender_summary = ?, proposal_summary = ?, company_profile = ?,
            style_profile = ?, ingest_status = 'completed', ingest_error = NULL, update_time = ?
        WHERE case_id = ?
        """,
        (project_type, tender_summary, proposal_summary, company_profile, style_profile, _now_text(), case_id),
    )


def ingest_case_folder(folder_path: str | None = None, *, force: bool = False) -> dict[str, Any]:
    folder = _safe_folder(folder_path)
    scan = scan_case_folder(str(folder))
    items: list[LocalPdfItem] = []
    for case in scan["cases"]:
        for file_info in case["tender_files"]:
            items.append(LocalPdfItem(Path(file_info["file_path"]), case["case_key"], case["case_name"], "tender"))
        for file_info in case["proposal_files"]:
            items.append(LocalPdfItem(Path(file_info["file_path"]), case["case_key"], case["case_name"], "proposal"))

    stats: dict[str, Any] = {"folder": str(folder), "case_count": scan["case_count"], "pdf_count": len(items), "cases": []}
    with storage.connect() as conn:
        seen_cases: set[int] = set()
        for item in items:
            case_id = _upsert_case(conn, item, folder)
            if force and case_id not in seen_cases:
                _clear_case_payload(conn, case_id)
                seen_cases.add(case_id)
            existing = conn.execute(
                "SELECT * FROM rag_case_documents WHERE case_id = ? AND file_path = ?",
                (case_id, str(item.path)),
            ).fetchone()
            file_stat = item.path.stat()
            unchanged = bool(
                existing
                and not force
                and str(existing["parse_status"] or "") == "completed"
                and int(existing["file_size"] or 0) == int(file_stat.st_size)
                and abs(float(existing["file_mtime"] or 0) - float(file_stat.st_mtime)) < 0.01
                and conn.execute(
                    "SELECT 1 FROM rag_case_pages WHERE document_id = ? LIMIT 1",
                    (int(existing["document_id"]),),
                ).fetchone()
            )
            if unchanged:
                stats["cases"].append(
                    {
                        "case_id": case_id,
                        "case_key": item.case_key,
                        "case_name": item.case_name,
                        "file_name": item.path.name,
                        "doc_role": item.doc_role,
                        "status": "cached",
                        "page_count": int(existing["page_count"] or 0),
                        "text_chars": int(existing["text_chars"] or 0),
                    }
                )
                continue
            if existing and not force:
                # Re-index only this changed document.  Mappings pointing at
                # its chunks are removed and rebuilt after the file is parsed.
                old_chunk_ids = [int(row["chunk_id"]) for row in conn.execute("SELECT chunk_id FROM rag_case_chunks WHERE document_id = ?", (int(existing["document_id"]),)).fetchall()]
                vector_store.delete_rag_case_chunks(old_chunk_ids)
                conn.execute(
                    "DELETE FROM rag_scoring_mappings WHERE tender_chunk_id IN (SELECT chunk_id FROM rag_case_chunks WHERE document_id = ?) OR proposal_chunk_id IN (SELECT chunk_id FROM rag_case_chunks WHERE document_id = ?)",
                    (int(existing["document_id"]), int(existing["document_id"])),
                )
                conn.execute("DELETE FROM rag_case_chunks WHERE document_id = ?", (int(existing["document_id"]),))
                conn.execute("DELETE FROM rag_case_pages WHERE document_id = ?", (int(existing["document_id"]),))
            logger.info("RAG ingest parsing file=%s role=%s case=%s", item.path.name, item.doc_role, item.case_key)
            try:
                text, page_count, pages = _extract_pdf_text(item.path)
                document_id = _insert_document(conn, case_id, item, text, page_count)
                _replace_page_index(conn, document_id, pages)
                chunks = _build_chunks(text, item.doc_role)
                _insert_chunks(conn, case_id, document_id, item.doc_role, chunks)
                _sync_rag_document_chunks_to_vector_store(conn, document_id)
                stats["cases"].append(
                    {
                        "case_id": case_id,
                        "case_key": item.case_key,
                        "case_name": item.case_name,
                        "file_name": item.path.name,
                        "doc_role": item.doc_role,
                        "page_count": page_count,
                        "text_chars": len(text),
                        "chunk_count": len(chunks),
                        "status": "indexed",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("RAG ingest failed file=%s", item.path)
                conn.execute(
                    "UPDATE rag_cases SET ingest_status = 'failed', ingest_error = ?, update_time = ? WHERE case_id = ?",
                    (str(exc), _now_text(), case_id),
                )
        case_ids = [int(row["case_id"]) for row in conn.execute("SELECT case_id FROM rag_cases").fetchall()]
        mapping_count = 0
        for case_id in case_ids:
            _refresh_case_profiles(conn, case_id)
            mapping_count += _build_scoring_mappings(conn, case_id)
        conn.commit()
    stats["mapping_count"] = mapping_count
    stats["index_mode"] = "full" if force else "incremental"
    return stats


def list_cases() -> list[dict[str, Any]]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, 
                   COUNT(DISTINCT d.document_id) AS document_count,
                   COUNT(DISTINCT ch.chunk_id) AS chunk_count
            FROM rag_cases c
            LEFT JOIN rag_case_documents d ON d.case_id = c.case_id
            LEFT JOIN rag_case_chunks ch ON ch.case_id = c.case_id
            GROUP BY c.case_id
            ORDER BY c.update_time DESC, c.case_id DESC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_current_bid_query(bid_id: int) -> str:
    with storage.connect() as conn:
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        bid = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
    if bid is None:
        raise RagCaseServiceError(f"标书不存在：{bid_id}", 404)
    if parse is None:
        raise RagCaseServiceError("请先完成招标文件解读，再检索历史案例", 400)
    parts = [
        str(bid["bid_title"] or ""),
        str(parse["parsed_project_overview"] or ""),
        str(parse["parsed_score_requirements"] or ""),
        str(parse["parsed_tech_scoring"] or ""),
        str(parse["parsed_biz_requirements"] or ""),
        str(parse["qualification_requirements"] or ""),
        str(parse["bid_key_points"] or ""),
        str(parse["raw_full_text"] or "")[:6000],
    ]
    return "\n".join(item for item in parts if item).strip()


def retrieve_similar_cases(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    query_tokens = _search_tokens(query)
    if not query_tokens:
        return []
    if vector_store.enabled():
        milvus_hits = vector_store.search_rag_case_chunks(
            query,
            limit=max(limit * 40, 80),
            filters={"doc_role": "tender"} if vector_store.qdrant_enabled() else None,
        )
        chunk_ids = list(dict.fromkeys(int(hit.get("chunk_id") or 0) for hit in milvus_hits if int(hit.get("chunk_id") or 0) > 0))
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            with storage.connect() as conn:
                chunk_rows = conn.execute(
                    f"""
                    SELECT ch.*, c.case_key, c.case_name, c.project_type
                    FROM rag_case_chunks ch
                    JOIN rag_cases c ON c.case_id = ch.case_id
                    WHERE c.ingest_status = 'completed'
                      AND ch.doc_role = 'tender'
                      AND ch.chunk_id IN ({placeholders})
                    """,
                    chunk_ids,
                ).fetchall()
            row_map = {int(row["chunk_id"]): row for row in chunk_rows}
            score_map = {int(hit.get("chunk_id") or 0): float(hit.get("score") or 0.0) for hit in milvus_hits}
            case_scores: dict[int, dict[str, Any]] = {}
            for chunk_id in chunk_ids:
                row = row_map.get(chunk_id)
                if row is None:
                    continue
                case_id = int(row["case_id"])
                bucket = case_scores.setdefault(
                    case_id,
                    {
                        "case": {
                            "case_id": case_id,
                            "case_key": row["case_key"],
                            "case_name": row["case_name"],
                            "project_type": row["project_type"],
                        },
                        "score": 0.0,
                        "snippets": [],
                    },
                )
                bucket["score"] += score_map.get(chunk_id, 0.0)
                if len(bucket["snippets"]) < 3:
                    bucket["snippets"].append({"title": row["chunk_title"], "kind": row["chunk_kind"], "content": str(row["content"] or "")[:500]})
            ranked = [item for item in case_scores.values() if item["score"] > 0]
            ranked.sort(key=lambda item: item["score"], reverse=True)
            if ranked:
                top_score = float(ranked[0]["score"])
                result: list[dict[str, Any]] = []
                for item in ranked[:limit]:
                    case = item["case"]
                    score = float(item["score"])
                    result.append(
                        {
                            "case_id": case["case_id"],
                            "case_key": case["case_key"],
                            "case_name": case["case_name"],
                            "project_type": case["project_type"],
                            "similarity_score": round(score, 4),
                            "similarity_percent": round(score / top_score * 100) if top_score else 0,
                            "snippets": item["snippets"],
                        }
                    )
                return result
    with storage.connect() as conn:
        case_rows = conn.execute("SELECT * FROM rag_cases WHERE ingest_status = 'completed'").fetchall()
        chunk_rows = conn.execute(
            """
            SELECT ch.*, c.case_name, c.project_type
            FROM rag_case_chunks ch
            JOIN rag_cases c ON c.case_id = ch.case_id
            WHERE c.ingest_status = 'completed' AND ch.doc_role = 'tender'
            """
        ).fetchall()
    case_scores: dict[int, dict[str, Any]] = {
        int(row["case_id"]): {"case": _row_to_dict(row), "score": 0.0, "snippets": []} for row in case_rows
    }
    for chunk in chunk_rows:
        score = _score_overlap(query_tokens, _token_set(chunk), str(chunk["chunk_title"] or ""), str(chunk["content"] or ""))
        if score <= 0:
            continue
        bucket = case_scores.get(int(chunk["case_id"]))
        if not bucket:
            continue
        bucket["score"] += score
        if len(bucket["snippets"]) < 3:
            bucket["snippets"].append(
                {"title": chunk["chunk_title"], "kind": chunk["chunk_kind"], "content": str(chunk["content"] or "")[:500]}
            )
    ranked = [item for item in case_scores.values() if item["score"] > 0]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    result: list[dict[str, Any]] = []
    top_score = float(ranked[0]["score"]) if ranked else 0.0
    for item in ranked[:limit]:
        case = item["case"]
        score = float(item["score"])
        result.append(
            {
                "case_id": case["case_id"],
                "case_key": case["case_key"],
                "case_name": case["case_name"],
                "project_type": case["project_type"],
                "similarity_score": round(score, 4),
                "similarity_percent": round(score / top_score * 100) if top_score else 0,
                "snippets": item["snippets"],
            }
        )
    return result


def retrieve_similar_cases_for_bid(bid_id: int, *, limit: int = 5) -> dict[str, Any]:
    requirements = _load_current_bid_requirements(bid_id)
    if not requirements:
        return {"bid_id": bid_id, "similar_cases": [], "analysis_status": "pending"}
    ranked = _rank_cases_by_requirements(requirements, limit=limit)
    return {"bid_id": bid_id, "similar_cases": ranked, "analysis_status": "completed"}


_RAG_REPORT_STOPWORDS = {
    "投标人", "招标人", "项目", "提供", "服务", "方案", "要求", "文件", "内容", "相关", "应当", "包括", "具有", "进行", "按照", "不得", "或者", "以及", "其中", "采购", "投标", "评审",
}


def _decode_json_value(value: object) -> object:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _load_current_bid_requirements(bid_id: int) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        bid = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
    if bid is None:
        raise RagCaseServiceError(f"标书不存在：{bid_id}", 404)
    if parse is None or str(parse["parse_status"] or "") != "completed":
        raise RagCaseServiceError("招标文件仍在解析中，完成后才能检索历史案例", 409)

    requirements: list[dict[str, Any]] = []
    fields = (
        ("parsed_tech_scoring", "技术评分"),
        ("parsed_biz_requirements", "商务评分"),
        ("qualification_requirements", "资格要求"),
        ("invalid_bid_clauses", "否决条款"),
        ("bid_key_points", "关键要求"),
    )
    for field, kind in fields:
        value = _decode_json_value(parse[field])
        values: list[object] = []
        if isinstance(value, list):
            values = value
        elif isinstance(value, dict):
            for key in ("items", "data", "tech_scoring_items", "biz_scoring_items", "qualification_requirements", "invalid_bid_clauses", "bid_key_points"):
                if isinstance(value.get(key), list):
                    values.extend(value[key])
            if not values:
                values = [value]
        elif isinstance(value, str) and value.strip():
            values = [line.strip(" -•") for line in value.splitlines() if len(line.strip(" -•")) >= 18]
        for item in values:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or item.get("category") or kind).strip()
                content = _display_text(item.get("criteria") or item.get("content") or item.get("requirement") or item.get("description") or "", 1800)
            else:
                title, content = kind, _display_text(item, 1800)
            if len(content) < 18:
                continue
            duplicate = next((existing for existing in requirements if existing["title"] == title and existing["content"] == content), None)
            if duplicate is None:
                requirements.append({"index": len(requirements), "kind": kind, "title": title[:120], "content": content[:1800]})
    if not requirements:
        raw = str(parse["parsed_score_requirements"] or parse["parsed_project_overview"] or "")
        for line in raw.splitlines():
            clean = line.strip(" -•")
            if len(clean) >= 24:
                requirements.append({"index": len(requirements), "kind": "招标要求", "title": "招标要求", "content": clean[:1800]})
    return requirements[:30]


def _shared_report_terms(left: str, right: str) -> list[str]:
    terms = _search_tokens(left) & _search_tokens(right)
    filtered = [term for term in terms if len(term) >= 3 and term not in _RAG_REPORT_STOPWORDS]
    return sorted(filtered, key=lambda item: (-len(item), item))[:6]


def _pdf_terms(*values: object) -> list[str]:
    """Extract a small, useful set of phrases for PDF page matching/highlighting."""
    terms: list[str] = []
    for value in values:
        text = _unwrap_text_value(value)
        for token in sorted(_search_tokens(text), key=lambda item: (-len(item), item)):
            if len(token) < 3 or token in _RAG_REPORT_STOPWORDS:
                continue
            if token not in terms:
                terms.append(token)
            if len(terms) >= 10:
                return terms
    return terms


def _page_markers_from_text(text: str, *, max_pages: int = 2) -> list[int]:
    pages: list[int] = []
    for match in re.finditer(r"第\s*(\d{1,4})\s*页", text or ""):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
        if len(pages) >= max_pages:
            break
    return pages


@lru_cache(maxsize=24)
def _cached_pdf_page_texts(path_text: str) -> tuple[tuple[int, str], ...]:
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return ()
    cached = _PDF_PAGE_TEXT_CACHE.get(path_text)
    if cached is not None:
        return cached
    # Page text is persisted during ingestion.  Reading it from SQLite is
    # substantially faster than reopening and parsing a long PDF after a
    # backend restart.
    try:
        with storage.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.page_number, p.content
                FROM rag_case_pages p
                JOIN rag_case_documents d ON d.document_id = p.document_id
                WHERE d.file_path = ?
                ORDER BY p.page_number ASC
                """,
                (path_text,),
            ).fetchall()
        if rows:
            value = tuple((int(row["page_number"]), str(row["content"] or "")) for row in rows)
            _PDF_PAGE_TEXT_CACHE[path_text] = value
            return value
    except Exception as exc:  # noqa: BLE001
        logger.debug("RAG persisted page index unavailable file=%s error=%s", path_text, exc)
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            pages: list[tuple[int, str]] = []
            for page_number, page in enumerate(pdf.pages, 1):
                try:
                    page_text = _clean_text(page.extract_text(x_tolerance=1.5, y_tolerance=3, layout=False) or "")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("RAG cached PDF page parse skipped file=%s page=%s error=%s", path.name, page_number, exc)
                    page_text = ""
                if page_text:
                    pages.append((page_number, page_text))
            value = tuple(pages)
            _PDF_PAGE_TEXT_CACHE[path_text] = value
            return value
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG cached PDF parse failed file=%s error=%s", path, exc)
        return ()


def _find_pdf_evidence(row: sqlite3.Row, query: str, *, max_pages: int = 2) -> list[dict[str, Any]]:
    """Locate the source PDF page(s) for a chunk without trusting extracted page markers."""
    path = Path(str(row["source_file_path"] or ""))
    if not path.exists() or not path.is_file():
        return []
    terms = _pdf_terms(query, row["chunk_title"], row["content"])
    query_tokens = set(terms)
    if not query_tokens:
        return []
    marker_pages = _page_markers_from_text(str(row["content"] or ""), max_pages=max_pages)
    if marker_pages:
        return [
            {
                "document_id": int(row["document_id"]),
                "doc_role": str(row["doc_role"] or ""),
                "file_name": str(row["source_file_name"] or path.name),
                "page_number": page,
                "page_count": int(row["source_page_count"] or 0),
                "terms": terms[:8],
                "score": 1.0,
            }
            for page in marker_pages
        ]
    try:
        ranked: list[tuple[float, int]] = []
        for page_number, page_text in _cached_pdf_page_texts(str(path)):
            page_tokens = _search_tokens(page_text)
            overlap = len(query_tokens & page_tokens)
            exact_hits = sum(1 for term in terms if term in page_text)
            score = overlap / math.sqrt(max(1, len(query_tokens)) * max(1, len(page_tokens))) + exact_hits * 0.04
            if score > 0:
                ranked.append((score, page_number))
        ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        if not ranked:
            return []
        selected: list[tuple[float, int]] = [ranked[0]]
        # Include one neighboring evidence page when it is also materially relevant.
        for candidate in ranked[1:]:
            if len(selected) >= max_pages:
                break
            if candidate[0] >= ranked[0][0] * 0.52 or abs(candidate[1] - ranked[0][1]) <= 1:
                selected.append(candidate)
        return [
            {
                "document_id": int(row["document_id"]),
                "doc_role": str(row["doc_role"] or ""),
                "file_name": str(row["source_file_name"] or path.name),
                "page_number": page,
                "page_count": int(row["source_page_count"] or 0),
                "terms": terms[:8],
                "score": round(float(score), 4),
            }
            for score, page in selected
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG evidence page locate failed file=%s error=%s", path, exc)
        return []


def _evidence_similarity(query_tokens: set[str], row: sqlite3.Row) -> float:
    tokens = _token_set(row)
    if not query_tokens or not tokens:
        return 0.0
    overlap = query_tokens & tokens
    if not overlap:
        return 0.0
    base = len(overlap) / math.sqrt(max(1, len(query_tokens)) * max(1, len(tokens)))
    title_tokens = _search_tokens(str(row["chunk_title"] or ""))
    title_overlap = len(query_tokens & title_tokens) / max(1, len(title_tokens))
    return round(min(1.0, base + min(0.12, title_overlap * 0.12)), 4)


def _case_evidence(requirements: list[dict[str, Any]], case_id: int, *, limit: int = 8, include_pdf_pages: bool = False) -> dict[str, Any]:
    with storage.connect() as conn:
        case = conn.execute("SELECT * FROM rag_cases WHERE case_id = ? AND ingest_status = 'completed'", (case_id,)).fetchone()
        tender_rows = [
            row for row in conn.execute(
                """
                SELECT ch.*, d.file_name AS source_file_name, d.file_path AS source_file_path,
                       d.page_count AS source_page_count
                FROM rag_case_chunks ch
                JOIN rag_case_documents d ON d.document_id = ch.document_id
                WHERE ch.case_id = ? AND ch.doc_role = 'tender'
                ORDER BY ch.sort_order ASC
                LIMIT 140
                """,
                (case_id,),
            ).fetchall()
            if not _is_display_noise(row["chunk_title"], row["content"])
        ]
        proposal_rows = [
            row for row in conn.execute(
                """
                SELECT ch.*, d.file_name AS source_file_name, d.file_path AS source_file_path,
                       d.page_count AS source_page_count
                FROM rag_case_chunks ch
                JOIN rag_case_documents d ON d.document_id = ch.document_id
                WHERE ch.case_id = ? AND ch.doc_role = 'proposal'
                  AND ch.chunk_kind IN ('proposal_response', 'proposal_company')
                ORDER BY ch.sort_order ASC
                LIMIT 160
                """,
                (case_id,),
            ).fetchall()
            if not _is_display_noise(row["chunk_title"], row["content"])
        ]
        mappings = conn.execute(
            "SELECT m.*, pc.chunk_title AS proposal_title, pc.content AS proposal_content, pc.chunk_kind AS proposal_kind FROM rag_scoring_mappings m JOIN rag_case_chunks pc ON pc.chunk_id = m.proposal_chunk_id WHERE m.case_id = ? ORDER BY m.match_score DESC",
            (case_id,),
        ).fetchall()
    if case is None:
        raise RagCaseServiceError(f"历史案例不存在：{case_id}", 404)
    mapped: dict[int, sqlite3.Row] = {}
    for row in mappings:
        tender_id = int(row["tender_chunk_id"])
        if tender_id not in mapped:
            mapped[tender_id] = row
    candidates: list[dict[str, Any]] = []
    for req in requirements:
        query_tokens = _search_tokens(f"{req['title']}\n{req['content']}")
        ranked = []
        for chunk in tender_rows:
            score = _evidence_similarity(query_tokens, chunk)
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < 0.035:
            continue
        score, chunk = ranked[0]
        mapping = mapped.get(int(chunk["chunk_id"]))
        # 响应检索只围绕“历史招标要求”寻找标书正文，避免被投标函、目录等通用文字误命中。
        historical_excerpt = _focus_text(chunk["content"], f"{req['title']}\n{req['content']}", 1200)
        response_query = _search_tokens(f"{chunk['chunk_title']}\n{historical_excerpt}")
        response_ranked: list[tuple[float, sqlite3.Row]] = []
        mapped_response_id = int(mapping["proposal_chunk_id"]) if mapping else None
        for proposal in proposal_rows:
            response_score = _evidence_similarity(response_query, proposal)
            if mapped_response_id == int(proposal["chunk_id"]):
                response_score += 0.025
            if response_score > 0:
                response_ranked.append((response_score, proposal))
        response_ranked.sort(key=lambda item: item[0], reverse=True)
        response_score, response = response_ranked[0] if response_ranked else (0.0, None)
        historical_response = None
        if response is not None and response_score >= 0.055:
            response_excerpt = _focus_text(response["content"], f"{req['title']}\n{historical_excerpt}", 1800)
            specific_terms = {
                token for token in _search_tokens(historical_excerpt)
                if len(token) >= 4 and token not in _RAG_REPORT_STOPWORDS
            }
            response_terms = _search_tokens(response_excerpt)
            evidence_terms = specific_terms if specific_terms else set(_shared_report_terms(historical_excerpt, response_excerpt))
            if evidence_terms & response_terms:
                historical_response = {"title": _display_text(response["chunk_title"] or "历史标书响应", 120), "kind": str(response["chunk_kind"] or "proposal_response"), "content": response_excerpt, "mapping_score": round(float(response_score), 4)}
        candidates.append({
            "current_requirement": {"kind": req["kind"], "title": req["title"], "content": req["content"]},
            "historical_requirement": {"title": _display_text(chunk["chunk_title"] or "历史招标要求", 120), "kind": str(chunk["chunk_kind"] or "tender_general"), "content": _focus_text(chunk["content"], f"{req['title']}\n{req['content']}", 1200)},
            "historical_response": historical_response,
            "match_score": round(float(score), 4),
            "shared_terms": _shared_report_terms(req["content"], str(chunk["content"] or "")),
            "_requirement_row": chunk,
            "_response_row": response if historical_response else None,
        })
    candidates.sort(key=lambda item: (item["historical_response"] is not None, item["match_score"]), reverse=True)
    selected = candidates[:limit]
    if include_pdf_pages:
        for item in selected:
            item["evidence_pages"] = {
                "historical_requirement": _find_pdf_evidence(item["_requirement_row"], f"{item['current_requirement']['title']}\n{item['current_requirement']['content']}\n{item['historical_requirement']['content']}"),
                "historical_response": _find_pdf_evidence(
                    item["_response_row"],
                    f"{item['current_requirement']['title']}\n{item['historical_requirement']['content']}\n{item['historical_response']['content'] if item['historical_response'] else ''}",
                ) if item.get("_response_row") is not None and item.get("historical_response") else [],
            }
    for item in selected:
        item.pop("_requirement_row", None)
        item.pop("_response_row", None)
    evidence_score = sum(float(item["match_score"]) for item in selected[:5]) / max(1, min(5, len(selected)))
    return {"case": _row_to_dict(case), "comparisons": selected, "evidence_score": round(evidence_score, 4)}


def _rank_cases_by_requirements(requirements: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        case_ids = [int(row["case_id"]) for row in conn.execute("SELECT case_id FROM rag_cases WHERE ingest_status = 'completed'").fetchall()]
    ranked = []
    for case_id in case_ids:
        evidence = _case_evidence(requirements, case_id, limit=6)
        comparisons = evidence["comparisons"]
        if not comparisons:
            continue
        matched = len(comparisons)
        score = float(evidence["evidence_score"])
        ranked.append({
            "case_id": case_id,
            "case_key": evidence["case"]["case_key"],
            "case_name": evidence["case"]["case_name"],
            "project_type": evidence["case"]["project_type"],
            "similarity_score": round(score, 4),
            "matched_requirements": matched,
            "evidence_preview": [{"current_title": item["current_requirement"]["title"], "historical_title": item["historical_requirement"]["title"], "response_title": (item["historical_response"] or {}).get("title"), "shared_terms": item["shared_terms"]} for item in comparisons[:3]],
            "snippets": [{"title": item["historical_requirement"]["title"], "kind": item["historical_requirement"]["kind"], "content": item["historical_requirement"]["content"][:500]} for item in comparisons[:3]],
        })
    ranked.sort(key=lambda item: (item["matched_requirements"], item["similarity_score"]), reverse=True)
    if not ranked:
        return []
    top = float(ranked[0]["similarity_score"])
    threshold = max(0.035, top * 0.38)
    for item in ranked:
        item["similarity_percent"] = round(min(100, float(item["similarity_score"]) / top * 100)) if top else 0
    return [item for item in ranked if float(item["similarity_score"]) >= threshold][:limit]


def build_case_comparison_report(bid_id: int, case_id: int, *, limit: int = 8) -> dict[str, Any]:
    requirements = _load_current_bid_requirements(bid_id)
    evidence = _case_evidence(requirements, case_id, limit=limit, include_pdf_pages=True)
    with storage.connect() as conn:
        bid = conn.execute("SELECT bid_title FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
    return {"bid_id": bid_id, "case_id": case_id, "bid_title": str(bid["bid_title"] if bid else ""), "case": evidence["case"], "comparisons": evidence["comparisons"], "evidence_score": evidence["evidence_score"], "analysis_source": "structured_retrieval"}


async def generate_case_comparison_report(bid_id: int, case_id: int, *, limit: int = 8) -> dict[str, Any]:
    report = build_case_comparison_report(bid_id, case_id, limit=limit)
    if not report["comparisons"] or not settings.qwen_api_key.strip():
        return report
    evidence_text = "\n\n".join(
        f"证据{index + 1}\n当前要求：{item['current_requirement']['content']}\n历史要求：{item['historical_requirement']['content']}\n历史响应：{(item['historical_response'] or {}).get('content', '未找到明确响应')}"
        for index, item in enumerate(report["comparisons"][:6])
    )
    prompt = (
        "你是招标文件历史案例分析员。只能根据给出的三段原文证据生成JSON，不得补造企业事实、项目名称、金额、日期或承诺。"
        "请为每条证据写出similarity_analysis（相似原因）、response_method（历史标书如何处理）、reuse_boundary（当前项目使用时需要修改的内容）。"
        "同时给出executive_summary和reuse_advice。仅输出JSON对象。\n\n" + evidence_text[:18000]
    )
    result = await chat_completion(
        [{"role": "system", "content": "输出严谨、正式的历史案例对比报告。"}, {"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=2600, scene="knowledge_analysis",
        json_mode=True,
    )
    try:
        content = result.content.strip()
        parsed = json.loads(content[content.find("{") : content.rfind("}") + 1])
        if isinstance(parsed, dict):
            report["analysis"] = parsed
            report["analysis_source"] = "structured_retrieval_qwen"
    except (ValueError, json.JSONDecodeError):
        logger.warning("RAG comparison report Qwen response was not valid JSON bid=%s case=%s", bid_id, case_id)
    return report


def retrieve_scoring_context(bid_id: int, query: str, bid_type: str = "open", *, limit: int = 6) -> dict[str, Any]:
    query_text = f"{_load_current_bid_query(bid_id)[:3000]}\n{query or ''}"
    query_tokens = _search_tokens(query_text)
    if not query_tokens:
        return {"has_rag": False, "similar_cases": [], "company_profile": "", "style_profile": "", "fragments": []}

    with storage.connect() as conn:
        case_rows = conn.execute("SELECT * FROM rag_cases WHERE ingest_status = 'completed'").fetchall()
        rows = conn.execute(
            """
            SELECT ch.*, c.case_name, c.company_profile, c.style_profile
            FROM rag_case_chunks ch
            JOIN rag_cases c ON c.case_id = ch.case_id
            WHERE c.ingest_status = 'completed'
              AND ch.doc_role = 'proposal'
              AND ch.chunk_kind IN ('proposal_response', 'proposal_company')
            """
        ).fetchall()
        mapping_rows = conn.execute(
            """
            SELECT m.match_score, pc.*, c.case_name, c.company_profile, c.style_profile, tc.tokens_json AS tender_tokens
            FROM rag_scoring_mappings m
            JOIN rag_case_chunks tc ON tc.chunk_id = m.tender_chunk_id
            JOIN rag_case_chunks pc ON pc.chunk_id = m.proposal_chunk_id
            JOIN rag_cases c ON c.case_id = m.case_id
            WHERE c.ingest_status = 'completed'
            """
        ).fetchall()

    ranked: list[tuple[float, sqlite3.Row, str]] = []
    if vector_store.enabled():
        milvus_hits = vector_store.search_rag_case_chunks(
            query_text,
            limit=max(limit * 40, 100),
            filters={"doc_role": "proposal", "chunk_kind": ["proposal_response", "proposal_company"]}
            if vector_store.qdrant_enabled()
            else None,
        )
        chunk_ids = list(dict.fromkeys(int(hit.get("chunk_id") or 0) for hit in milvus_hits if int(hit.get("chunk_id") or 0) > 0))
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            with storage.connect() as conn:
                milvus_rows = conn.execute(
                    f"""
                    SELECT ch.*, c.case_name, c.company_profile, c.style_profile
                    FROM rag_case_chunks ch
                    JOIN rag_cases c ON c.case_id = ch.case_id
                    WHERE c.ingest_status = 'completed'
                      AND ch.doc_role = 'proposal'
                      AND ch.chunk_kind IN ('proposal_response', 'proposal_company')
                      AND ch.chunk_id IN ({placeholders})
                    """,
                    chunk_ids,
                ).fetchall()
            row_map = {int(row["chunk_id"]): row for row in milvus_rows}
            score_map = {int(hit.get("chunk_id") or 0): float(hit.get("score") or 0.0) for hit in milvus_hits}
            for chunk_id in chunk_ids:
                row = row_map.get(chunk_id)
                if row is not None:
                    ranked.append((score_map.get(chunk_id, 0.0), row, "milvus_vector"))
    for row in rows:
        score = _score_overlap(query_tokens, _token_set(row), str(row["chunk_title"] or ""), str(row["content"] or ""))
        if row["chunk_kind"] == "proposal_company" and any(keyword in query_text for keyword in COMPANY_KEYWORDS):
            score += 0.2
        if score > 0:
            ranked.append((score, row, "direct"))
    for row in mapping_rows:
        try:
            tender_tokens = set(json.loads(str(row["tender_tokens"] or "[]")))
        except json.JSONDecodeError:
            tender_tokens = set()
        score = _score_overlap(query_tokens, tender_tokens, str(row["chunk_title"] or ""), str(row["content"] or "")) + float(row["match_score"] or 0) * 0.4
        if score > 0:
            ranked.append((score, row, "mapped_scoring"))
    ranked.sort(key=lambda item: item[0], reverse=True)

    fragments: list[dict[str, Any]] = []
    seen_chunks: set[int] = set()
    used_chars = 0
    for score, row, source_type in ranked:
        chunk_id = int(row["chunk_id"])
        if chunk_id in seen_chunks or used_chars >= MAX_FRAGMENT_CHARS:
            continue
        content = str(row["content"] or "").strip()
        if not content:
            continue
        content = content[: max(0, min(900, MAX_FRAGMENT_CHARS - used_chars))]
        used_chars += len(content)
        seen_chunks.add(chunk_id)
        fragments.append(
            {
                "case_name": row["case_name"],
                "title": row["chunk_title"],
                "kind": row["chunk_kind"],
                "source_type": source_type,
                "score": round(float(score), 4),
                "content": content,
            }
        )
        if len(fragments) >= limit:
            break

    style_profiles = list(dict.fromkeys(str(row["style_profile"] or "").strip() for row in case_rows if str(row["style_profile"] or "").strip()))
    company_profiles = list(dict.fromkeys(str(row["company_profile"] or "").strip() for row in case_rows if str(row["company_profile"] or "").strip()))
    return {
        "has_rag": bool(fragments or style_profiles or company_profiles),
        "similar_cases": retrieve_similar_cases(query_text, limit=3),
        "company_profile": "" if bid_type == "blind" else "\n".join(company_profiles)[:5000],
        "style_profile": "\n".join(style_profiles)[:2500],
        "fragments": [] if bid_type == "blind" else fragments,
    }


def format_scoring_context_prompt(context: dict[str, Any], bid_type: str = "open") -> str:
    if not context.get("has_rag"):
        return ""
    style_profile = str(context.get("style_profile") or "").strip()
    if bid_type == "blind":
        return (
            "\n历史案例库写作方法参考：\n"
            f"{style_profile or '沿用历史标书正式、审慎、分层展开的写作方式。'}\n"
            "暗标约束：仅学习历史写法和论证结构，禁止出现任何历史公司名称、项目名称、业主名称、人员姓名、品牌或可识别信息。\n"
        )

    return (
        "\n历史案例库写作方法：正式、审慎、按评分点分层论证。"
        "历史项目正文和企业资料不构成当前有效证明；不得复用历史报价、证件、人员、设备或业绩事实。\n"
    )


def get_case_detail(case_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        case = conn.execute("SELECT * FROM rag_cases WHERE case_id = ?", (case_id,)).fetchone()
        if case is None:
            raise RagCaseServiceError(f"历史案例不存在：{case_id}", 404)
        documents = conn.execute(
            """
            SELECT document_id, case_id, doc_role, file_name, file_path, file_size, page_count,
                   text_chars, parse_status, parse_error, create_time, update_time
            FROM rag_case_documents
            WHERE case_id = ?
            ORDER BY CASE doc_role WHEN 'tender' THEN 0 ELSE 1 END, document_id ASC
            """,
            (case_id,),
        ).fetchall()
        method_rows = conn.execute(
            """
            SELECT m.mapping_id, m.match_score,
                   tc.chunk_title AS tender_title, tc.content AS tender_content, tc.chunk_kind AS tender_kind,
                   pc.chunk_title AS proposal_title, pc.content AS proposal_content, pc.chunk_kind AS proposal_kind
            FROM rag_scoring_mappings m
            JOIN rag_case_chunks tc ON tc.chunk_id = m.tender_chunk_id
            JOIN rag_case_chunks pc ON pc.chunk_id = m.proposal_chunk_id
            WHERE m.case_id = ?
            ORDER BY m.match_score DESC, m.mapping_id ASC
            LIMIT 18
            """,
            (case_id,),
        ).fetchall()
        company_chunks = conn.execute(
            """
            SELECT chunk_id, chunk_title, chunk_kind, content
            FROM rag_case_chunks
            WHERE case_id = ? AND chunk_kind = 'proposal_company'
            ORDER BY sort_order ASC
            LIMIT 8
            """,
            (case_id,),
        ).fetchall()
        response_chunks = conn.execute(
            """
            SELECT chunk_id, chunk_title, chunk_kind, content
            FROM rag_case_chunks
            WHERE case_id = ? AND chunk_kind = 'proposal_response'
            ORDER BY sort_order ASC
            LIMIT 10
            """,
            (case_id,),
        ).fetchall()

    case_data = _row_to_dict(case)
    case_data["tender_summary"] = _display_text(case_data.get("tender_summary"), 4200)
    case_data["proposal_summary"] = _display_text(case_data.get("proposal_summary"), 4200)
    case_data["company_profile"] = _display_text(case_data.get("company_profile"), 5200)
    case_data["style_profile"] = _display_text(case_data.get("style_profile"), 2600)
    return {
        "case": case_data,
        "documents": [_row_to_dict(row) for row in documents],
        "method_mappings": [
            {
                "mapping_id": int(row["mapping_id"]),
                "match_score": float(row["match_score"] or 0),
                "tender_title": row["tender_title"],
                "tender_kind": row["tender_kind"],
                "tender_content": _display_text(row["tender_content"], 900),
                "proposal_title": row["proposal_title"],
                "proposal_kind": row["proposal_kind"],
                "proposal_content": _display_text(row["proposal_content"], 1200),
            }
            for row in method_rows
            if not _is_display_noise(row["tender_title"], row["tender_content"]) and not _is_display_noise(row["proposal_title"], row["proposal_content"])
        ],
        "company_chunks": [{**_row_to_dict(row), "content": _display_text(row["content"], 1800)} for row in company_chunks if not _is_display_noise(row["chunk_title"], row["content"])],
        "response_chunks": [{**_row_to_dict(row), "content": _display_text(row["content"], 1800)} for row in response_chunks if not _is_display_noise(row["chunk_title"], row["content"])],
    }


def get_case_document(case_id: int, document_id: int, *, proposal_only: bool = False) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM rag_case_documents
            WHERE case_id = ? AND document_id = ?
            """,
            (case_id, document_id),
        ).fetchone()
    if row is None:
        raise RagCaseServiceError("历史案例文件不存在", 404)
    data = _row_to_dict(row)
    if proposal_only and data.get("doc_role") != "proposal":
        raise RagCaseServiceError("只能下载历史标书响应文件", 400)
    path = Path(str(data.get("file_path") or ""))
    if not path.exists() or not path.is_file():
        raise RagCaseServiceError("历史案例原文件不存在，请重新入库", 404)
    data["absolute_path"] = str(path)
    return data


def _save_page_image_to_stream(image: Any) -> io.BytesIO:
    stream = io.BytesIO()
    image.save(stream, format="PNG", quantize=False)
    stream.seek(0)
    return stream


def _draw_highlights_on_page_image(image: Any, page: Any, highlights: list[str]) -> None:
    for term in highlights:
        try:
            matches = page.search(re.escape(term), regex=True, case=False)[:24]
        except Exception:  # noqa: BLE001
            matches = []
        for match in matches:
            image.draw_rect(
                (float(match["x0"]) - 1, float(match["top"]) - 1, float(match["x1"]) + 1, float(match["bottom"]) + 1),
                fill=(255, 230, 90, 92),
                stroke=(226, 179, 36, 150),
                stroke_width=1,
            )


def _render_pymupdf_page(path: Path, page_number: int, highlights: list[str]) -> io.BytesIO | None:
    """Render a PDF page without PDFium (more stable on affected Windows builds)."""
    try:
        import fitz  # PyMuPDF
        from PIL import Image, ImageDraw

        document = fitz.open(str(path))
        try:
            if page_number < 1 or page_number > document.page_count:
                return None
            page = document.load_page(page_number - 1)
            scale = 1.65
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            draw = ImageDraw.Draw(image, "RGBA")
            for term in highlights:
                if len(term) < 2:
                    continue
                # Search returns page coordinates in points; scale to pixels.
                for rect in page.search_for(term)[:24]:
                    box = (rect.x0 * scale - 2, rect.y0 * scale - 2, rect.x1 * scale + 2, rect.y1 * scale + 2)
                    draw.rectangle(box, fill=(255, 226, 68, 95), outline=(220, 165, 20, 180), width=2)
            return _save_page_image_to_stream(image)
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG PyMuPDF render unavailable file=%s page=%s error=%s", path.name, page_number, exc)
        return None


def _render_normalized_pdf_page(path: Path, page_number: int, highlights: list[str]) -> io.BytesIO | None:
    try:
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            # The project declares PyPDF2 on Windows; support both package
            # names so malformed PDFium pages can still be normalized.
            from PyPDF2 import PdfReader, PdfWriter
        import pdfplumber

        reader = PdfReader(str(path))
        if page_number < 1 or page_number > len(reader.pages):
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[page_number - 1])
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        with pdfplumber.open(buffer) as pdf:
            page = pdf.pages[0]
            image = page.to_image(width=1200, antialias=True)
            _draw_highlights_on_page_image(image, page, highlights)
            return _save_page_image_to_stream(image)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG normalized PDF render failed file=%s page=%s error=%s", path.name, page_number, exc)
        return None


def _render_text_fallback_page(file_name: str, page_number: int, text: str, highlights: list[str]) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1200, 1550
    image = Image.new("RGB", (width, height), "#fbfaf7")
    draw = ImageDraw.Draw(image)
    try:
        font_title = ImageFont.truetype("msyh.ttc", 26)
        font_body = ImageFont.truetype("msyh.ttc", 24)
        font_small = ImageFont.truetype("msyh.ttc", 18)
    except Exception:  # noqa: BLE001
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.rectangle((0, 0, width, 76), fill="#f3efe6")
    draw.text((42, 24), f"{file_name}  Page {page_number}", fill="#2f2a22", font=font_title)
    draw.text((42, 96), "PDF page image could not be rendered. Showing extracted evidence text.", fill="#8a7358", font=font_small)

    y = 142
    clean = _display_text(text, 3200) or "No readable text was extracted from this page."
    lines: list[str] = []
    for paragraph in clean.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=42) or [""])
    for line in lines[:46]:
        hit = any(term and term in line for term in highlights)
        if hit:
            draw.rectangle((36, y - 4, width - 36, y + 32), fill="#fff0a8")
        draw.text((48, y), line, fill="#332f2a", font=font_body)
        y += 34
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def render_case_document_page(case_id: int, document_id: int, page_number: int, *, terms: str = "") -> dict[str, Any]:
    data = get_case_document(case_id, document_id)
    page_count = int(data.get("page_count") or 0)
    if page_number < 1 or (page_count and page_number > page_count):
        raise RagCaseServiceError("PDF椤电爜瓒呭嚭鑼冨洿", 400)
    path = Path(str(data["absolute_path"]))
    file_name = str(data.get("file_name") or path.name)
    output_name = f"{Path(file_name).stem}-p{page_number}.png"
    highlights = [
        item.strip()
        for item in re.split(r"[,;|\s]+", terms or "")
        if 2 <= len(item.strip()) <= 24 and item.strip() not in _RAG_REPORT_STOPWORDS
    ][:10]
    pymupdf_image = _render_pymupdf_page(path, page_number, highlights)
    if pymupdf_image is not None:
        return {"stream": pymupdf_image, "file_name": output_name}
    try:
        import pdfplumber
        page_text = ""
        with pdfplumber.open(str(path)) as pdf:
            if page_number > len(pdf.pages):
                raise RagCaseServiceError("PDF椤电爜瓒呭嚭鑼冨洿", 400)
            page = pdf.pages[page_number - 1]
            try:
                image = page.to_image(width=1200, antialias=True)
                _draw_highlights_on_page_image(image, page, highlights)
                return {"stream": _save_page_image_to_stream(image), "file_name": output_name}
            except Exception as exc:  # noqa: BLE001
                logger.warning("RAG direct PDF render failed file=%s page=%s error=%s", path.name, page_number, exc)
                page_text = _clean_text(page.extract_text(x_tolerance=1.5, y_tolerance=3, layout=False) or "")
        normalized = _render_normalized_pdf_page(path, page_number, highlights)
        if normalized is not None:
            return {"stream": normalized, "file_name": output_name}
        return {"stream": _render_text_fallback_page(file_name, page_number, page_text, highlights), "file_name": output_name}
    except RagCaseServiceError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG PDF evidence fallback used case=%s document=%s page=%s error=%s", case_id, document_id, page_number, exc)
        page_text = "\n".join(text for page, text in _cached_pdf_page_texts(str(path)) if page == page_number)
        return {"stream": _render_text_fallback_page(file_name, page_number, page_text, highlights), "file_name": output_name}
