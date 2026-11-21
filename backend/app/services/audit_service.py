from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import pdfplumber
from docx import Document
from fastapi import UploadFile

from app.core.config import settings
from app.services import qwen_client, storage
from app.utils.bid_parser.parser import parse_bid_document


class AuditServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}
DEFAULT_CHECKS = ["rejection", "scoring", "qualification", "format", "business"]
CHECK_LABELS = {
    "rejection": "废标风险",
    "scoring": "评分响应",
    "qualification": "资质校验",
    "format": "格式规范",
    "business": "商务一致性",
    "suggestion": "优化建议",
}

REJECTION_KEYWORDS = ["废标", "无效", "否决", "不予受理", "重大偏差", "实质性偏离", "资格不符", "未响应", "投标保证金", "串通", "弄虚作假"]
SCORING_KEYWORDS = ["评分", "得分", "分值", "评审", "技术方案", "服务方案", "实施方案", "售后", "配送", "质量保障", "应急", "食品安全"]
QUALIFICATION_KEYWORDS = ["营业执照", "食品经营许可证", "许可证", "资质", "证书", "体系认证", "健康证", "人员", "业绩", "合同", "车辆", "冷库"]
FORMAT_KEYWORDS = ["目录", "页码", "字体", "字号", "行距", "签字", "盖章", "密封", "正本", "副本", "偏离表", "承诺函"]
BUSINESS_KEYWORDS = ["报价", "服务期限", "合同履行", "交付", "质量标准", "响应时间", "付款", "配送时间", "承诺", "违约"]


