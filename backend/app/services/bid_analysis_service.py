from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.schemas.tender import BidSettings
from app.services import storage
from app.services.model_preferences import selected_model
from app.utils.bid_analysis_skill import adapter
from app.utils.bid_parser import BidParseError, parse_bid_document


logger = logging.getLogger(__name__)

ALLOWED_CHECK_ITEMS = tuple(adapter.CHECK_LABELS)
ANALYSIS_ALLOWED_SUFFIXES = {".doc", ".docx", ".pdf"}
ANALYSIS_MAX_FILE_SIZE = 50 * 1024 * 1024


class BidAnalysisServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _now() -> str:
    return storage.now_utc().isoformat()


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return value


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _result_dict(row: Any) -> dict[str, Any]:
    value = _row_dict(row)
    value["resolved"] = bool(value.get("resolved"))
    return value


def _friendly_skill_error(exc: Exception) -> str:
    raw = str(exc).strip()
    lowered = raw.lower()
    if "connection" in lowered or "connect" in lowered:
        return "千问服务连接失败，请检查网络连接后重新检测"
    if "timeout" in lowered:
        return "千问服务响应超时，请稍后重新检测"
    if "api key" in lowered or "unauthorized" in lowered or "401" in lowered:
        return "千问 API 配置无效，请检查 QWEN_API_KEY"
    if "json" in lowered or "delimiter" in lowered or "unterminated" in lowered:
        return "千问已调用，但返回内容格式不完整，请重新检测"
    return "bid-analysis skill 执行失败，请稍后重新检测"


