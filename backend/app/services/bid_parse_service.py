from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import sys
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.schemas.bid_parse import BidParseInfoUpdateRequest, MaterialType, ParseResultEditRequest, PasteParseContentRequest
from app.services import bid_reference_service, qwen_client, storage
from app.utils.bid_extractor import extract_bid_info, locate_core_blocks
from app.utils.bid_extractor.ai_extractor import extract_bid_info_with_ai
from app.utils.bid_parser import BidParseDocError, BidParseError, BidParseScannedError, BidParseUnsupportedError, parse_bid_document


UPLOAD_COPY_CHUNK_SIZE = 1024 * 1024
TENDER_EXTENSIONS = {".doc", ".docx", ".pdf"}
PARSE_TIMEOUT_SECONDS = 300
logger = logging.getLogger(__name__)
_ACTIVE_PARSE_TASKS: dict[tuple[str, int, str], set[object]] = {}
_PARSE_TASK_LOCK = threading.Lock()


class BidParseServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _ensure_bid(bid_id: int) -> sqlite3.Row:
    try:
        return storage.ensure_bid_exists(bid_id)
    except FileNotFoundError as exc:
        raise BidParseServiceError(40401, f"标书不存在：{bid_id}", 404) from exc


def _now_text() -> str:
    return storage.now_utc().isoformat()


def _clean_filename(filename: str | None) -> str:
    value = (filename or "file").replace("\\", "/")
    name = Path(value).name.strip()
    return name or "file"


def _relative_data_path(path: Path) -> str:
    data_root = settings.resolve_local_path(settings.data_dir)
    return path.resolve().relative_to(data_root).as_posix()


def _absolute_data_path(path: str | None) -> Path | None:
    if not path:
        return None
    return settings.resolve_local_path(settings.data_dir) / path


def _delete_data_file(path: str | None) -> None:
    target = _absolute_data_path(path)
    if target and target.exists() and target.is_file():
        target.unlink(missing_ok=True)


def _upload_limit_bytes() -> int:
    return settings.bid_max_upload_size_mb * 1024 * 1024


def _copy_upload_to_disk(
    bid_id: int,
    folder: str,
    upload: UploadFile,
    original_name: str,
    suffix: str,
    cancel: threading.Event,
) -> dict[str, Any]:
    """Copy Starlette's spooled upload without materializing the file in RAM."""
    target_dir = settings.upload_dir / "bids" / str(bid_id) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix or '.bin'}"
    partial = target.with_name(target.name + ".part")
    limit = _upload_limit_bytes()
    size = 0
    try:
        relative_path = _relative_data_path(target)
        upload.file.seek(0)
        with partial.open("xb") as output:
            while True:
                if cancel.is_set():
                    raise InterruptedError("upload copy cancelled")
                block = upload.file.read(min(UPLOAD_COPY_CHUNK_SIZE, limit - size + 1))
                if not block:
                    break
                size += len(block)
                if size > limit:
                    raise BidParseServiceError(40002, f"单个文件大小不能超过 {settings.bid_max_upload_size_mb} MiB", 413)
                output.write(block)
        if size == 0:
            raise BidParseServiceError(40001, "上传文件不能为空")
        if cancel.is_set():
            raise InterruptedError("upload copy cancelled")
        partial.replace(target)
        return {"file_path": relative_path, "file_name": original_name, "file_size": size}
    except BaseException as exc:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        if isinstance(exc, (BidParseServiceError, InterruptedError)) or not isinstance(exc, Exception):
            raise
        logger.exception("bid upload write failed bid_id=%s folder=%s", bid_id, folder)
        raise BidParseServiceError(50003, "文件保存失败，请确认磁盘剩余空间后重新上传", 500) from exc


async def _save_upload(
    bid_id: int,
    folder: str,
    upload: UploadFile,
    allowed_suffixes: set[str] | None = None,
) -> dict[str, Any]:
    original_name = _clean_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if allowed_suffixes is not None and suffix not in allowed_suffixes:
        allowed = "、".join(sorted(item.lstrip(".") for item in allowed_suffixes))
        raise BidParseServiceError(40003, f"文件格式不支持，仅支持 {allowed}")
    if upload.size is not None and upload.size > _upload_limit_bytes():
        raise BidParseServiceError(40002, f"单个文件大小不能超过 {settings.bid_max_upload_size_mb} MiB", 413)
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_copy_upload_to_disk, bid_id, folder, upload, original_name, suffix, cancel))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Let the worker close its file before FastAPI closes UploadFile.
        cancel.set()
        try:
            saved = await task
        except Exception:
            pass
        else:
            _delete_data_file(saved["file_path"])
        raise


EXTRACTION_STEPS = (
    ("prepare", "原文定位与规则整理"),
    ("project_overview", "项目概况"),
    ("scoring", "评分办法"),
    ("qualification", "资格要求"),
    ("commercial", "商务要求"),
    ("invalid", "废标/否决条款"),
    ("summary", "要点汇总"),
    ("save", "保存提取结果"),
)


def _new_extraction_progress() -> dict[str, Any]:
    now = _now_text()
    return {
        "status": "running", "completed": 0, "total": len(EXTRACTION_STEPS), "percent": 0,
        "current_key": "prepare", "current_label": EXTRACTION_STEPS[0][1],
        "message": "正在整理原文与规则结果", "started_at": now, "updated_at": now,
        "items": [{"key": key, "label": label, "status": "pending", "attempt": 0} for key, label in EXTRACTION_STEPS],
    }


def _advance_extraction_progress(progress: dict[str, Any], key: str, status: str, attempt: int = 0) -> None:
    item = next((item for item in progress["items"] if item["key"] == key), None)
    if item is None or status not in {"running", "completed", "fallback", "failed"}:
        return
    item.update(status=status, attempt=max(item["attempt"], int(attempt)))
    progress.update(current_key=key, current_label=item["label"], updated_at=_now_text())
    label = item["label"]
    if status == "failed":
        progress.update(status="failed", message=f"{label}未完成，请重试")
    elif status == "fallback":
        progress["message"] = f"{label}已完成，使用规则补充结果"
    elif status == "completed":
        progress["message"] = f"{label}已完成"
        if key == "save" and all(entry["status"] in {"completed", "fallback"} for entry in progress["items"]):
            progress.update(status="completed", message="信息提取已完成并保存")
    else:
        progress["message"] = f"正在重试{label}（第 {item['attempt']} 次尝试）" if item["attempt"] > 1 else f"正在处理{label}"
    progress["completed"] = sum(item["status"] in {"completed", "fallback"} for item in progress["items"])
    percent = progress["completed"] * 100 // progress["total"]
    progress["percent"] = percent if progress["status"] == "completed" else min(99, percent)


def _decode_extraction_progress(value: object) -> dict[str, Any] | None:
    try:
        progress = json.loads(str(value)) if value else None
    except (ValueError, TypeError):
        return None
    return progress if isinstance(progress, dict) and isinstance(progress.get("items"), list) else None


def _public_extraction_progress(value: object, parse_status: object, updated_at: object = None) -> dict[str, Any] | None:
    progress = _decode_extraction_progress(value)
    if progress is None:
        return None
    # A final telemetry write may fail after the result transaction commits.
    # The committed parse status is authoritative for the final save item.
    if parse_status == "completed" and progress.get("status") != "completed":
        other_items = [item for item in progress["items"] if item.get("key") != "save"]
        if not all(item.get("status") in {"completed", "fallback"} for item in other_items):
            return None
        _advance_extraction_progress(progress, "save", "completed", 1)
        progress["updated_at"] = str(updated_at or progress["updated_at"])
    elif parse_status == "failed" and progress.get("status") != "failed":
        _advance_extraction_progress(progress, str(progress.get("current_key") or "prepare"), "failed")
        progress["updated_at"] = str(updated_at or progress["updated_at"])
    return progress


def _write_extraction_progress(bid_id: int, file_path: str, serialized: str, expected_status: str) -> None:
    with closing(storage.connect()) as conn:
        conn.execute(
            "UPDATE bid_parse_info SET extraction_progress = ?, update_time = ? WHERE bid_id = ? AND tender_file_path = ? AND parse_status = ?",
            (serialized, _now_text(), bid_id, file_path, expected_status),
        )
        conn.commit()


async def _report_extraction_progress(bid_id: int, file_path: str, progress: dict[str, Any], event: dict[str, Any]) -> None:
    _advance_extraction_progress(progress, str(event["key"]), str(event["status"]), int(event.get("attempt") or 0))
    serialized = json.dumps(progress, ensure_ascii=False)
    expected = "completed" if progress["status"] == "completed" else "processing"
    try:
        await asyncio.to_thread(_write_extraction_progress, bid_id, file_path, serialized, expected)
    except Exception:
        logger.warning("could not persist extraction progress bid_id=%s", bid_id)


