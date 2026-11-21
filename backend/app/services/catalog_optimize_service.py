from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import settings
from app.services import qwen_client, rag_case_service
from app.utils.bid_extractor import extract_bid_info
from app.utils.bid_parser import parse_bid_file_to_markdown


logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".doc", ".docx", ".pdf", ".txt", ".md"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


class CatalogOptimizeServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def optimize_catalog_from_uploads(tender_file: UploadFile, catalog_file: UploadFile | None, catalog_text: str = "") -> dict[str, Any]:
    tender_path = await _save_temp_upload(tender_file, "tender")
    catalog_path = await _save_temp_upload(catalog_file, "catalog") if catalog_file else None
    try:
        tender_markdown = _parse_document_text(tender_path)
        existing_catalog = _parse_document_text(catalog_path) if catalog_path else catalog_text.strip()
        if len(tender_markdown) < 120:
            raise CatalogOptimizeServiceError("招标文件未提取到足够内容，请上传文本型 PDF、doc 或 docx 文件")
        if len(existing_catalog) < 10:
            raise CatalogOptimizeServiceError("请上传或粘贴需要优化的目录")

        extracted = extract_bid_info(tender_markdown, "service")
        scoring_items = _normalize_scoring_items(extracted)
        rag_context = _build_rag_catalog_context(tender_markdown, scoring_items)
        fallback = _fallback_catalog_optimization(existing_catalog, scoring_items, rag_context)
        optimized = await _qwen_catalog_optimization(tender_markdown, existing_catalog, scoring_items, rag_context, fallback)
        return optimized
    finally:
        tender_path.unlink(missing_ok=True)
        if catalog_path:
            catalog_path.unlink(missing_ok=True)


async def _save_temp_upload(upload: UploadFile | None, role: str) -> Path:
    if upload is None:
        raise CatalogOptimizeServiceError(f"缺少{role}文件")
    filename = Path((upload.filename or role).replace("\\", "/")).name or role
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise CatalogOptimizeServiceError("文件格式不支持，仅支持 doc、docx、pdf、txt、md")
    content = await upload.read()
    if not content:
        raise CatalogOptimizeServiceError("上传文件不能为空")
    if len(content) > MAX_UPLOAD_SIZE:
        raise CatalogOptimizeServiceError("单个文件不能超过 50MB")
    temp_dir = settings.upload_dir / "catalog_optimize_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / f"{role}-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(content)
    return path


def _parse_document_text(path: Path | None) -> str:
    if path is None:
        return ""
    if path.suffix.lower() in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    return parse_bid_file_to_markdown(str(path))


def _normalize_scoring_items(extracted: dict[str, Any]) -> list[dict[str, str]]:
    raw_items: list[Any] = []
    for key in ("tech_scoring", "tech_scoring_items", "biz_scoring_items"):
        value = extracted.get(key)
        if isinstance(value, list):
            raw_items.extend(value)
    if not raw_items:
        text = str(extracted.get("score_requirements") or "")
        raw_items = [line.strip(" -") for line in text.splitlines() if len(line.strip()) >= 18]
    items: list[dict[str, str]] = []
    for raw in raw_items[:18]:
        if isinstance(raw, dict):
            title = str(raw.get("title") or raw.get("name") or raw.get("category") or "评分要求").strip()
            criteria = str(raw.get("criteria") or raw.get("content") or raw.get("description") or raw.get("requirement") or "").strip()
            score = str(raw.get("score") or raw.get("points") or "").strip()
        else:
            title = "评分要求"
            criteria = str(raw).strip()
            score = ""
        if criteria or title:
            items.append({"title": title[:80], "score": score[:30], "criteria": criteria[:700]})
    return items[:12]


def _build_rag_catalog_context(tender_markdown: str, scoring_items: list[dict[str, str]]) -> list[dict[str, Any]]:
    query = "\n".join([item.get("title", "") + "\n" + item.get("criteria", "") for item in scoring_items]) or tender_markdown[:6000]
    try:
        cases = rag_case_service.retrieve_similar_cases(query, limit=3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog optimize RAG retrieve failed: %s", exc)
        return []
    context: list[dict[str, Any]] = []
    for case in cases:
        snippets = case.get("snippets") if isinstance(case, dict) else []
        context.append(
            {
                "case_name": case.get("case_name"),
                "similarity_percent": case.get("similarity_percent"),
                "snippets": snippets[:2] if isinstance(snippets, list) else [],
            }
        )
    return context


def _clean_catalog_line(raw: Any) -> str:
    """清洗单条目录文本，避免模型返回长段落或带 Markdown 符号导致前端空白。"""
    line = str(raw or "")
    line = re.sub(r"^[\s>*\-•·]+", "", line)
    line = re.sub(r"\s+", " ", line).strip(" \t\r\n,，;；")
    if not line:
        return ""
    # 目录标题过长时保留前半段，避免被直接过滤为空。
    return line[:120]


def _catalog_lines(text: str) -> list[str]:
    normalized = str(text or "")
    # 常见目录来自 Word/PDF 时可能粘成一整段，先按标题编号补换行。
    normalized = re.sub(r"(?<!\n)(第[一二三四五六七八九十百\d]+[章节篇])", r"\n\1", normalized)
    normalized = re.sub(r"(?<!\n)(\d+(?:\.\d+){0,4}[、.．]\s*)", r"\n\1", normalized)
    normalized = re.sub(r"(?<!\n)([一二三四五六七八九十]+[、.．]\s*)", r"\n\1", normalized)

    lines: list[str] = []
    for raw in re.split(r"\n+|(?<=[。；;])\s*", normalized):
        line = _clean_catalog_line(raw)
        if 2 <= len(line):
            lines.append(line)

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = re.sub(r"\s+", "", line)
        if key and key not in seen:
            seen.add(key)
            deduped.append(line)
    return deduped[:80]


def _normalize_catalog_payload(value: Any) -> list[str]:
    """兼容 Qwen 可能返回的字符串、数组、章节对象、嵌套 children/items 等格式。"""
    lines: list[str] = []

    def walk(node: Any, depth: int = 0) -> None:
        if node is None:
            return
        if isinstance(node, str):
            for line in _catalog_lines(node):
                lines.append(("  " * min(depth, 3)) + line)
            return
        if isinstance(node, (int, float)):
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth)
            return
        if isinstance(node, dict):
            title = (
                node.get("title")
                or node.get("name")
                or node.get("chapter")
                or node.get("heading")
                or node.get("section")
                or node.get("目录")
                or node.get("章节")
            )
            if title:
                cleaned = _clean_catalog_line(title)
                if cleaned:
                    lines.append(("  " * min(depth, 3)) + cleaned)
            for key in ("children", "items", "sections", "subsections", "chapters", "catalog", "目录", "子目录"):
                if key in node:
                    walk(node.get(key), depth + (1 if title else 0))
            return
        for line in _catalog_lines(str(node)):
            lines.append(("  " * min(depth, 3)) + line)

    walk(value)

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = re.sub(r"\s+", "", line)
        if key and key not in seen:
            seen.add(key)
            deduped.append(line)
    return deduped[:80]