def ensure_schema() -> None:
    storage.init_db()
    with storage.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bid_audit_result (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bid_id INTEGER,
                tender_file_name TEXT,
                tender_file_path TEXT,
                tender_file_size INTEGER NOT NULL DEFAULT 0,
                bid_file_name TEXT,
                bid_file_path TEXT,
                bid_file_size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                progress INTEGER NOT NULL DEFAULT 0,
                current_step TEXT,
                checks_json TEXT NOT NULL DEFAULT '[]',
                tender_text TEXT,
                bid_text TEXT,
                total_risk_count INTEGER NOT NULL DEFAULT 0,
                high_count INTEGER NOT NULL DEFAULT 0,
                medium_count INTEGER NOT NULL DEFAULT 0,
                low_count INTEGER NOT NULL DEFAULT 0,
                suggestion_count INTEGER NOT NULL DEFAULT 0,
                issues_json TEXT NOT NULL DEFAULT '[]',
                summary_json TEXT NOT NULL DEFAULT '{}',
                used_ai INTEGER NOT NULL DEFAULT 0,
                ai_error TEXT,
                create_time TEXT NOT NULL,
                update_time TEXT NOT NULL,
                generated_time TEXT
            )
            """
        )
        conn.commit()


def _now() -> str:
    return storage.now_utc().isoformat()


def _audit_root() -> Path:
    root = settings.upload_dir / "audit"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stored_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(settings.backend_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _resolve_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    return path if path.is_absolute() else (settings.backend_root / path).resolve()


async def upload_document(role: str, file: UploadFile, audit_id: int | None = None) -> dict[str, Any]:
    ensure_schema()
    if role not in {"tender", "bid"}:
        raise AuditServiceError(40001, "未知审核文件类型")
    filename = Path(file.filename or "uploaded").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise AuditServiceError(40002, "仅支持 PDF/DOC/DOCX 文件")
    content = await file.read()
    if not content:
        raise AuditServiceError(40003, "上传文件为空")

    now = _now()
    with storage.connect() as conn:
        if audit_id:
            row = conn.execute("SELECT audit_id FROM bid_audit_result WHERE audit_id = ?", (audit_id,)).fetchone()
            if row is None:
                raise AuditServiceError(40401, "审核任务不存在", 404)
        else:
            cursor = conn.execute(
                "INSERT INTO bid_audit_result (status, checks_json, create_time, update_time) VALUES ('draft', ?, ?, ?)",
                (json.dumps(DEFAULT_CHECKS, ensure_ascii=False), now, now),
            )
            audit_id = int(cursor.lastrowid)

        target_dir = _audit_root() / str(audit_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{role}_{uuid.uuid4().hex}{suffix}"
        target.write_bytes(content)

        if role == "tender":
            conn.execute(
                """
                UPDATE bid_audit_result
                SET tender_file_name = ?, tender_file_path = ?, tender_file_size = ?,
                    status = 'uploaded', progress = 0, current_step = NULL,
                    total_risk_count = 0, high_count = 0, medium_count = 0, low_count = 0,
                    suggestion_count = 0, issues_json = '[]', summary_json = '{}', update_time = ?
                WHERE audit_id = ?
                """,
                (filename, _stored_path(target), len(content), now, audit_id),
            )
        else:
            conn.execute(
                """
                UPDATE bid_audit_result
                SET bid_file_name = ?, bid_file_path = ?, bid_file_size = ?,
                    status = 'uploaded', progress = 0, current_step = NULL,
                    total_risk_count = 0, high_count = 0, medium_count = 0, low_count = 0,
                    suggestion_count = 0, issues_json = '[]', summary_json = '{}', update_time = ?
                WHERE audit_id = ?
                """,
                (filename, _stored_path(target), len(content), now, audit_id),
            )
        conn.commit()
    return {"audit_id": audit_id, "file_name": filename, "file_size": len(content), "role": role, "status": "uploaded"}


def validate_audit_ready(audit_id: int) -> None:
    row = _get_row(audit_id)
    tender_path = _resolve_path(row["tender_file_path"])
    bid_path = _resolve_path(row["bid_file_path"])
    if not tender_path or not tender_path.exists() or not bid_path or not bid_path.exists():
        raise AuditServiceError(40011, "请先上传招标文件和待审核投标文件")


async def run_audit_stream(audit_id: int, checks: list[str]) -> AsyncIterator[dict[str, Any]]:
    ensure_schema()
    checks = [item for item in checks if item in DEFAULT_CHECKS] or DEFAULT_CHECKS
    row = _get_row(audit_id)
    tender_path = _resolve_path(row["tender_file_path"])
    bid_path = _resolve_path(row["bid_file_path"])
    if not tender_path or not tender_path.exists() or not bid_path or not bid_path.exists():
        raise AuditServiceError(40011, "请先上传招标文件和待审核投标文件")

    _update_status(audit_id, "processing", 5, "准备审核", checks=checks)
    yield {"type": "progress", "progress": 8, "step": "解析招标文件"}
    tender_text = _parse_document(tender_path)

    _update_status(audit_id, "processing", 22, "解析投标文件", tender_text=tender_text)
    yield {"type": "progress", "progress": 25, "step": "解析投标文件"}
    bid_text = _parse_document(bid_path)

    _update_status(audit_id, "processing", 42, "废标风险检查", bid_text=bid_text)
    yield {"type": "progress", "progress": 45, "step": "废标风险检查"}
    rule_issues, extracted = _rule_audit(tender_text, bid_text, checks)

    _update_status(audit_id, "processing", 64, "评分响应校验")
    yield {"type": "progress", "progress": 68, "step": "评分响应校验"}
    ai_issues, used_ai, ai_error = await _ai_audit(tender_text, bid_text, checks, extracted, rule_issues)

    _update_status(audit_id, "processing", 86, "生成报告")
    yield {"type": "progress", "progress": 88, "step": "生成报告"}
    issues = _dedupe_issues([*rule_issues, *ai_issues])[:80]
    result = _save_result(audit_id, issues, extracted, used_ai, ai_error)
    yield {"type": "done", "progress": 100, "step": "审核完成", "result": result}


def get_result(audit_id: int) -> dict[str, Any]:
    ensure_schema()
    return _row_to_result(_get_row(audit_id))


def export_markdown(audit_id: int) -> tuple[str, str]:
    result = get_result(audit_id)
    summary = result["summary"]
    lines = [
        f"# 标书智能审核报告 #{audit_id}",
        "",
        f"- 招标文件：{result.get('tender_file_name') or '-'}",
        f"- 投标文件：{result.get('bid_file_name') or '-'}",
        f"- 生成时间：{result.get('generated_time') or result.get('update_time') or '-'}",
        f"- 高风险：{summary['high_count']} 项；中风险：{summary['medium_count']} 项；低风险：{summary['low_count']} 项；优化建议：{summary['suggestion_count']} 项",
        "",
    ]
    if not result["issues"]:
        lines.append("暂未发现明确问题。")
    for index, issue in enumerate(result["issues"], start=1):
        lines.extend(
            [
                f"## {index}. [{_severity_label(issue.get('severity'))}] {issue.get('title') or '审核问题'}",
                "",
                f"- 分类：{CHECK_LABELS.get(issue.get('category'), issue.get('category') or '-')}",
                f"- 问题详情：{issue.get('detail') or issue.get('summary') or '-'}",
                f"- 招标依据：{issue.get('tender_basis') or '-'}",
                f"- 标书位置：{issue.get('bid_location') or '-'}",
                f"- 整改建议：{issue.get('suggestion') or '-'}",
                "",
            ]
        )
    return "\n".join(lines), f"标书智能审核报告_{audit_id}.md"


def _get_row(audit_id: int) -> Any:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_audit_result WHERE audit_id = ?", (audit_id,)).fetchone()
    if row is None:
        raise AuditServiceError(40401, "审核任务不存在", 404)
    return row


def _update_status(audit_id: int, status: str, progress: int, step: str | None, **extra: Any) -> None:
    assignments = ["status = ?", "progress = ?", "current_step = ?", "update_time = ?"]
    params: list[Any] = [status, progress, step, _now()]
    if "checks" in extra:
        assignments.append("checks_json = ?")
        params.append(json.dumps(extra["checks"], ensure_ascii=False))
    if "tender_text" in extra:
        assignments.append("tender_text = ?")
        params.append(extra["tender_text"])
    if "bid_text" in extra:
        assignments.append("bid_text = ?")
        params.append(extra["bid_text"])
    params.append(audit_id)
    with storage.connect() as conn:
        conn.execute(f"UPDATE bid_audit_result SET {', '.join(assignments)} WHERE audit_id = ?", params)
        conn.commit()


def _save_result(audit_id: int, issues: list[dict[str, Any]], extracted: dict[str, Any], used_ai: bool, ai_error: str | None) -> dict[str, Any]:
    high = sum(1 for item in issues if item.get("severity") == "high")
    medium = sum(1 for item in issues if item.get("severity") == "medium")
    low = sum(1 for item in issues if item.get("severity") == "low")
    suggestions = sum(1 for item in issues if item.get("severity") == "suggestion" or item.get("category") == "suggestion")
    summary = {
        "high_count": high,
        "medium_count": medium,
        "low_count": low,
        "suggestion_count": suggestions,
        "by_category": dict(_count_by(issues, "category")),
        "extracted": extracted,
    }
    now = _now()
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE bid_audit_result
            SET status = 'completed', progress = 100, current_step = '审核完成',
                total_risk_count = ?, high_count = ?, medium_count = ?, low_count = ?, suggestion_count = ?,
                issues_json = ?, summary_json = ?, used_ai = ?, ai_error = ?, generated_time = ?, update_time = ?
            WHERE audit_id = ?
            """,
            (
                len(issues),
                high,
                medium,
                low,
                suggestions,
                json.dumps(issues, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                1 if used_ai else 0,
                ai_error,
                now,
                now,
                audit_id,
            ),
        )
        conn.commit()
    return get_result(audit_id)