def _parse_info_row_to_dict(row: sqlite3.Row | None, bid_id: int) -> dict[str, Any]:
    if row is None:
        return {
            "parse_id": None,
            "bid_id": bid_id,
            "parse_mode": None,
            "tender_file_path": None,
            "tender_file_name": None,
            "tender_file_size": None,
            "paste_content": None,
            "bid_category": "service",
            "parsed_project_overview": None,
            "parsed_score_requirements": None,
            "parsed_tech_scoring": None,
            "parsed_biz_requirements": None,
            "parsed_invalid_clauses": None,
            "raw_full_text": None,
            "raw_markdown": None,
            "full_markdown": None,
            "chapter_blocks": None,
            "project_basic_info": None,
            "tech_scoring_items": None,
            "biz_scoring_items": None,
            "qualification_requirements": None,
            "invalid_bid_clauses": None,
            "bid_key_points": None,
            "parse_current_step": None,
            "parse_status": "pending",
            "parse_error": None,
            "parse_error_msg": None,
            "parse_warning_msg": None,
            "extraction_progress": None,
            "create_time": None,
            "update_time": None,
        }
    result = {key: row[key] for key in row.keys()}
    result["extraction_progress"] = _public_extraction_progress(result.get("extraction_progress"), result.get("parse_status"), result.get("update_time"))
    return result


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _json_dumps(value: object) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2)


def _json_loads(value: object, fallback: object | None = None) -> Any:
    if fallback is None:
        fallback = {}
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _parse_error_code(value: object) -> int | None:
    try:
        code = int(str(value or ""))
    except ValueError:
        return None
    return code if code >= 40000 else None


def get_parse_info(bid_id: int) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    with storage.connect() as conn:
        parse_row = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        list_rows = conn.execute(
            "SELECT * FROM bid_list_files WHERE bid_id = ? ORDER BY list_id ASC",
            (bid_id,),
        ).fetchall()
        ref_rows = conn.execute(
            """
            SELECT ref_id, bid_id, material_type, file_path, file_name, file_size,
                   company_profile, style_profile, parse_status, parse_error, create_time, update_time
            FROM bid_reference_materials WHERE bid_id = ? ORDER BY ref_id ASC
            """,
            (bid_id,),
        ).fetchall()

    parse_info = _parse_info_row_to_dict(parse_row, bid_id)
    tender_file = None
    if parse_info.get("tender_file_path"):
        tender_file = {
            "file_path": parse_info["tender_file_path"],
            "file_name": parse_info["tender_file_name"],
            "file_size": parse_info["tender_file_size"],
        }

    return {
        "bid_id": bid_id,
        "current_step": int(bid["current_step"]),
        **parse_info,
        "tender_file": tender_file,
        "list_files": [_row_to_dict(row) for row in list_rows],
        "reference_materials": [_row_to_dict(row) for row in ref_rows],
    }


def _get_parse_status_info(bid_id: int) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    mysql = (settings.db_backend or "sqlite").strip().lower() == "mysql"
    length = "CHAR_LENGTH" if mysql else "LENGTH"
    array_length = "JSON_LENGTH" if mysql else "JSON_ARRAY_LENGTH"
    with closing(storage.connect()) as conn:
        row = conn.execute(
            f"""
            SELECT parse_mode, parse_status, parse_current_step, tender_file_path,
                   tender_file_name, tender_file_size, parse_error, parse_error_msg,
                   parse_warning_msg, update_time, extraction_progress,
                   {length}(COALESCE(raw_full_text, '')) AS raw_full_text_length,
                   {length}(COALESCE(raw_markdown, '')) AS raw_markdown_length,
                   {length}(COALESCE(full_markdown, '')) AS full_markdown_length,
                   CASE WHEN JSON_VALID(chapter_blocks) THEN COALESCE({array_length}(chapter_blocks), 0) ELSE 0 END AS chapter_block_count,
                   CASE WHEN {length}(TRIM(COALESCE(project_basic_info, ''))) > 0
                          OR {length}(TRIM(COALESCE(tech_scoring_items, ''))) > 0
                          OR {length}(TRIM(COALESCE(bid_key_points, ''))) > 0 THEN 1 ELSE 0 END AS has_ai_detail,
                   SUBSTR(COALESCE(parsed_project_overview, ''), 1, 160) AS project_overview_preview,
                   SUBSTR(COALESCE(parsed_score_requirements, ''), 1, 160) AS score_requirements_preview,
                   CASE WHEN {length}(TRIM(COALESCE(parsed_project_overview, ''))) > 0
                          AND {length}(TRIM(COALESCE(parsed_score_requirements, ''))) > 0 THEN 1 ELSE 0 END AS has_extracted_result
            FROM bid_parse_info WHERE bid_id = ?
            """,
            (bid_id,),
        ).fetchone()
    result = _row_to_dict(row) if row is not None else _parse_info_row_to_dict(None, bid_id)
    result["current_step"] = int(bid["current_step"])
    return result


def get_parse_status(bid_id: int) -> dict[str, Any]:
    info = _get_parse_status_info(bid_id)
    if info.get("parse_status") == "processing" and _parse_is_stale(info.get("update_time")):
        file_path = str(info.get("tender_file_path") or "")
        with _PARSE_TASK_LOCK:
            active = bool(_ACTIVE_PARSE_TASKS.get(_parse_task_key(bid_id, file_path)))
        if file_path and not active:
            _mark_parse_failed(bid_id, file_path, "解析超时，请重新上传", 50002)
            info = _get_parse_status_info(bid_id)
    return {
        "bid_id": bid_id,
        "parse_mode": info.get("parse_mode"),
        "parse_status": info.get("parse_status"),
        "parse_current_step": info.get("parse_current_step"),
        "parse_step_label": _parse_step_label(info.get("parse_current_step"), info.get("parse_status")),
        "tender_file_name": info.get("tender_file_name"),
        "tender_file_size": info.get("tender_file_size"),
        "parse_error_msg": info.get("parse_error_msg") or info.get("parse_error"),
        "parse_error_code": _parse_error_code(info.get("parse_error")),
        "parse_warning_msg": info.get("parse_warning_msg"),
        "raw_full_text_length": int(info.get("raw_full_text_length") or 0),
        "raw_markdown_length": int(info.get("raw_markdown_length") or 0),
        "full_markdown_length": int(info.get("full_markdown_length") or 0),
        "chapter_block_count": int(info.get("chapter_block_count") or 0),
        "has_ai_detail": bool(info.get("has_ai_detail")),
        "project_overview_preview": info.get("project_overview_preview") or "",
        "score_requirements_preview": info.get("score_requirements_preview") or "",
        "has_extracted_result": bool(info.get("has_extracted_result")),
        "current_step": info.get("current_step"),
        "update_time": info.get("update_time"),
        "extraction_progress": _public_extraction_progress(info.get("extraction_progress"), info.get("parse_status"), info.get("update_time")),
    }


def _extract_project_number(basic: dict[str, Any], raw_text: str) -> str:
    for key in ("project_number", "project_no", "project_code", "项目编号", "采购项目编号"):
        value = str(basic.get(key) or "").strip()
        if value:
            return value
    values = set(re.findall(r"(?:采购)?项目编号\s*[：:]\s*([A-Za-z0-9][A-Za-z0-9_./-]{3,59})", (raw_text or "")[:30000]))
    return next(iter(values)) if len(values) == 1 else ""


def get_parse_detail(bid_id: int) -> dict[str, Any]:
    info = get_parse_info(bid_id)
    basic = _json_loads(info.get("project_basic_info"), {})
    if not isinstance(basic, dict):
        basic = {}
    return {
        "bid_id": bid_id,
        "parse_mode": info.get("parse_mode"),
        "parse_status": info.get("parse_status"),
        "tender_file_name": info.get("tender_file_name"),
        "parsed_project_overview": info.get("parsed_project_overview") or "",
        "parsed_score_requirements": info.get("parsed_score_requirements") or "",
        "project_basic_info": basic,
        "project_number": _extract_project_number(basic, str(info.get("raw_full_text") or "")),
        "tech_scoring_items": _json_loads(info.get("tech_scoring_items"), []),
        "biz_scoring_items": _json_loads(info.get("biz_scoring_items"), []),
        "qualification_requirements": _json_loads(info.get("qualification_requirements"), []),
        "invalid_bid_clauses": _json_loads(info.get("invalid_bid_clauses"), []),
        "bid_key_points": _json_loads(info.get("bid_key_points"), {}),
        "chapter_block_count": len(_json_loads(info.get("chapter_blocks"), [])),
        "full_markdown_length": len(info.get("full_markdown") or info.get("raw_markdown") or ""),
        "parse_warning_msg": info.get("parse_warning_msg"),
        "update_time": info.get("update_time"),
    }