def _fallback_catalog_optimization(existing_catalog: str, scoring_items: list[dict[str, str]], rag_context: list[dict[str, Any]]) -> dict[str, Any]:
    original = _catalog_lines(existing_catalog)
    coverage: list[dict[str, str]] = []
    optimized = list(original)
    compact_catalog = "".join(original)
    for item in scoring_items[:10]:
        title = item.get("title") or "评分要求"
        terms = [term for term in rag_case_service._search_tokens(f"{title}\n{item.get('criteria', '')}") if len(term) >= 3][:8]
        matched = any(term in compact_catalog for term in terms)
        if matched:
            status = "已覆盖"
            suggestion = "保留现有目录表达，可在正文中强化响应。"
        else:
            status = "建议补充"
            suggestion = f"建议新增或强化章节：{title}"
            if title not in optimized:
                optimized.append(title)
        coverage.append({"score_point": title, "status": status, "suggestion": suggestion})
    return {
        "summary": "已基于招标评分点、历史案例和现有目录完成初步优化。",
        "optimized_catalog": optimized[:80],
        "coverage": coverage,
        "rag_cases": rag_context,
        "warnings": ["部分 PDF 若为扫描件，目录优化会受文本提取质量影响。"],
        "llm_used": False,
    }


async def _qwen_catalog_optimization(
    tender_markdown: str,
    existing_catalog: str,
    scoring_items: list[dict[str, str]],
    rag_context: list[dict[str, Any]],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    prompt = (
        "你是资深投标文件目录优化顾问。请基于招标文件评分点、历史案例参考和用户已有目录，优化投标文件目录。\n"
        "要求：保留用户已有合理章节；补齐空缺目录；强化评分点覆盖；目录标题正式、可直接用于标书；不要生成正文。\n"
        "只返回JSON对象，字段为 summary、optimized_catalog、coverage、rag_cases、warnings。\n"
        "optimized_catalog必须是字符串数组，每一项是一行目录标题，不要返回空数组，不要返回正文段落。\n"
        "coverage每项包含 score_point、status、suggestion。\n\n"
        f"招标文件摘录：\n{tender_markdown[:10000]}\n\n"
        f"评分点：\n{json.dumps(scoring_items, ensure_ascii=False)}\n\n"
        f"用户已有目录：\n{existing_catalog[:6000]}\n\n"
        f"历史案例参考：\n{json.dumps(rag_context, ensure_ascii=False)[:5000]}"
    )
    result = await qwen_client.chat_completion(
        [{"role": "system", "content": "只输出合法JSON，不输出解释。"}, {"role": "user", "content": prompt}],
        temperature=0.15, scene="catalog",
        max_tokens=3200,
        json_mode=True,
        fallback=json.dumps(fallback, ensure_ascii=False),
    )
    try:
        parsed = json.loads(result.content[result.content.find("{") : result.content.rfind("}") + 1])
        if isinstance(parsed, dict):
            parsed["llm_used"] = bool(result.usedApi)
            parsed["rag_cases"] = parsed.get("rag_cases") or rag_context
            catalog_payload = (
                parsed.get("optimized_catalog")
                or parsed.get("optimizedCatalog")
                or parsed.get("catalog")
                or parsed.get("chapters")
                or parsed.get("目录")
                or parsed.get("优化目录")
            )
            parsed["optimized_catalog"] = _normalize_catalog_payload(catalog_payload)
            if not parsed["optimized_catalog"]:
                logger.warning("catalog optimize Qwen returned empty catalog; using fallback catalog")
                parsed["optimized_catalog"] = fallback.get("optimized_catalog") or _catalog_lines(existing_catalog)
            parsed["coverage"] = parsed.get("coverage") if isinstance(parsed.get("coverage"), list) else fallback["coverage"]
            parsed["warnings"] = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else fallback["warnings"]
            parsed["summary"] = str(parsed.get("summary") or fallback["summary"])
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog optimize Qwen response parse failed: %s", exc)
    return fallback