def _parse_document(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        parsed = parse_bid_document(str(path))
        if parsed.full_markdown.strip():
            return parsed.full_markdown[:220000]
        if parsed.raw_text.strip():
            return parsed.raw_text[:220000]
    except Exception as exc:
        print(f"[Audit] parser fallback for {path.name}: {exc}", flush=True)

    if suffix == ".pdf":
        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(f"【第{index}页】\n{text}")
        return "\n\n".join(pages)[:220000]

    if suffix in {".docx", ".doc"}:
        try:
            doc = Document(str(path))
        except Exception as exc:
            raise AuditServiceError(40012, f"文档解析失败：{exc}") from exc
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines)[:220000]
    return ""


def _rule_audit(tender_text: str, bid_text: str, checks: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    extracted = {
        "invalid_clauses": _line_candidates(tender_text, REJECTION_KEYWORDS, 16),
        "scoring_items": _line_candidates(tender_text, SCORING_KEYWORDS, 18),
        "qualification_items": _line_candidates(tender_text, QUALIFICATION_KEYWORDS, 16),
        "format_items": _line_candidates(tender_text, FORMAT_KEYWORDS, 10),
        "business_items": _line_candidates(tender_text, BUSINESS_KEYWORDS, 12),
    }

    if "rejection" in checks:
        for clause in extracted["invalid_clauses"][:8]:
            key = _best_keyword(clause, REJECTION_KEYWORDS)
            if key and key not in bid_text:
                issues.append(
                    _issue(
                        "rejection",
                        "high",
                        f"疑似未响应{key}条款",
                        "招标文件存在废标/无效相关要求，投标文件未检索到明确响应线索。",
                        clause,
                        f"未检索到关键词：{key}",
                        "补充逐条响应说明，并核对资格、报价、偏离表及承诺章节。",
                    )
                )

    if "scoring" in checks:
        missed = 0
        scoring_items = extracted["scoring_items"][:12]
        for item in scoring_items:
            key = _best_keyword(item, SCORING_KEYWORDS)
            if key and key not in bid_text:
                missed += 1
                issues.append(
                    _issue(
                        "scoring",
                        "medium",
                        f"评分点响应不足：{key}",
                        "评分项在投标文件中缺少明确章节或关键词响应。",
                        item,
                        f"未检索到评分关键词：{key}",
                        "在目录和正文中增加对应响应章节，并补充可量化措施、案例和证明材料。",
                    )
                )
        if scoring_items and missed / max(1, len(scoring_items)) > 0.35:
            issues.append(
                _issue(
                    "suggestion",
                    "suggestion",
                    "评分覆盖率偏低",
                    "多个评分关键词缺少直接响应线索，建议按评分表逐项补强。",
                    "评分标准汇总",
                    "目录/正文覆盖不足",
                    "建立评分点-章节映射表，逐项检查是否有标题、正文、证明材料和页码。",
                )
            )

    if "qualification" in checks:
        for keyword in QUALIFICATION_KEYWORDS[:10]:
            if keyword in tender_text and keyword not in bid_text:
                issues.append(
                    _issue(
                        "qualification",
                        "high",
                        f"资质材料缺失线索：{keyword}",
                        "招标文件提及该资质或证明材料，投标文件未检索到对应文本线索。",
                        _near_text(tender_text, keyword),
                        f"未检索到：{keyword}",
                        "补充对应证照/证明材料，核对有效期，并在目录和附件清单中列明。",
                    )
                )

    if "format" in checks:
        for keyword in FORMAT_KEYWORDS[:8]:
            if keyword in tender_text and keyword not in bid_text:
                severity = "medium" if keyword in {"目录", "偏离表", "盖章", "签字"} else "low"
                issues.append(
                    _issue(
                        "format",
                        severity,
                        f"格式要求需复核：{keyword}",
                        "招标文件存在格式或装订要求，投标文件文本未出现对应线索。",
                        _near_text(tender_text, keyword),
                        f"未检索到：{keyword}",
                        "按招标文件格式要求复核电子版与最终盖章版，必要时补充说明或检查清单。",
                    )
                )

    if "business" in checks:
        for keyword in BUSINESS_KEYWORDS[:10]:
            if keyword in tender_text and keyword not in bid_text:
                issues.append(
                    _issue(
                        "business",
                        "medium",
                        f"商务条款响应缺失：{keyword}",
                        "商务或服务承诺类要求未检索到明确响应。",
                        _near_text(tender_text, keyword),
                        f"未检索到：{keyword}",
                        "在商务响应、服务承诺或偏离表中补充一致性表述，避免前后承诺不一致。",
                    )
                )
    return issues, extracted


async def _ai_audit(tender_text: str, bid_text: str, checks: list[str], extracted: dict[str, Any], rule_issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, str | None]:
    prompt = (
        "你是标书智能审核专家。参考 OpenBidKit 的废标检查思路：先限定电子文件可判断范围，再逐项核查，最后去重定稿。\n"
        "请围绕废标风险、评分响应、资质校验、格式规范、商务一致性输出 JSON。"
        "不得把纸质密封、现场递交等无法从电子文件判断的事项写成确定风险，只能列为复核建议。\n"
        "JSON格式：{\"issues\":[{\"category\":\"rejection|scoring|qualification|format|business|suggestion\","
        "\"severity\":\"high|medium|low|suggestion\",\"title\":\"标题\",\"detail\":\"问题详情\","
        "\"tender_basis\":\"招标依据\",\"bid_location\":\"标书位置或缺失说明\",\"suggestion\":\"整改建议\"}]}。\n"
        f"启用检查项：{checks}\n"
        f"招标文件候选要求：{json.dumps(extracted, ensure_ascii=False)[:6000]}\n"
        f"规则初筛问题：{json.dumps(rule_issues[:30], ensure_ascii=False)[:8000]}\n"
        f"招标文件原文节选：{tender_text[:12000]}\n"
        f"投标文件原文节选：{bid_text[:16000]}"
    )
    fallback = json.dumps({"issues": []}, ensure_ascii=False)
    result = await qwen_client.chat_completion(
        [{"role": "user", "content": prompt}], scene="audit",
        temperature=0.12,
        max_tokens=3500,
        fallback=fallback,
        json_mode=True,
    )
    payload = _extract_json(result.content)
    issues = [_normalize_ai_issue(item) for item in payload.get("issues", []) if isinstance(item, dict)] if isinstance(payload, dict) else []
    return [item for item in issues if item], result.usedApi, result.error


def _line_candidates(text: str, keywords: list[str], limit: int) -> list[str]:
    lines = re.split(r"[\n。；;]", text or "")
    found: list[str] = []
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        if len(compact) < 8:
            continue
        if any(keyword in compact for keyword in keywords):
            found.append(compact[:260])
        if len(found) >= limit:
            break
    return list(dict.fromkeys(found))


def _best_keyword(text: str, keywords: list[str]) -> str:
    for keyword in keywords:
        if keyword in text:
            return keyword
    return ""


def _near_text(text: str, keyword: str, width: int = 160) -> str:
    index = text.find(keyword)
    if index < 0:
        return keyword
    return re.sub(r"\s+", " ", text[max(0, index - width): index + width]).strip()


def _issue(category: str, severity: str, title: str, detail: str, tender_basis: str, bid_location: str, suggestion: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "category": category,
        "severity": severity,
        "title": title[:80],
        "detail": detail,
        "tender_basis": tender_basis,
        "bid_location": bid_location,
        "suggestion": suggestion,
    }


def _normalize_ai_issue(item: dict[str, Any]) -> dict[str, Any] | None:
    category = str(item.get("category") or "suggestion")
    if category not in CHECK_LABELS:
        category = "suggestion"
    severity = str(item.get("severity") or "low")
    if severity not in {"high", "medium", "low", "suggestion"}:
        severity = "low"
    title = str(item.get("title") or "AI审核建议").strip()
    if not title:
        return None
    return _issue(
        category,
        severity,
        title,
        str(item.get("detail") or item.get("summary") or ""),
        str(item.get("tender_basis") or item.get("requirement") or ""),
        str(item.get("bid_location") or item.get("bidEvidence") or ""),
        str(item.get("suggestion") or "请人工复核并补充响应。"),
    )


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    order = {"high": 0, "medium": 1, "low": 2, "suggestion": 3}
    for issue in sorted(issues, key=lambda item: (order.get(item.get("severity"), 9), item.get("category") or "")):
        key = f"{issue.get('category')}|{issue.get('title')}|{issue.get('bid_location')}"
        if key in seen:
            continue
        seen.add(key)
        issue["id"] = issue.get("id") or uuid.uuid4().hex[:10]
        result.append(issue)
    return result


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {"issues": []}


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _row_to_result(row: Any) -> dict[str, Any]:
    issues = json.loads(row["issues_json"] or "[]")
    summary = json.loads(row["summary_json"] or "{}")
    summary.update(
        {
            "high_count": int(row["high_count"] or 0),
            "medium_count": int(row["medium_count"] or 0),
            "low_count": int(row["low_count"] or 0),
            "suggestion_count": int(row["suggestion_count"] or 0),
            "total": int(row["total_risk_count"] or 0),
        }
    )
    return {
        "audit_id": int(row["audit_id"]),
        "bid_id": row["bid_id"],
        "tender_file_name": row["tender_file_name"],
        "bid_file_name": row["bid_file_name"],
        "status": row["status"],
        "progress": int(row["progress"] or 0),
        "current_step": row["current_step"],
        "checks": json.loads(row["checks_json"] or "[]"),
        "summary": summary,
        "issues": issues,
        "used_ai": bool(row["used_ai"]),
        "ai_error": row["ai_error"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
        "generated_time": row["generated_time"],
    }


def _severity_label(value: str | None) -> str:
    return {"high": "高风险", "medium": "中风险", "low": "低风险", "suggestion": "优化建议"}.get(value or "", "问题")


def cleanup_audit_files(audit_id: int) -> None:
    path = _audit_root() / str(audit_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