def find_duplicate_parse_file(bid_id: int, tender_file_name: str, tender_file_size: int | None = None) -> dict[str, Any]:
    _ensure_bid(bid_id)
    name = _clean_filename(tender_file_name).lower()
    if not name:
        return {"matches": []}
    clauses = ["LOWER(pi.tender_file_name) = ?", "pi.bid_id <> ?"]
    params: list[Any] = [name, bid_id]
    if tender_file_size and tender_file_size > 0:
        clauses.append("(pi.tender_file_size = ? OR pi.tender_file_size IS NULL)")
        params.append(tender_file_size)
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT pi.bid_id, pi.tender_file_name, pi.tender_file_size, pi.parse_status, pi.update_time,
                   b.project_id, b.bid_title AS project_title
            FROM bid_parse_info pi
            JOIN bids b ON b.bid_id = pi.bid_id
            WHERE {' AND '.join(clauses)}
            ORDER BY pi.update_time DESC
            LIMIT 8
            """,
            params,
        ).fetchall()
    return {
        "matches": [
            {
                "bid_id": int(row["bid_id"]),
                "project_id": row["project_id"],
                "project_title": row["project_title"],
                "tender_file_name": row["tender_file_name"],
                "tender_file_size": row["tender_file_size"],
                "parse_status": row["parse_status"],
                "update_time": row["update_time"],
            }
            for row in rows
        ]
    }


def merge_duplicate_parse(target_bid_id: int, source_bid_id: int) -> dict[str, Any]:
    if target_bid_id == source_bid_id:
        return get_parse_info(target_bid_id)
    target = get_parse_info(target_bid_id)
    source = get_parse_info(source_bid_id)
    now = _now_text()

    def merged_text(field: str) -> str | None:
        return _merge_text_values(target.get(field), source.get(field))

    def merged_json(field: str) -> str | None:
        merged = _merge_json_values(target.get(field), source.get(field))
        return _json_dumps(merged) if merged not in ({}, [], None, "") else (source.get(field) or target.get(field))

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO bid_parse_info (
                bid_id, parse_mode, tender_file_path, tender_file_name, tender_file_size,
                paste_content, bid_category, parsed_project_overview, parsed_score_requirements,
                raw_full_text, raw_markdown, full_markdown, chapter_blocks,
                parsed_tech_scoring, parsed_biz_requirements, parsed_invalid_clauses,
                project_basic_info, tech_scoring_items, biz_scoring_items,
                qualification_requirements, invalid_bid_clauses, bid_key_points,
                parse_current_step, parse_status, parse_error, parse_error_msg, parse_warning_msg,
                create_time, update_time
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 'completed', NULL, NULL, ?, ?, ?)
            ON CONFLICT(bid_id) DO UPDATE SET
                parse_mode = excluded.parse_mode,
                tender_file_path = excluded.tender_file_path,
                tender_file_name = excluded.tender_file_name,
                tender_file_size = excluded.tender_file_size,
                bid_category = excluded.bid_category,
                parsed_project_overview = excluded.parsed_project_overview,
                parsed_score_requirements = excluded.parsed_score_requirements,
                raw_full_text = excluded.raw_full_text,
                raw_markdown = excluded.raw_markdown,
                full_markdown = excluded.full_markdown,
                chapter_blocks = excluded.chapter_blocks,
                parsed_tech_scoring = excluded.parsed_tech_scoring,
                parsed_biz_requirements = excluded.parsed_biz_requirements,
                parsed_invalid_clauses = excluded.parsed_invalid_clauses,
                project_basic_info = excluded.project_basic_info,
                tech_scoring_items = excluded.tech_scoring_items,
                biz_scoring_items = excluded.biz_scoring_items,
                qualification_requirements = excluded.qualification_requirements,
                invalid_bid_clauses = excluded.invalid_bid_clauses,
                bid_key_points = excluded.bid_key_points,
                extraction_progress = NULL,
                parse_current_step = 4,
                parse_status = 'completed',
                parse_error = NULL,
                parse_error_msg = NULL,
                parse_warning_msg = excluded.parse_warning_msg,
                update_time = excluded.update_time
            """,
            (
                target_bid_id,
                source.get("parse_mode") or target.get("parse_mode") or "auto_upload",
                source.get("tender_file_path") or target.get("tender_file_path"),
                source.get("tender_file_name") or target.get("tender_file_name"),
                source.get("tender_file_size") or target.get("tender_file_size"),
                target.get("bid_category") or source.get("bid_category") or "service",
                merged_text("parsed_project_overview"),
                merged_text("parsed_score_requirements"),
                merged_text("raw_full_text"),
                merged_text("raw_markdown"),
                merged_text("full_markdown"),
                merged_json("chapter_blocks"),
                merged_text("parsed_tech_scoring"),
                merged_text("parsed_biz_requirements"),
                merged_text("parsed_invalid_clauses"),
                merged_json("project_basic_info"),
                merged_json("tech_scoring_items"),
                merged_json("biz_scoring_items"),
                merged_json("qualification_requirements"),
                merged_json("invalid_bid_clauses"),
                merged_json("bid_key_points"),
                "已合并同名历史解析记录，保留差异内容。",
                now,
                now,
            ),
        )
        conn.commit()
    return get_parse_info(target_bid_id)


def _merge_text_values(a: object, b: object) -> str | None:
    left = str(a or "").strip()
    right = str(b or "").strip()
    if not left:
        return right or None
    if not right or right in left:
        return left
    if left in right:
        return right
    blocks = []
    seen: set[str] = set()
    for part in re.split(r"\n{2,}", f"{left}\n\n{right}"):
        clean = part.strip()
        key = re.sub(r"\s+", "", clean)[:160]
        if clean and key not in seen:
            seen.add(key)
            blocks.append(clean)
    return "\n\n".join(blocks) or None


def _merge_json_values(a: object, b: object) -> Any:
    left = _json_loads(a, [] if str(a or b or "").strip().startswith("[") else {})
    right = _json_loads(b, [] if str(a or b or "").strip().startswith("[") else {})
    if isinstance(left, list) or isinstance(right, list):
        merged: list[Any] = []
        seen: set[str] = set()
        for item in (left if isinstance(left, list) else []) + (right if isinstance(right, list) else []):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        return merged
    if isinstance(left, dict) or isinstance(right, dict):
        merged_dict = dict(left if isinstance(left, dict) else {})
        for key, value in (right if isinstance(right, dict) else {}).items():
            if key not in merged_dict or not merged_dict[key]:
                merged_dict[key] = value
        return merged_dict
    return right or left


def _parse_task_key(bid_id: int, file_path: str) -> tuple[str, int, str]:
    tenant = get_current_tenant()
    return (tenant.company_id if tenant else "company_default", int(bid_id), file_path)


def _parse_is_stale(update_time: object) -> bool:
    if not update_time:
        return False
    try:
        updated = datetime.fromisoformat(str(update_time))
    except (AttributeError, TypeError, ValueError):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (storage.now_utc() - updated).total_seconds() > PARSE_TIMEOUT_SECONDS


def _parse_step_label(step: object, status: object) -> str:
    if status == "failed":
        return "解析失败"
    labels = {1: "文件上传", 2: "文档解析", 3: "信息提取", 4: "解析完成"}
    try:
        value = int(step or 0)
    except (TypeError, ValueError):
        value = 0
    return labels.get(value, "等待解析")


def save_parse_info(bid_id: int, payload: BidParseInfoUpdateRequest) -> dict[str, Any]:
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        current = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        field_set = payload.model_fields_set
        parse_mode = payload.parse_mode if "parse_mode" in field_set else (current["parse_mode"] if current else None)
        paste_content = payload.paste_content if "paste_content" in field_set else (current["paste_content"] if current else None)
        bid_category = payload.bid_category if "bid_category" in field_set else (current["bid_category"] if current else "service")
        has_structured_paste = bool(current and (current["parsed_project_overview"] or "").strip() and (current["parsed_score_requirements"] or "").strip())

        if parse_mode == "paste_text" and not (paste_content or "").strip() and not has_structured_paste:
            raise BidParseServiceError(40001, "选择粘贴文本解析时，项目信息文本不能为空")

        old_file_path = current["tender_file_path"] if current and parse_mode == "paste_text" else None

        now = _now_text()
        cursor = conn.execute(
            """
            INSERT INTO bid_parse_info (
                bid_id, parse_mode, paste_content, bid_category, parse_status, create_time, update_time
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(bid_id) DO UPDATE SET
                parse_mode = excluded.parse_mode,
                paste_content = excluded.paste_content,
                bid_category = excluded.bid_category,
                parsed_project_overview = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.parsed_project_overview
                END,
                parsed_score_requirements = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.parsed_score_requirements
                END,
                tender_file_path = CASE WHEN excluded.parse_mode = 'paste_text' THEN NULL ELSE bid_parse_info.tender_file_path END,
                tender_file_name = CASE WHEN excluded.parse_mode = 'paste_text' THEN NULL ELSE bid_parse_info.tender_file_name END,
                tender_file_size = CASE WHEN excluded.parse_mode = 'paste_text' THEN NULL ELSE bid_parse_info.tender_file_size END,
                raw_full_text = CASE
                    WHEN excluded.parse_mode = 'paste_text' THEN NULL
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.raw_full_text
                END,
                raw_markdown = CASE
                    WHEN excluded.parse_mode = 'paste_text' THEN NULL
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.raw_markdown
                END,
                full_markdown = CASE
                    WHEN excluded.parse_mode = 'paste_text' THEN NULL
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.full_markdown
                END,
                chapter_blocks = CASE
                    WHEN excluded.parse_mode = 'paste_text' THEN NULL
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') != 'auto_upload' THEN NULL
                    ELSE bid_parse_info.chapter_blocks
                END,
                project_basic_info = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.project_basic_info ELSE bid_parse_info.project_basic_info END,
                tech_scoring_items = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.tech_scoring_items ELSE bid_parse_info.tech_scoring_items END,
                biz_scoring_items = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.biz_scoring_items ELSE bid_parse_info.biz_scoring_items END,
                qualification_requirements = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.qualification_requirements ELSE bid_parse_info.qualification_requirements END,
                invalid_bid_clauses = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.invalid_bid_clauses ELSE bid_parse_info.invalid_bid_clauses END,
                bid_key_points = CASE WHEN excluded.parse_mode = 'paste_text' THEN bid_parse_info.bid_key_points ELSE bid_parse_info.bid_key_points END,
                extraction_progress = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') = 'auto_upload'
                    THEN bid_parse_info.extraction_progress ELSE NULL END,
                parse_status = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') = 'auto_upload'
                    THEN bid_parse_info.parse_status
                    WHEN excluded.parse_mode != bid_parse_info.parse_mode
                      OR COALESCE(excluded.paste_content, '') != COALESCE(bid_parse_info.paste_content, '')
                      OR excluded.parse_mode = 'auto_upload'
                    THEN 'pending'
                    ELSE bid_parse_info.parse_status
                END,
                parse_error = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') = 'auto_upload'
                    THEN bid_parse_info.parse_error
                    ELSE NULL
                END,
                parse_error_msg = CASE
                    WHEN excluded.parse_mode = 'auto_upload' AND COALESCE(bid_parse_info.parse_mode, '') = 'auto_upload'
                    THEN bid_parse_info.parse_error_msg
                    ELSE NULL
                END,
                update_time = excluded.update_time
            """,
            (bid_id, parse_mode, paste_content, bid_category, now, now),
        )
        conn.commit()
    if old_file_path:
        _delete_data_file(old_file_path)
    return get_parse_info(bid_id)