def _ensure_prerequisites(bid_id: int) -> None:
    with storage.connect() as conn:
        bid = conn.execute("SELECT bid_id FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        documents = conn.execute("SELECT bid_id FROM bid_analysis_documents WHERE bid_id = ?", (bid_id,)).fetchone()
    if bid is None:
        raise BidAnalysisServiceError(40401, f"标书不存在：{bid_id}", 404)
    if documents is not None:
        return
    if parse is None or parse["parse_status"] != "completed":
        raise BidAnalysisServiceError(40001, "请先完成招标文件解读，再执行标书检查")
    fields = (
        "project_basic_info", "tech_scoring_items", "biz_scoring_items",
        "invalid_bid_clauses", "qualification_requirements", "bid_key_points",
        "parsed_project_overview", "parsed_score_requirements",
    )
    if not any(str(parse[field] or "").strip() for field in fields):
        raise BidAnalysisServiceError(40001, "请先完成招标文件解读，再执行标书检查")


def validate_check_items(check_items: list[str]) -> list[str]:
    selected = list(dict.fromkeys(check_items or ALLOWED_CHECK_ITEMS))
    invalid = [item for item in selected if item not in ALLOWED_CHECK_ITEMS]
    if invalid:
        raise BidAnalysisServiceError(40002, f"不支持的检查项：{', '.join(invalid)}")
    if not selected:
        raise BidAnalysisServiceError(40002, "请至少选择一个检查项")
    return selected


def _select_version(conn: Any, bid_id: int) -> Any:
    return conn.execute(
        """
        SELECT * FROM bid_catalog_versions
        WHERE bid_id = ?
        ORDER BY is_final DESC, version_id ASC
        LIMIT 1
        """,
        (bid_id,),
    ).fetchone()


def _analysis_input(bid_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
        documents = conn.execute("SELECT * FROM bid_analysis_documents WHERE bid_id = ?", (bid_id,)).fetchone()
        if documents is not None:
            tender_markdown = str(documents["tender_markdown"] or documents["tender_text"] or "")
            bid_markdown = str(documents["bid_markdown"] or documents["bid_text"] or "")
            return {
                "bid": {
                    "bid_id": bid_id,
                    "title": Path(str(documents["bid_file_name"])).stem,
                    "type": "standalone_file_check",
                    "page_count": None,
                },
                "tender_rules": {
                    "source_file": documents["tender_file_name"],
                    "full_markdown": tender_markdown[:70000],
                    "content_truncated": len(tender_markdown) > 70000,
                },
                "current_bid_document": {
                    "source_file": documents["bid_file_name"],
                    "full_markdown": bid_markdown[:70000],
                    "content_truncated": len(bid_markdown) > 70000,
                    "chapters": [],
                },
                "input_note": "本次为独立双文件检查。内容被截断时必须标注需人工复核，不得推断未提供部分。",
            }
        version = _select_version(conn, bid_id)
        chapters = []
        contents: dict[int, Any] = {}
        if version is not None:
            chapter_rows = conn.execute(
                """
                SELECT chapter_id, parent_id, chapter_title, level, sort_order
                FROM bid_catalog_chapters
                WHERE bid_id = ? AND version_id = ?
                ORDER BY sort_order ASC, chapter_id ASC
                """,
                (bid_id, int(version["version_id"])),
            ).fetchall()
            content_rows = conn.execute(
                """
                SELECT chapter_id, content_text, word_count, status
                FROM bid_chapter_contents
                WHERE bid_id = ? AND version_id = ?
                """,
                (bid_id, int(version["version_id"])),
            ).fetchall()
            contents = {int(row["chapter_id"]): row for row in content_rows}
            remaining = 50000
            for row in chapter_rows:
                content = contents.get(int(row["chapter_id"]))
                raw_text = str(content["content_text"] or "") if content else ""
                excerpt = raw_text[: min(remaining, 8000)]
                remaining = max(0, remaining - len(excerpt))
                chapters.append({
                    "chapter_id": int(row["chapter_id"]),
                    "parent_id": int(row["parent_id"]),
                    "level": int(row["level"]),
                    "sort_order": int(row["sort_order"]),
                    "title": row["chapter_title"],
                    "content_status": content["status"] if content else "unwritten",
                    "word_count": int(content["word_count"] or 0) if content else 0,
                    "content": excerpt,
                    "content_truncated": len(raw_text) > len(excerpt),
                })

    if bid is None or parse is None:
        raise BidAnalysisServiceError(40001, "请先完成招标文件解读，再执行标书检查")
    tender_rules = {
        "project_basic_info": _loads(parse["project_basic_info"], {}),
        "tech_scoring_items": _loads(parse["tech_scoring_items"], []),
        "biz_scoring_items": _loads(parse["biz_scoring_items"], []),
        "invalid_bid_clauses": _loads(parse["invalid_bid_clauses"], []),
        "qualification_requirements": _loads(parse["qualification_requirements"], []),
        "bid_key_points": _loads(parse["bid_key_points"], {}),
        "project_overview": parse["parsed_project_overview"] or "",
        "score_requirements": parse["parsed_score_requirements"] or "",
    }
    return {
        "bid": {
            "bid_id": bid_id,
            "title": bid["bid_title"],
            "type": bid["bid_type"],
            "page_count": bid["page_count"],
        },
        "tender_rules": tender_rules,
        "current_bid_document": {
            "catalog_version": {
                "version_id": int(version["version_id"]) if version else None,
                "version_name": version["version_name"] if version else None,
                "is_final": bool(version["is_final"]) if version else False,
            },
            "chapters": chapters,
        },
        "input_note": "正文总输入最多保留50000字符；content_truncated=true的章节必须标记需人工复核，不得据此断言全文缺失。",
    }


def _safe_upload_name(filename: str | None, fallback: str) -> str:
    name = Path(filename or fallback).name
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)[:180] or fallback


async def _read_analysis_upload(upload: UploadFile, role: str) -> tuple[str, bytes]:
    name = _safe_upload_name(upload.filename, f"{role}.docx")
    suffix = Path(name).suffix.lower()
    if suffix not in ANALYSIS_ALLOWED_SUFFIXES:
        raise BidAnalysisServiceError(40010, f"{role}仅支持 doc、docx、pdf 格式")
    content = await upload.read()
    if not content:
        raise BidAnalysisServiceError(40011, f"{role}为空，请重新选择文件")
    if len(content) > ANALYSIS_MAX_FILE_SIZE:
        raise BidAnalysisServiceError(40012, f"{role}不能超过 50MB")
    return name, content


async def prepare_standalone_analysis(tender_file: UploadFile, bid_file: UploadFile) -> dict[str, Any]:
    tender_name, tender_bytes = await _read_analysis_upload(tender_file, "招标文件")
    bid_name, bid_bytes = await _read_analysis_upload(bid_file, "待检查标书")
    project = storage.create_project(BidSettings(
        bidTitle=Path(bid_name).stem,
        pageCount=0,
        estimatedWordCount=0,
        selectedModel=selected_model("tender_parse"),
        selectedProvider="qwen",
    ))
    bid_id = storage.get_bid_id(project.id)
    folder = storage.settings.upload_dir / "analysis" / str(bid_id)
    folder.mkdir(parents=True, exist_ok=True)
    tender_path = folder / f"tender{Path(tender_name).suffix.lower()}"
    bid_path = folder / f"bid{Path(bid_name).suffix.lower()}"
    tender_path.write_bytes(tender_bytes)
    bid_path.write_bytes(bid_bytes)
    try:
        tender_doc, bid_doc = await asyncio.gather(
            asyncio.to_thread(parse_bid_document, str(tender_path)),
            asyncio.to_thread(parse_bid_document, str(bid_path)),
        )
    except BidParseError as exc:
        logger.exception("standalone bid-analysis document parse failed bid_id=%s", bid_id)
        for path in (tender_path, bid_path):
            path.unlink(missing_ok=True)
        folder.rmdir()
        with storage.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project.id,))
            conn.commit()
        raise BidAnalysisServiceError(getattr(exc, "code", 40013), str(exc)) from exc
    except Exception as exc:
        logger.exception("standalone bid-analysis document parse failed bid_id=%s", bid_id)
        for path in (tender_path, bid_path):
            path.unlink(missing_ok=True)
        folder.rmdir()
        with storage.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project.id,))
            conn.commit()
        raise BidAnalysisServiceError(40013, f"文件解析失败：{exc}") from exc
    now = _now()
    data_root = storage.settings.resolve_local_path(storage.settings.data_dir)
    relative_tender = tender_path.relative_to(data_root).as_posix()
    relative_bid = bid_path.relative_to(data_root).as_posix()
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO bid_analysis_documents(
                bid_id, tender_file_name, tender_file_path, tender_text, tender_markdown,
                bid_file_name, bid_file_path, bid_text, bid_markdown, create_time, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid_id, tender_name, relative_tender, tender_doc.raw_text, tender_doc.full_markdown,
                bid_name, relative_bid, bid_doc.raw_text, bid_doc.full_markdown, now, now,
            ),
        )
        conn.commit()
    logger.info(
        "standalone bid-analysis prepared bid_id=%s tender=%s bid=%s tender_chars=%s bid_chars=%s",
        bid_id, tender_name, bid_name, len(tender_doc.full_markdown), len(bid_doc.full_markdown),
    )
    return {
        "bid_id": bid_id,
        "tender_file_name": tender_name,
        "bid_file_name": bid_name,
        "tender_char_count": len(tender_doc.raw_text),
        "bid_char_count": len(bid_doc.raw_text),
        "ready": True,
    }