async def upload_tender_file(bid_id: int, upload: UploadFile) -> dict[str, Any]:
    _ensure_bid(bid_id)
    saved = await _save_upload(bid_id, "tender", upload, TENDER_EXTENSIONS)
    now = _now_text()
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO bid_parse_info (
                bid_id, parse_mode, tender_file_path, tender_file_name, tender_file_size,
                bid_category, parse_status, create_time, update_time
            ) VALUES (?, 'auto_upload', ?, ?, ?, 'service', 'pending', ?, ?)
            ON CONFLICT(bid_id) DO UPDATE SET
                parse_mode = 'auto_upload',
                tender_file_path = excluded.tender_file_path,
                tender_file_name = excluded.tender_file_name,
                tender_file_size = excluded.tender_file_size,
                paste_content = NULL,
                parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_status = 'pending',
                parse_error = NULL,
                parse_error_msg = NULL,
                update_time = excluded.update_time
            """,
            (bid_id, saved["file_path"], saved["file_name"], saved["file_size"], now, now),
        )
        conn.commit()
    return {"bid_id": bid_id, **saved, "parse_info": get_parse_info(bid_id)}


async def upload_and_start_parse(bid_id: int, upload: UploadFile) -> dict[str, Any]:
    _ensure_bid(bid_id)
    saved = await _save_upload(bid_id, "tender", upload, TENDER_EXTENSIONS)
    now = _now_text()
    old_file_path = None
    with storage.connect() as conn:
        current = conn.execute("SELECT tender_file_path FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        old_file_path = current["tender_file_path"] if current else None
        conn.execute(
            """
            INSERT INTO bid_parse_info (
                bid_id, parse_mode, tender_file_path, tender_file_name, tender_file_size,
                paste_content, bid_category, parsed_project_overview, parsed_score_requirements,
                raw_full_text, raw_markdown, parsed_tech_scoring, parsed_biz_requirements, parsed_invalid_clauses,
                parse_current_step, parse_status, parse_error, parse_error_msg, create_time, update_time
            ) VALUES (?, 'auto_upload', ?, ?, ?, NULL, 'service', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1, 'processing', NULL, NULL, ?, ?)
            ON CONFLICT(bid_id) DO UPDATE SET
                parse_mode = 'auto_upload',
                tender_file_path = excluded.tender_file_path,
                tender_file_name = excluded.tender_file_name,
                tender_file_size = excluded.tender_file_size,
                paste_content = NULL,
                parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                parsed_tech_scoring = NULL,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_current_step = 1,
                parse_status = 'processing',
                parse_error = NULL,
                parse_error_msg = NULL,
                parse_warning_msg = NULL,
                update_time = excluded.update_time
            """,
            (bid_id, saved["file_path"], saved["file_name"], saved["file_size"], now, now),
        )
        conn.commit()
    if old_file_path and old_file_path != saved["file_path"]:
        _delete_data_file(old_file_path)
    return {"bid_id": bid_id, **saved, "parse_status": "processing", "parse_info": get_parse_info(bid_id)}


async def _run_auto_parse_task_impl(bid_id: int, file_path: str) -> None:
    progress: dict[str, Any] | None = None
    absolute_path = _absolute_data_path(file_path)
    if absolute_path is None:
        _mark_parse_failed(bid_id, file_path, "招标文件路径无效")
        return

    try:
        print(f"[BidParse] bid_id={bid_id} stage=document_parse file={file_path}", flush=True)
        _update_parse_progress(bid_id, file_path, 2)
        parsed_document = await asyncio.wait_for(
            asyncio.to_thread(parse_bid_document, str(absolute_path)),
            timeout=PARSE_TIMEOUT_SECONDS,
        )
        raw_text = parsed_document.raw_text
        raw_markdown = parsed_document.raw_markdown
        full_markdown = parsed_document.full_markdown or raw_markdown
        chapter_blocks = parsed_document.chapter_blocks or []
        print(
            f"[BidParse] bid_id={bid_id} document_parse_done raw_chars={len(raw_text or '')} "
            f"markdown_chars={len(full_markdown or '')} blocks={len(chapter_blocks)}",
            flush=True,
        )
        _update_parse_progress(bid_id, file_path, 3)
        progress = _new_extraction_progress()

        async def report(event: dict[str, Any]) -> None:
            await _report_extraction_progress(bid_id, file_path, progress, event)

        await report({"key": "prepare", "status": "running", "attempt": 1})
        try:
            bid_category = _get_bid_category(bid_id)
            rule_result = await asyncio.to_thread(extract_bid_info, full_markdown or raw_markdown or raw_text, bid_category)
            located_blocks = await asyncio.to_thread(locate_core_blocks, chapter_blocks, full_markdown or raw_markdown or raw_text)
            await report({"key": "prepare", "status": "completed", "attempt": 1})
            print(
                f"[BidParse] bid_id={bid_id} stage=qwen_ai_extract enabled={settings.enable_llm_parse_optimize} "
                f"api_key_configured={bool(settings.qwen_api_key.strip())} categories={list((located_blocks.get('categories') or {}).keys())}",
                flush=True,
            )
            extracted = await extract_bid_info_with_ai(
                full_markdown=full_markdown or raw_markdown or raw_text,
                chapter_blocks=chapter_blocks,
                located_blocks=located_blocks,
                rule_result=rule_result,
                bid_type=bid_category,
                progress_callback=report,
            )
            print(
                f"[BidParse] bid_id={bid_id} qwen_ai_extract_done used_api={bool(extracted.get('used_api'))} "
                f"tech_items={len(extracted.get('tech_scoring_items') or [])} "
                f"biz_items={len(extracted.get('biz_scoring_items') or [])} "
                f"degraded={extracted.get('degraded_categories') or []}",
                flush=True,
            )
        except Exception:
            logger.exception("AI structured extraction failed bid_id=%s file=%s; falling back to rules", bid_id, file_path)
            print(f"[BidParse] bid_id={bid_id} qwen_ai_extract_failed fallback=rules", flush=True)
            rule_result = await asyncio.to_thread(extract_bid_info, full_markdown or raw_markdown or raw_text, _get_bid_category(bid_id))
            extracted = _structured_result_from_rules(rule_result)
            for key, _ in EXTRACTION_STEPS[:-1]:
                await report({"key": key, "status": "fallback", "attempt": 0})
    except asyncio.TimeoutError:
        _mark_parse_failed(bid_id, file_path, "文件解析超时，请稍后重试或上传更小的文件", 50002)
        return
    except BidParseScannedError as exc:
        _mark_parse_failed(bid_id, file_path, str(exc), exc.code)
        return
    except BidParseDocError as exc:
        _mark_parse_failed(bid_id, file_path, str(exc), exc.code)
        return
    except BidParseUnsupportedError as exc:
        _mark_parse_failed(bid_id, file_path, str(exc), exc.code)
        return
    except BidParseError as exc:
        _mark_parse_failed(bid_id, file_path, str(exc), getattr(exc, "code", 50001))
        return
    except Exception as exc:  # noqa: BLE001 - parser failures must be persisted.
        _mark_parse_failed(bid_id, file_path, f"招标文件解析失败：{exc}", 50001)
        return

    warning = None
    if len((raw_text or "").strip()) < 500:
        warning = "检测到文件可能为扫描版，提取内容有限，建议手动补充"
    if not extracted.get("used_api"):
        warning = warning or "千问模型未实际调用成功，已降级为规则提取；请检查 QWEN_API_KEY、模型权限或网络后重新解析"
    elif extracted.get("degraded_categories"):
        warning = warning or "AI 解读部分发生降级，已保留可用结果，建议检查解析内容"

    project_overview = extracted.get("parsed_project_overview") or extracted.get("project_overview") or ""
    score_requirements = extracted.get("parsed_score_requirements") or extracted.get("score_requirements") or ""
    tech_items = extracted.get("tech_scoring_items") or []
    biz_items = extracted.get("biz_scoring_items") or []
    qualifications = extracted.get("qualification_requirements") or []
    invalid_clauses = extracted.get("invalid_bid_clauses") or []
    bid_key_points = extracted.get("bid_key_points") or {}
    if not (project_overview or "").strip() or not (score_requirements or "").strip():
        warning = warning or "提取内容较少，建议在解析结果中手动补充"
    await report({"key": "save", "status": "running", "attempt": 1})
    now = _now_text()
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE bid_parse_info
            SET raw_full_text = ?,
                raw_markdown = ?,
                full_markdown = ?,
                chapter_blocks = ?,
                parsed_project_overview = ?,
                parsed_score_requirements = ?,
                parsed_tech_scoring = ?,
                parsed_biz_requirements = ?,
                parsed_invalid_clauses = ?,
                project_basic_info = ?,
                tech_scoring_items = ?,
                biz_scoring_items = ?,
                qualification_requirements = ?,
                invalid_bid_clauses = ?,
                bid_key_points = ?,
                parse_current_step = 4,
                parse_status = 'completed',
                parse_error = NULL,
                parse_error_msg = NULL,
                parse_warning_msg = ?,
                update_time = ?
            WHERE bid_id = ? AND tender_file_path = ? AND parse_status = 'processing'
            """,
            (
                raw_text,
                raw_markdown,
                full_markdown,
                _json_dumps(chapter_blocks),
                project_overview,
                score_requirements,
                _json_dumps(tech_items),
                _json_dumps({"biz_scoring_items": biz_items, "qualification_requirements": qualifications}),
                _json_dumps(invalid_clauses),
                _json_dumps(extracted.get("project_basic_info") or {}),
                _json_dumps(tech_items),
                _json_dumps(biz_items),
                _json_dumps(qualifications),
                _json_dumps(invalid_clauses),
                _json_dumps(bid_key_points),
                warning,
                now,
                bid_id,
                file_path,
            ),
        )
        conn.commit()
        saved = cursor.rowcount == 1
    if not saved:
        return  # A replaced file or manually completed record owns the result now.
    await report({"key": "save", "status": "completed", "attempt": 1})
    print(
        f"[BidParse] bid_id={bid_id} parse_completed used_api={bool(extracted.get('used_api'))} warning={warning or ''}",
        flush=True,
    )


async def run_auto_parse_task(bid_id: int, file_path: str) -> None:
    """Track live work so long AI extraction is not mistaken for an orphan."""
    key = _parse_task_key(bid_id, file_path)
    token = object()
    with _PARSE_TASK_LOCK:
        if _ACTIVE_PARSE_TASKS.get(key):
            return
        _ACTIVE_PARSE_TASKS.setdefault(key, set()).add(token)
    try:
        await _run_auto_parse_task_impl(bid_id, file_path)
    except asyncio.CancelledError:
        _safe_mark_parse_failed(bid_id, file_path, "解析任务已中断，请重试", 50002)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled background parse failure bid_id=%s file=%s", bid_id, file_path)
        try:
            _mark_parse_failed(bid_id, file_path, f"招标文件解析失败：{exc}", 50001)
        except Exception:
            logger.exception("failed to persist background parse failure bid_id=%s file=%s", bid_id, file_path)
    finally:
        with _PARSE_TASK_LOCK:
            active = _ACTIVE_PARSE_TASKS.get(key)
            if active is not None:
                active.discard(token)
                if not active:
                    _ACTIVE_PARSE_TASKS.pop(key, None)


def _update_parse_progress(bid_id: int, file_path: str, step: int) -> None:
    now = _now_text()
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE bid_parse_info
            SET parse_current_step = ?, parse_status = 'processing', update_time = ?
            WHERE bid_id = ? AND tender_file_path = ?
            """,
            (step, now, bid_id, file_path),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("解析记录不存在或已被新上传文件替换")
        conn.commit()


def _get_bid_category(bid_id: int) -> str:
    with storage.connect() as conn:
        row = conn.execute("SELECT bid_category FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
    return str(row["bid_category"] if row and row["bid_category"] else "service")


def _structured_result_from_rules(rule_result: dict[str, Any]) -> dict[str, Any]:
    tech_items = _items_from_json_text(rule_result.get("parsed_tech_scoring")) or _text_items(rule_result.get("score_requirements"))
    biz_scoring_items = rule_result.get("biz_scoring_items") or []
    requirement_items = _items_from_json_text(rule_result.get("parsed_biz_requirements")) or _text_items(rule_result.get("biz_requirements_text"))
    invalid_items = _items_from_json_text(rule_result.get("parsed_invalid_clauses")) or _text_items(rule_result.get("invalid_clauses_text"))
    project_basic_info = {"overview": rule_result.get("project_overview") or ""}
    return {
        "project_basic_info": project_basic_info,
        "tech_scoring_items": tech_items,
        "biz_scoring_items": biz_scoring_items,
        "qualification_requirements": requirement_items,
        "invalid_bid_clauses": invalid_items,
        "bid_key_points": {
            "core_points": [item.get("title") or item.get("text") for item in tech_items[:5]],
            "high_score_priorities": [item.get("title") or item.get("text") for item in tech_items[:5]],
            "catalog_suggestions": [item.get("title") or item.get("text") for item in tech_items[:5]],
            "invalid_risks": [item.get("title") or item.get("text") for item in invalid_items[:5]],
        },
        "used_api": False,
        "degraded_categories": ["all"],
        "parsed_project_overview": rule_result.get("project_overview") or "",
        "parsed_score_requirements": rule_result.get("score_requirements") or "",
    }


async def _legacy_extract_and_optionally_optimize(raw_text: str, bid_category: str) -> dict[str, Any]:
    extracted = extract_bid_info(raw_text, bid_category)
    if not settings.enable_llm_parse_optimize:
        return extracted

    fallback = json.dumps(
        {
            "project_overview": extracted.get("project_overview") or "",
            "score_requirements": extracted.get("score_requirements") or "",
        },
        ensure_ascii=False,
    )
    result = await qwen_client.chat_completion(
        [
            {"role": "system", "content": "你是招标文件信息提取助手，只返回JSON。"},
            {
                "role": "user",
                "content": (
                    "请基于以下规则提取结果和原文片段，优化项目概述和评分要求表达。"
                    "返回JSON字段：project_overview、score_requirements。\n\n"
                    f"规则结果：{fallback}\n\n原文片段：{raw_text[:6000]}"
                ),
            },
        ], scene="tender_parse",
        temperature=0.1,
        max_tokens=1800,
        fallback=fallback,
    )
    try:
        optimized = json.loads(result.content)
    except Exception:
        return extracted
    if isinstance(optimized, dict):
        extracted["project_overview"] = str(optimized.get("project_overview") or extracted.get("project_overview") or "")
        extracted["score_requirements"] = str(optimized.get("score_requirements") or extracted.get("score_requirements") or "")
    return extracted


async def _extract_and_optionally_optimize(markdown_text: str, bid_category: str, plain_text: str | None = None) -> dict[str, Any]:
    source = (markdown_text or plain_text or "").strip()
    extracted = extract_bid_info(source, bid_category)
    if not settings.enable_llm_parse_optimize:
        return extracted

    fallback_payload = _rules_to_llm_payload(extracted)
    fallback = json.dumps(fallback_payload, ensure_ascii=False, indent=2)
    candidates = _build_llm_candidate_context(source, extracted)
    result = await qwen_client.chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是专业的招标文件解析助手。你必须严格基于原文提取信息，禁止编造。"
                    "只返回合法 JSON，不要输出 Markdown 代码块或解释文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请从招标文件 Markdown 中做语义级结构化提取。重点识别表格、段落、非标准表述中的评审项。\n"
                    "返回 JSON 字段必须为：\n"
                    "{\n"
                    "  \"project_overview\": \"项目概述文本\",\n"
                    "  \"tech_scoring\": [{\"title\": \"评分项名称\", \"score\": \"分值\", \"criteria\": \"评审标准原文整理\"}],\n"
                    "  \"biz_requirements\": [{\"title\": \"要求名称\", \"type\": \"资质/业绩/人员/财务/商务/其他\", \"criteria\": \"要求内容\"}],\n"
                    "  \"invalid_clauses\": [{\"title\": \"条款名称\", \"criteria\": \"无效投标或否决情形\"}]\n"
                    "}\n"
                    "要求：必须覆盖全文所有评分项，尤其不能遗漏 Markdown 表格里的分值和评审标准；字段为空时返回空字符串或空数组；去除页眉页脚等冗余内容。\n\n"
                    f"标书类型：{bid_category}\n\n"
                    f"规则预处理候选片段：\n{candidates}\n\n"
                    f"招标文件 Markdown 全文：\n{_clip_for_llm(source, 32000)}"
                ),
            },
        ], scene="tender_parse",
        temperature=0.0,
        max_tokens=6000,
        fallback=fallback,
    )
    try:
        optimized = _json_from_llm_content(result.content)
        merged = _merge_llm_extract_result(extracted, optimized)
        if result.error:
            logger.warning("Qwen parse optimization degraded to fallback: %s", result.error)
        return merged
    except Exception:
        logger.exception("Qwen parse optimization returned invalid JSON; using rule extraction")
        return extracted


def _rules_to_llm_payload(extracted: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_overview": extracted.get("project_overview") or "",
        "tech_scoring": _items_from_json_text(extracted.get("parsed_tech_scoring")) or _text_items(extracted.get("score_requirements")),
        "biz_requirements": _items_from_json_text(extracted.get("parsed_biz_requirements")) or _text_items(extracted.get("biz_requirements_text")),
        "invalid_clauses": _items_from_json_text(extracted.get("parsed_invalid_clauses")) or _text_items(extracted.get("invalid_clauses_text")),
    }


def _build_llm_candidate_context(source: str, extracted: dict[str, Any]) -> str:
    parts = [
        extracted.get("score_requirements") or "",
        extracted.get("biz_requirements_text") or "",
        extracted.get("invalid_clauses_text") or "",
    ]
    parts.extend(
        _keyword_windows(
            source,
            (
                "评分", "评审", "评标", "技术", "商务", "资质", "资格", "业绩", "人员", "财务",
                "废标", "无效投标", "否决", "详细评审", "评审因素", "分值", "得分",
            ),
            limit=24,
        )
    )
    compact: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        compact.append(value[:1800])
    return "\n\n---\n\n".join(compact)[:12000]


def _keyword_windows(source: str, keywords: tuple[str, ...], limit: int = 24) -> list[str]:
    lines = [line.strip() for line in (source or "").splitlines() if line.strip()]
    snippets: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not any(keyword in line for keyword in keywords):
            continue
        start = max(0, index - 2)
        end = min(len(lines), index + 5)
        block = "\n".join(lines[start:end]).strip()
        if block and block not in seen:
            seen.add(block)
            snippets.append(block)
        if len(snippets) >= limit:
            break
    return snippets


def _clip_for_llm(text: str, limit: int) -> str:
    value = text or ""
    if len(value) <= limit:
        return value
    head = value[: int(limit * 0.65)]
    tail = value[-int(limit * 0.25) :]
    middle = _build_llm_candidate_context(value, {})[: int(limit * 0.10)]
    return f"{head}\n\n<!-- 中间候选片段 -->\n{middle}\n\n<!-- 文档后段 -->\n{tail}"


def _json_from_llm_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM parse result is not a JSON object")
    return data


def _merge_llm_extract_result(extracted: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
    tech_items = _normalize_llm_items(
        optimized.get("tech_scoring")
        or optimized.get("technical_scoring")
        or optimized.get("score_requirements")
        or optimized.get("scoring_requirements")
    )
    biz_items = _normalize_llm_items(optimized.get("biz_requirements") or optimized.get("business_requirements"))
    invalid_items = _normalize_llm_items(optimized.get("invalid_clauses") or optimized.get("rejection_clauses"))

    project_overview = str(optimized.get("project_overview") or extracted.get("project_overview") or "").strip()
    score_requirements = _format_llm_items(tech_items) or str(optimized.get("score_requirements") or extracted.get("score_requirements") or "").strip()
    if not project_overview or not score_requirements:
        return extracted

    extracted["project_overview"] = project_overview
    extracted["score_requirements"] = score_requirements
    extracted["tech_scoring_text"] = score_requirements
    if biz_items:
        extracted["biz_requirements_text"] = _format_llm_items(biz_items)
    if invalid_items:
        extracted["invalid_clauses_text"] = _format_llm_items(invalid_items)
    extracted["parsed_tech_scoring"] = json.dumps(tech_items, ensure_ascii=False, indent=2)
    extracted["parsed_biz_requirements"] = json.dumps(biz_items, ensure_ascii=False, indent=2)
    extracted["parsed_invalid_clauses"] = json.dumps(invalid_items, ensure_ascii=False, indent=2)
    return extracted


def _normalize_llm_items(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            return _normalize_llm_items(parsed)
        except Exception:
            return _text_items(stripped)
    if isinstance(value, dict):
        return [_normalize_single_item(value, 1)]
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                items.append(_normalize_single_item(item, len(items) + 1))
            elif str(item or "").strip():
                items.extend(_text_items(str(item), start=len(items) + 1))
        return items
    return []


def _normalize_single_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    title = str(item.get("title") or item.get("name") or item.get("item") or item.get("category") or f"条目{index}").strip()
    criteria = str(item.get("criteria") or item.get("text") or item.get("requirement") or item.get("clause") or "").strip()
    score = item.get("score") or item.get("points") or item.get("分值")
    item_type = str(item.get("type") or item.get("category") or "").strip()
    normalized = {"id": index, "title": title[:160], "text": criteria[:1800]}
    if score not in (None, ""):
        normalized["score"] = str(score)
    if item_type:
        normalized["type"] = item_type[:80]
    return normalized


def _items_from_json_text(value: object) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        data = json.loads(str(value))
    except Exception:
        return []
    return _normalize_llm_items(data)


def _text_items(value: object, start: int = 1) -> list[dict[str, Any]]:
    text = str(value or "").strip()
    if not text:
        return []
    lines = [line.strip(" -\t") for line in text.splitlines() if line.strip(" -\t")]
    return [{"id": start + idx, "title": line[:80], "text": line[:1800]} for idx, line in enumerate(lines[:40])]


def _format_llm_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items[:80]:
        score = f"（{item.get('score')}分）" if item.get("score") not in (None, "") else ""
        text = item.get("text") or ""
        lines.append(f"- {item.get('title') or '评分项'}{score}：{text}" if text else f"- {item.get('title') or '评分项'}{score}")
    return "\n".join(lines)


def _persist_parse_failed(bid_id: int, file_path: str, message: str, code: int = 50001) -> None:
    logger.error(
        "parse failed bid_id=%s file=%s code=%s message=%s",
        bid_id,
        file_path,
        code,
        message,
        exc_info=sys.exc_info() if sys.exc_info()[0] else None,
    )
    now = _now_text()
    with storage.connect() as conn:
        row = conn.execute("SELECT extraction_progress FROM bid_parse_info WHERE bid_id = ? AND tender_file_path = ?", (bid_id, file_path)).fetchone()
        progress = _decode_extraction_progress(row["extraction_progress"] if row else None)
        if progress:
            _advance_extraction_progress(progress, str(progress.get("current_key") or "prepare"), "failed")
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parse_status = 'failed',
                parse_current_step = COALESCE(parse_current_step, 1),
                parse_error = ?,
                parse_error_msg = ?,
                parse_warning_msg = NULL,
                extraction_progress = ?,
                update_time = ?
            WHERE bid_id = ? AND tender_file_path = ? AND parse_status = 'processing'
            """,
            (str(code), message, json.dumps(progress, ensure_ascii=False) if progress else None, now, bid_id, file_path),
        )
        conn.commit()