def get_analysis_source(bid_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_analysis_documents WHERE bid_id = ?", (bid_id,)).fetchone()
    if row is None:
        _ensure_prerequisites(bid_id)
        return {"bid_id": bid_id, "standalone": False, "ready": True}
    return {
        "bid_id": bid_id,
        "standalone": True,
        "ready": True,
        "tender_file_name": row["tender_file_name"],
        "bid_file_name": row["bid_file_name"],
        "tender_char_count": len(str(row["tender_text"] or "")),
        "bid_char_count": len(str(row["bid_text"] or "")),
    }


def _create_task(bid_id: int, check_items: list[str]) -> str:
    task_id = uuid.uuid4().hex
    now = _now()
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO bid_analysis_tasks(task_id, bid_id, status, check_items, create_time, update_time)
            VALUES (?, ?, 'processing', ?, ?, ?)
            """,
            (task_id, bid_id, json.dumps(check_items, ensure_ascii=False), now, now),
        )
        conn.commit()
    return task_id


def _save_results(task_id: str, bid_id: int, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    now = _now()
    with storage.connect() as conn:
        chapter_rows = conn.execute(
            "SELECT chapter_id, parent_id, chapter_title, level FROM bid_catalog_chapters WHERE bid_id = ?",
            (bid_id,),
        ).fetchall()
        chapters = {int(row["chapter_id"]): row for row in chapter_rows}
        for item in results:
            chapter_id = item.get("chapter_id")
            chapter = chapters.get(chapter_id)
            if chapter is not None and int(chapter["level"]) == 3:
                parent = chapters.get(int(chapter["parent_id"]))
                if parent is not None:
                    chapter_id = int(parent["chapter_id"])
                    item["chapter_title"] = parent["chapter_title"]
            elif chapter is None:
                chapter_id = None
            cursor = conn.execute(
                """
                INSERT INTO bid_analysis_result(
                    task_id, bid_id, check_type, risk_level, risk_title, source_text,
                    problem_text, suggestion_text, chapter_id, chapter_title, resolved,
                    create_time, update_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    task_id, bid_id, item["check_type"], item["risk_level"], item["risk_title"],
                    item.get("source_text"), item["problem_text"], item["suggestion_text"],
                    chapter_id, item.get("chapter_title"), now, now,
                ),
            )
            saved.append({**item, "result_id": int(cursor.lastrowid), "task_id": task_id, "bid_id": bid_id, "resolved": False})
        conn.commit()
    return saved