def _mark_parse_failed(bid_id: int, file_path: str, message: str, code: int = 50001) -> None:
    """Persist failure state without allowing a secondary DB error to escape."""
    try:
        _persist_parse_failed(bid_id, file_path, message, code)
    except Exception:
        logger.exception("failed to persist parse failure bid_id=%s file=%s", bid_id, file_path)


def _safe_mark_parse_failed(bid_id: int, file_path: str, message: str, code: int = 50001) -> None:
    try:
        _mark_parse_failed(bid_id, file_path, message, code)
    except Exception:
        logger.exception("failed to persist parse failure bid_id=%s file=%s", bid_id, file_path)


def save_paste_parse_content(bid_id: int, payload: PasteParseContentRequest) -> dict[str, Any]:
    _ensure_bid(bid_id)
    project_overview = (payload.project_overview or "").strip()
    score_requirements = (payload.score_requirements or "").strip()
    if not project_overview:
        raise BidParseServiceError(40001, "项目概述不能为空")
    if not score_requirements:
        raise BidParseServiceError(40001, "技术部分评分要求不能为空")
    if len(project_overview) > 30000 or len(score_requirements) > 30000:
        raise BidParseServiceError(40001, "单个文本字段长度不能超过 30000 字")

    now = _now_text()
    old_file_path = None
    with storage.connect() as conn:
        current = conn.execute("SELECT tender_file_path FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        old_file_path = current["tender_file_path"] if current else None
        conn.execute(
            """
            INSERT INTO bid_parse_info (
                bid_id, parse_mode, paste_content, bid_category,
                parsed_project_overview, parsed_score_requirements,
                tender_file_path, tender_file_name, tender_file_size,
                raw_full_text, raw_markdown, parsed_tech_scoring, parsed_biz_requirements, parsed_invalid_clauses,
                parse_current_step, parse_status, parse_error, parse_error_msg, create_time, update_time
            ) VALUES (?, 'paste_text', NULL, 'service', ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, 4, 'completed', NULL, NULL, ?, ?)
            ON CONFLICT(bid_id) DO UPDATE SET
                parse_mode = 'paste_text',
                paste_content = NULL,
                parsed_project_overview = excluded.parsed_project_overview,
                parsed_score_requirements = excluded.parsed_score_requirements,
                parsed_tech_scoring = excluded.parsed_tech_scoring,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                tender_file_path = NULL,
                tender_file_name = NULL,
                tender_file_size = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                extraction_progress = NULL,
                parse_current_step = 4,
                parse_status = 'completed',
                parse_error = NULL,
                parse_error_msg = NULL,
                update_time = excluded.update_time
            """,
            (bid_id, project_overview, score_requirements, score_requirements, now, now),
        )
        conn.execute(
            """
            UPDATE bid_parse_info
            SET full_markdown = NULL,
                chapter_blocks = NULL,
                project_basic_info = ?,
                tech_scoring_items = ?,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL
            WHERE bid_id = ?
            """,
            (
                _json_dumps({"overview": project_overview}),
                _json_dumps(_text_items(score_requirements)),
                bid_id,
            ),
        )
        conn.commit()
    if old_file_path:
        _delete_data_file(old_file_path)
    return get_parse_info(bid_id)


def save_parse_result_edit(bid_id: int, payload: ParseResultEditRequest) -> dict[str, Any]:
    _ensure_bid(bid_id)
    project_overview = (payload.project_overview or "").strip()
    score_requirements = (payload.score_requirements or "").strip()
    if not project_overview:
        raise BidParseServiceError(40001, "项目概述不能为空")
    if not score_requirements:
        raise BidParseServiceError(40001, "技术部分评分要求不能为空")
    if len(project_overview) > 30000 or len(score_requirements) > 30000:
        raise BidParseServiceError(40001, "单个文本字段长度不能超过 30000 字")

    now = _now_text()
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parsed_project_overview = ?,
                parsed_score_requirements = ?,
                parsed_tech_scoring = ?,
                project_basic_info = ?,
                tech_scoring_items = ?,
                extraction_progress = NULL,
                parse_current_step = 4,
                parse_status = 'completed',
                parse_error = NULL,
                parse_error_msg = NULL,
                parse_warning_msg = NULL,
                update_time = ?
            WHERE bid_id = ?
            """,
            (
                project_overview,
                score_requirements,
                score_requirements,
                _json_dumps({"overview": project_overview}),
                _json_dumps(_text_items(score_requirements)),
                now,
                bid_id,
            ),
        )
        conn.commit()
    return get_parse_info(bid_id)


def prepare_reparse(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    now = _now_text()
    with storage.connect() as conn:
        row = conn.execute("SELECT tender_file_path FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        if row is None or not row["tender_file_path"]:
            raise BidParseServiceError(40001, "请先上传招标文件")
        with _PARSE_TASK_LOCK:
            if _ACTIVE_PARSE_TASKS.get(_parse_task_key(bid_id, str(row["tender_file_path"]))):
                raise BidParseServiceError(40901, "当前文件仍在解析，请等待任务结束后重试", 409)
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                parsed_tech_scoring = NULL,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_current_step = 1,
                parse_status = 'processing',
                parse_error = NULL,
                parse_error_msg = NULL,
                parse_warning_msg = NULL,
                update_time = ?
            WHERE bid_id = ?
            """,
            (now, bid_id),
        )
        conn.commit()
    info = get_parse_info(bid_id)
    return {"bid_id": bid_id, "file_path": info["tender_file_path"], "parse_status": "processing", "parse_info": info}


def clear_paste_parse_content(bid_id: int, only_if_current_step_2: bool = False) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    if only_if_current_step_2 and int(bid["current_step"]) >= 3:
        return get_parse_info(bid_id)

    now = _now_text()
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parse_mode = NULL,
                paste_content = NULL,
                parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                parsed_tech_scoring = NULL,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_current_step = NULL,
                parse_status = 'pending',
                parse_error = NULL,
                parse_error_msg = NULL,
                update_time = ?
            WHERE bid_id = ? AND parse_mode = 'paste_text'
            """,
            (now, bid_id),
        )
        conn.commit()
    return get_parse_info(bid_id)


def clear_auto_parse_content(bid_id: int, only_if_current_step_2: bool = False) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    if only_if_current_step_2 and int(bid["current_step"]) >= 3:
        return get_parse_info(bid_id)

    old_file_path = None
    now = _now_text()
    with storage.connect() as conn:
        current = conn.execute("SELECT tender_file_path FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        old_file_path = current["tender_file_path"] if current else None
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parse_mode = NULL,
                tender_file_path = NULL,
                tender_file_name = NULL,
                tender_file_size = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                parsed_tech_scoring = NULL,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_current_step = NULL,
                parse_status = 'pending',
                parse_error = NULL,
                parse_error_msg = NULL,
                update_time = ?
            WHERE bid_id = ? AND parse_mode = 'auto_upload'
            """,
            (now, bid_id),
        )
        conn.commit()
    if old_file_path:
        _delete_data_file(old_file_path)
    return get_parse_info(bid_id)


def cleanup_unsubmitted_parse_content(bid_id: int) -> dict[str, Any]:
    bid = _ensure_bid(bid_id)
    if int(bid["current_step"]) >= 3:
        return get_parse_info(bid_id)

    old_file_path = None
    now = _now_text()
    with storage.connect() as conn:
        current = conn.execute("SELECT parse_mode, tender_file_path FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        old_file_path = current["tender_file_path"] if current and current["parse_mode"] == "auto_upload" else None
        conn.execute(
            """
            UPDATE bid_parse_info
            SET parse_mode = NULL,
                tender_file_path = NULL,
                tender_file_name = NULL,
                tender_file_size = NULL,
                paste_content = NULL,
                parsed_project_overview = NULL,
                parsed_score_requirements = NULL,
                parsed_tech_scoring = NULL,
                parsed_biz_requirements = NULL,
                parsed_invalid_clauses = NULL,
                raw_full_text = NULL,
                raw_markdown = NULL,
                full_markdown = NULL,
                chapter_blocks = NULL,
                project_basic_info = NULL,
                tech_scoring_items = NULL,
                biz_scoring_items = NULL,
                qualification_requirements = NULL,
                invalid_bid_clauses = NULL,
                bid_key_points = NULL,
                extraction_progress = NULL,
                parse_current_step = NULL,
                parse_status = 'pending',
                parse_error = NULL,
                parse_error_msg = NULL,
                update_time = ?
            WHERE bid_id = ?
            """,
            (now, bid_id),
        )
        conn.commit()
    if old_file_path:
        _delete_data_file(old_file_path)
    return get_parse_info(bid_id)


async def upload_list_files(bid_id: int, uploads: list[UploadFile]) -> dict[str, Any]:
    _ensure_bid(bid_id)
    if not uploads:
        raise BidParseServiceError(40001, "请至少上传一个清单文件")
    saved_files: list[dict[str, Any]] = []
    try:
        for upload in uploads:
            saved_files.append(await _save_upload(bid_id, "lists", upload))
    except BaseException:
        for saved in saved_files:
            _delete_data_file(saved["file_path"])
        raise
    now = _now_text()
    with storage.connect() as conn:
        for item in saved_files:
            conn.execute(
                """
                INSERT INTO bid_list_files (bid_id, list_type, file_path, file_name, file_size, create_time)
                VALUES (?, 'upload', ?, ?, ?, ?)
                """,
                (bid_id, item["file_path"], item["file_name"], item["file_size"], now),
            )
        conn.commit()
    return {"bid_id": bid_id, "files": saved_files, "parse_info": get_parse_info(bid_id)}


async def upload_reference_material(bid_id: int, upload: UploadFile, material_type: MaterialType) -> dict[str, Any]:
    _ensure_bid(bid_id)
    saved = await _save_upload(bid_id, f"references/{material_type}", upload, TENDER_EXTENSIONS)
    now = _now_text()
    with storage.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO bid_reference_materials (
                bid_id, material_type, file_path, file_name, file_size,
                parse_status, create_time, update_time
            ) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?)
            """,
            (bid_id, material_type, saved["file_path"], saved["file_name"], saved["file_size"], now, now),
        )
        ref_id = int(cursor.lastrowid)
        conn.commit()
    absolute_path = _absolute_data_path(saved["file_path"])
    try:
        if absolute_path is None:
            raise BidParseServiceError(50003, "范本文件路径无效", 500)
        parsed = await asyncio.wait_for(
            asyncio.to_thread(parse_bid_document, str(absolute_path)),
            timeout=PARSE_TIMEOUT_SECONDS,
        )
        if not parsed.raw_text.strip():
            raise BidParseServiceError(40005, "范本未提取到可用文本，请上传可编辑的 doc、docx 或文本型 PDF")
        chunks = bid_reference_service.build_reference_chunks(parsed.full_markdown, parsed.chapter_blocks)
        fallback_company, fallback_style = bid_reference_service.fallback_profiles(parsed.raw_text)
        profile_result = await qwen_client.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是投标范本知识库整理专家。只输出JSON，不得编造。"
                        "company_profile 提取企业名称、简介、资质、认证、团队、业绩、案例、能力等原文事实；"
                        "style_profile 描述范本的语气、段落结构、论证方式、常用组织方式和措辞习惯。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请分析以下历史标书范本，输出："
                        '{"company_profile":["事实1"],"style_profile":["风格特征1"]}。'
                        "不得把项目招标要求误认为企业事实。\n\n"
                        f"范本内容：\n{parsed.full_markdown[:16000]}"
                    ),
                },
            ], scene="tender_parse",
            temperature=0.1,
            max_tokens=1800,
            fallback=json.dumps(
                {"company_profile": fallback_company, "style_profile": fallback_style},
                ensure_ascii=False,
            ),
            json_mode=True,
        )
        company_profile, style_profile = bid_reference_service.parse_profile_json(
            profile_result.content, fallback_company, fallback_style
        )
        completed_at = _now_text()
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE bid_reference_materials
                SET raw_text = ?, full_markdown = ?, chunks_json = ?, company_profile = ?,
                    style_profile = ?, parse_status = 'completed', parse_error = NULL, update_time = ?
                WHERE ref_id = ? AND bid_id = ?
                """,
                (
                    parsed.raw_text,
                    parsed.full_markdown,
                    json.dumps(chunks, ensure_ascii=False),
                    company_profile,
                    style_profile,
                    completed_at,
                    ref_id,
                    bid_id,
                ),
            )
            conn.commit()
        logger.info(
            "reference parsed bid_id=%s ref_id=%s file=%s chunks=%s qwen=%s",
            bid_id, ref_id, saved["file_name"], len(chunks), profile_result.usedApi,
        )
        return {
            "bid_id": bid_id,
            "reference": {
                "ref_id": ref_id,
                "material_type": material_type,
                **saved,
                "parse_status": "completed",
                "chunk_count": len(chunks),
                "company_profile": company_profile,
                "style_profile": style_profile,
                "qwen_used": profile_result.usedApi,
            },
            "parse_info": get_parse_info(bid_id),
        }
    except BidParseServiceError as exc:
        error_message = exc.message
        error = exc
    except (BidParseError, asyncio.TimeoutError) as exc:
        error_message = "范本解析超时，请重试" if isinstance(exc, asyncio.TimeoutError) else str(exc)
        error = BidParseServiceError(getattr(exc, "code", 50001), error_message)
    except Exception as exc:  # noqa: BLE001 - record all reference parsing failures.
        logger.exception("reference parse failed bid_id=%s ref_id=%s file=%s", bid_id, ref_id, saved["file_name"])
        error_message = f"范本解析失败：{str(exc)[:300]}"
        error = BidParseServiceError(50001, error_message, 500)
    with storage.connect() as conn:
        conn.execute(
            "UPDATE bid_reference_materials SET parse_status = 'failed', parse_error = ?, update_time = ? WHERE ref_id = ?",
            (error_message, _now_text(), ref_id),
        )
        conn.commit()
    raise error