def _build_report(bid_id: int, task_id: str) -> str:
    with storage.connect() as conn:
        bid = conn.execute("SELECT bid_title FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        rows = conn.execute(
            "SELECT * FROM bid_analysis_result WHERE bid_id = ? AND task_id = ? ORDER BY result_id ASC",
            (bid_id, task_id),
        ).fetchall()
    title = str(bid["bid_title"] or f"标书{bid_id}") if bid else f"标书{bid_id}"
    results = [_result_dict(row) for row in rows]
    high = [item for item in results if item["risk_level"] == "high"]
    medium = [item for item in results if item["risk_level"] == "medium"]
    suggestions = [item for item in results if item["risk_level"] == "suggestion"]

    def section(items: list[dict[str, Any]], empty: str) -> str:
        if not items:
            return empty
        blocks = []
        for index, item in enumerate(items, 1):
            status = "已处理" if item["resolved"] else "待处理"
            blocks.append(
                f"### {index}. {item['risk_title']}\n\n"
                f"- 状态：{status}\n"
                f"- 来源：{item.get('source_text') or '未提供明确原文，需人工复核'}\n"
                f"- 当前问题：{item['problem_text']}\n"
                f"- 整改建议：{item['suggestion_text']}\n"
                f"- 定位章节：{item.get('chapter_title') or '未定位'}"
            )
        return "\n\n".join(blocks)

    return f"""# {title} 标书合规检查报告

> 生成时间：{_now()}
> 检查任务：{task_id}
> 依据：bid-analysis skill

## 一、基本信息摘要

- 标书 ID：{bid_id}
- 共发现 {len(results)} 项，其中高风险 {len(high)} 项、中风险 {len(medium)} 项、优化建议 {len(suggestions)} 项。

## 二、时间节点梳理

本检查聚焦当前投标文件响应情况，时间节点以招标文件结构化解析结果为准。

## 三、采购需求与技术规格

已按 bid-analysis skill 对关键需求响应完整性进行核查。

## 四、评分标准深度解析

已逐项对照技术和商务评分项，风险明细见第八章。

## 五、商务条款与投标要求

已核查资格、商务条款、工期、保证金、格式及签署要求。

## 六、投标重点分析

优先处理所有高风险项，再处理评分点漏响应和商务冲突。

## 七、技术难点分析

对未生成、内容截断或无法定位的章节，应安排人工复核。

## 八、潜在风险提示

### 高风险（极易废标）

{section(high, '未发现高风险项。')}

### 中风险

{section(medium, '未发现中风险项。')}

### 优化建议

{section(suggestions, '暂无优化建议。')}

## 九、需特别关注的条款

重点复核废标条款、资格证明、签字盖章、保证金和评分项证明材料。

## 十、综合竞争态势分析

本报告用于投标前内部合规检查，不替代人工终审。所有未提供正文或原始附件的事项均需人工确认。

## 附注、未识别条款说明

正文被截断、尚未生成或招标解析数据未覆盖的内容，系统不会静默判定为合规，应人工复核原始文件。
"""


def _finish_task(task_id: str, bid_id: int, status: str, error: str | None = None) -> str:
    report = _build_report(bid_id, task_id)
    with storage.connect() as conn:
        conn.execute(
            "UPDATE bid_analysis_tasks SET status = ?, error_message = ?, report_markdown = ?, update_time = ? WHERE task_id = ?",
            (status, error, report, _now(), task_id),
        )
        conn.commit()
    return report