def _extract_lines(text: str, keywords: tuple[str, ...], limit: int = 8) -> list[str]:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    matched = [line for line in lines if any(keyword in line for keyword in keywords)]
    return matched[:limit]


def _build_placeholder_parse_result(parse_info: sqlite3.Row) -> tuple[str, str]:
    """Temporary parsing seam for the future AI/document parser integration.

    Replace this function with the Qwen + document extraction pipeline later;
    the surrounding status updates and persistence can stay unchanged.
    """

    project_overview = (parse_info["parsed_project_overview"] or "").strip()
    score_requirements = (parse_info["parsed_score_requirements"] or "").strip()
    if project_overview and score_requirements:
        return project_overview, score_requirements

    paste_content = (parse_info["paste_content"] or "").strip()
    if paste_content:
        overview_source = paste_content[:500]
        overview = f"已根据粘贴的项目信息完成初步整理：{overview_source}"
        score_lines = _extract_lines(paste_content, ("评分", "分值", "评审", "要求", "标准"))
        score_requirements = "\n".join(score_lines) if score_lines else "未在粘贴文本中识别到明确评分条款，请在后续资料中补充评分细则。"
        return overview, score_requirements

    file_name = parse_info["tender_file_name"] or "招标文件"
    overview = f"已接收招标文件《{file_name}》，可作为后续目录生成和正文生成的招标依据。"
    score_requirements = "评分要求已随招标文件保存，正式文件解析能力接入后会在此字段输出结构化评分条款。"
    return overview, score_requirements


def trigger_parse(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        parse_info = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        if parse_info is None:
            raise BidParseServiceError(40001, "请先上传招标文件或粘贴项目信息")
        if parse_info["parse_mode"] == "auto_upload":
            if parse_info["parse_status"] == "processing":
                raise BidParseServiceError(40001, "文件解析中，请稍候")
            has_ai_parse_result = bool(
                (parse_info["full_markdown"] or "").strip()
                and (parse_info["chapter_blocks"] or "").strip()
                and ((parse_info["tech_scoring_items"] or "").strip() or (parse_info["bid_key_points"] or "").strip())
            )
            if parse_info["parse_status"] == "completed" and has_ai_parse_result:
                return get_parse_info(bid_id)
            if parse_info["parse_status"] == "completed" and not has_ai_parse_result:
                raise BidParseServiceError(40001, "当前解析结果来自旧版全文提取，未完成千问智能解读，请点击重新解析")
            message = parse_info["parse_error_msg"] or parse_info["parse_error"] or "请重新上传招标文件并等待解析完成"
            raise BidParseServiceError(40001, message)
        has_paste_content = bool((parse_info["paste_content"] or "").strip())
        has_structured_paste = bool((parse_info["parsed_project_overview"] or "").strip() and (parse_info["parsed_score_requirements"] or "").strip())
        if not parse_info["tender_file_path"] and not has_paste_content and not has_structured_paste:
            raise BidParseServiceError(40001, "请先上传招标文件或粘贴项目信息")

        now = _now_text()
        conn.execute(
            "UPDATE bid_parse_info SET extraction_progress = NULL, parse_status = 'processing', parse_error = NULL, update_time = ? WHERE bid_id = ?",
            (now, bid_id),
        )
        conn.commit()

    try:
        with storage.connect() as conn:
            parse_info = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
            if parse_info is None:
                raise RuntimeError("parse_info disappeared during parsing")
            overview, score_requirements = _build_placeholder_parse_result(parse_info)
            now = _now_text()
            conn.execute(
                """
                UPDATE bid_parse_info
                SET parsed_project_overview = ?,
                    parsed_score_requirements = ?,
                    parse_status = 'completed',
                    parse_error = NULL,
                    update_time = ?
                WHERE bid_id = ?
                """,
                (overview, score_requirements, now, bid_id),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - persist parser failures for the UI.
        with storage.connect() as conn:
            conn.execute(
                "UPDATE bid_parse_info SET parse_status = 'failed', parse_error = ?, update_time = ? WHERE bid_id = ?",
                (str(exc), _now_text(), bid_id),
            )
            conn.commit()
        raise BidParseServiceError(50001, f"招标文件解析失败：{exc}", 500) from exc

    return get_parse_info(bid_id)


def complete_step2(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    parse_info = get_parse_info(bid_id)
    parse_mode = parse_info.get("parse_mode")
    if parse_mode not in {"auto_upload", "paste_text"}:
        raise BidParseServiceError(40001, "请选择招标文件解析方式")
    if not parse_info.get("bid_category"):
        raise BidParseServiceError(40001, "请选择标书类型")
    if parse_mode == "auto_upload" and not parse_info.get("tender_file_path"):
        raise BidParseServiceError(40001, "自动上传解析模式下，请先上传招标文件")
    if parse_mode == "auto_upload" and parse_info.get("parse_status") != "completed":
        if parse_info.get("parse_status") == "failed":
            message = parse_info.get("parse_error_msg") or parse_info.get("parse_error") or "招标文件解析失败，请重新上传"
            raise BidParseServiceError(40001, message)
        raise BidParseServiceError(40001, "文件解析中，请稍候")
    if parse_mode == "auto_upload" and not (
        (parse_info.get("parsed_project_overview") or "").strip()
        and (parse_info.get("parsed_score_requirements") or "").strip()
    ):
        raise BidParseServiceError(40001, "文件解析结果为空，请重新解析或手动补充解析结果")
    has_structured_paste = bool((parse_info.get("parsed_project_overview") or "").strip() and (parse_info.get("parsed_score_requirements") or "").strip())
    has_legacy_paste = bool((parse_info.get("paste_content") or "").strip())
    if parse_mode == "paste_text" and not has_structured_paste and not has_legacy_paste:
        raise BidParseServiceError(40001, "粘贴文本解析模式下，请填写项目信息文本")

    if parse_mode == "paste_text" and parse_info.get("parse_status") != "completed":
        parse_info = trigger_parse(bid_id)

    storage.update_bid_current_step(bid_id, 3)
    return {"bid_id": bid_id, "current_step": 3, "parse_info": parse_info}