async def stream_analysis(bid_id: int, check_items: list[str]) -> AsyncIterator[dict[str, Any]]:
    selected = validate_check_items(check_items)
    _ensure_prerequisites(bid_id)
    payload = _analysis_input(bid_id)
    task_id = _create_task(bid_id, selected)
    logger.info("bid-analysis started bid_id=%s task_id=%s checks=%s", bid_id, task_id, selected)
    yield {"type": "start", "task_id": task_id, "total": len(selected), "message": "标书检查已启动"}

    succeeded = 0
    errors: list[str] = []
    for index, check_type in enumerate(selected, 1):
        label = adapter.CHECK_LABELS[check_type]
        yield {"type": "progress", "task_id": task_id, "current": index, "total": len(selected), "check_type": check_type, "label": label}
        try:
            results, meta = await adapter.analyze_check_item(check_type, payload)
            saved = _save_results(task_id, bid_id, results)
            succeeded += 1
            for item in saved:
                yield {"type": "result", "task_id": task_id, "data": item}
            yield {"type": "check_done", "task_id": task_id, "check_type": check_type, "count": len(saved), "llm": meta}
        except Exception as exc:
            message = _friendly_skill_error(exc)
            errors.append(f"{label}: {message}")
            logger.exception("bid-analysis skill failed bid_id=%s task_id=%s check_type=%s", bid_id, task_id, check_type)
            yield {"type": "check_error", "task_id": task_id, "check_type": check_type, "message": f"{label}执行失败，已跳过该项，请稍后重新检测"}

    status = "completed" if succeeded else "failed"
    error = "；".join(errors) if errors else None
    _finish_task(task_id, bid_id, status, error)
    if status == "failed":
        yield {"type": "error", "task_id": task_id, "message": "bid-analysis skill 暂时不可用，请检查千问配置后重新检测"}
    else:
        logger.info("bid-analysis completed bid_id=%s task_id=%s succeeded=%s errors=%s", bid_id, task_id, succeeded, len(errors))
        yield {"type": "done", "task_id": task_id, "status": status, "partial_errors": errors, "message": "标书检查完成"}


def get_results(bid_id: int) -> dict[str, Any]:
    _ensure_prerequisites(bid_id)
    with storage.connect() as conn:
        task_rows = conn.execute(
            "SELECT * FROM bid_analysis_tasks WHERE bid_id = ? ORDER BY create_time DESC",
            (bid_id,),
        ).fetchall()
        result_rows = conn.execute(
            "SELECT * FROM bid_analysis_result WHERE bid_id = ? ORDER BY create_time DESC, result_id DESC",
            (bid_id,),
        ).fetchall()
    tasks = []
    for row in task_rows:
        item = _row_dict(row)
        item["check_items"] = _loads(item["check_items"], [])
        tasks.append(item)
    latest_task_id = tasks[0]["task_id"] if tasks else None
    return {
        "bid_id": bid_id,
        "latest_task_id": latest_task_id,
        "tasks": tasks,
        "results": [_result_dict(row) for row in result_rows],
    }


def resolve_result(bid_id: int, result_id: int, resolved: bool) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT * FROM bid_analysis_result WHERE bid_id = ? AND result_id = ?",
            (bid_id, result_id),
        ).fetchone()
        if row is None:
            raise BidAnalysisServiceError(40402, "检查结果不存在", 404)
        conn.execute(
            "UPDATE bid_analysis_result SET resolved = ?, update_time = ? WHERE bid_id = ? AND result_id = ?",
            (1 if resolved else 0, _now(), bid_id, result_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM bid_analysis_result WHERE result_id = ?", (result_id,)).fetchone()
    return _result_dict(updated)


def export_report(bid_id: int) -> tuple[str, str]:
    with storage.connect() as conn:
        task = conn.execute(
            "SELECT * FROM bid_analysis_tasks WHERE bid_id = ? ORDER BY create_time DESC LIMIT 1",
            (bid_id,),
        ).fetchone()
        bid = conn.execute("SELECT bid_title FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
    if task is None:
        raise BidAnalysisServiceError(40003, "暂无可导出的检查报告")
    report = _build_report(bid_id, str(task["task_id"]))
    with storage.connect() as conn:
        conn.execute(
            "UPDATE bid_analysis_tasks SET report_markdown = ?, update_time = ? WHERE task_id = ?",
            (report, _now(), str(task["task_id"])),
        )
        conn.commit()
    safe_title = "".join(char for char in str(bid["bid_title"] or f"标书{bid_id}") if char not in '\\/:*?\"<>|').strip()
    return report, f"{safe_title or f'标书{bid_id}'}_标书合规检查报告.md"
