from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.services import qwen_client


logger = logging.getLogger(__name__)

CATEGORY_CONFIG = {
    "project_overview": {
        "name": "项目概况",
        "schema": {
            "project_name": "",
            "buyer": "",
            "agency": "",
            "service_scope": "",
            "service_period": "",
            "budget": "",
            "core_goals": [],
            "sources": [],
        },
        "instruction": "提取项目名称、采购单位、代理机构、服务范围、服务周期、预算金额、核心目标。",
    },
    "scoring": {
        "name": "评分办法",
        "schema": {"tech_scoring_items": [], "biz_scoring_items": [], "price_scoring_items": [], "sources": []},
        "instruction": "完整提取技术评分项、商务评分项、价格评分项，必须保留评分项名称、分值、评审标准、优先级和来源。",
    },
    "qualification": {
        "name": "资格要求",
        "schema": {"qualification_requirements": [], "sources": []},
        "instruction": "提取资质、人员、业绩、财务、证书、联合体、其他资格条件。",
    },
    "commercial": {
        "name": "商务要求",
        "schema": {"commercial_requirements": [], "sources": []},
        "instruction": "提取服务周期、付款方式、履约、验收、售后、质保、合同与商务条款。",
    },
    "invalid": {
        "name": "废标/否决条款",
        "schema": {"invalid_bid_clauses": [], "sources": []},
        "instruction": "提取废标、否决投标、无效投标、不予通过、资格/符合性审查失败情形。",
    },
}


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _report_progress(callback: ProgressCallback | None, key: str, status: str, attempt: int = 0) -> None:
    if callback is None:
        return
    try:
        await callback({"key": key, "status": status, "attempt": attempt})
    except Exception:
        # Progress is optional telemetry; it must not discard extraction output.
        logger.warning("extraction progress callback failed key=%s", key)


async def extract_bid_info_with_ai(
    *,
    full_markdown: str,
    chapter_blocks: list[dict[str, Any]],
    located_blocks: dict[str, Any],
    rule_result: dict[str, Any],
    bid_type: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run extraction and report actual category outcomes, without document text."""
    if not settings.enable_llm_parse_optimize or not settings.qwen_api_key.strip():
        logger.warning("AI parse disabled or API key missing; using rule fallback")
        # Rules are already prepared by the caller. All local category results
        # become available together, so report their completion without delays.
        result = _fallback_from_rules(rule_result, located_blocks)
        for category in CATEGORY_CONFIG:
            await _report_progress(progress_callback, category, "fallback")
        await _report_progress(progress_callback, "summary", "fallback")
        return result

    category_results: dict[str, Any] = {}
    used_api = False
    degraded_categories: list[str] = []
    for category in CATEGORY_CONFIG:
        blocks = (located_blocks.get("categories") or {}).get(category) or []
        try:
            result, meta = await _extract_category_with_retry(category, blocks, bid_type, progress_callback)
            category_results[category] = result
            used_api = used_api or bool(meta.get("used_api"))
            degraded = bool(meta.get("error") or not meta.get("used_api"))
            if degraded:
                degraded_categories.append(category)
            await _report_progress(progress_callback, category, "fallback" if degraded else "completed", int(meta.get("attempt") or 1))
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI category extraction failed category=%s", category)
            degraded_categories.append(category)
            category_results[category] = _fallback_category(category, rule_result, blocks, str(exc))
            await _report_progress(progress_callback, category, "fallback", 2)

    await _report_progress(progress_callback, "summary", "running", 1)
    try:
        summary, summary_meta = await _summarize_results(category_results, full_markdown, bid_type)
        used_api = used_api or bool(summary_meta.get("used_api"))
        degraded = bool(summary_meta.get("error") or not summary_meta.get("used_api"))
        if degraded:
            logger.warning("AI summary degraded")
            degraded_categories.append("summary")
        await _report_progress(progress_callback, "summary", "fallback" if degraded else "completed", 1)
    except Exception:  # noqa: BLE001
        logger.exception("AI summary failed; using local merge")
        summary = _local_summary(category_results, rule_result)
        degraded_categories.append("summary")
        await _report_progress(progress_callback, "summary", "fallback", 1)

    merged = _normalize_final_result(category_results, summary, rule_result, used_api, degraded_categories)
    logger.info("AI parse completed used_api=%s degraded=%s", used_api, degraded_categories)
    return merged


async def _extract_category_with_retry(
    category: str, blocks: list[dict[str, Any]], bid_type: str,
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error = None
    for attempt in range(1, 3):
        await _report_progress(progress_callback, category, "running", attempt)
        started = time.perf_counter()
        context = _blocks_to_context(blocks)
        fallback = json.dumps(_empty_category_result(category, blocks), ensure_ascii=False)
        try:
            result = await qwen_client.chat_completion(
                [
                    {"role": "system", "content": "你是招投标专业分析师。只基于给定原文提取，不编造，只输出合法 JSON。"},
                    {"role": "user", "content": _category_prompt(category, context, bid_type)},
                ],
                temperature=0.0, max_tokens=6500, fallback=fallback, json_mode=True, scene="tender_parse",
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info("AI parse category=%s attempt=%s duration_ms=%s used_api=%s", category, attempt, duration_ms, result.usedApi)
            parsed = _loads_json(result.content)
            return parsed, {"used_api": result.usedApi, "error": result.error, "duration_ms": duration_ms, "attempt": attempt}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("AI category extraction retry category=%s attempt=%s", category, attempt)
            if attempt < 2:
                await asyncio.sleep(0.2)
    raise ValueError(f"category {category} AI extraction failed") from last_error


async def _summarize_results(category_results: dict[str, Any], full_markdown: str, bid_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.dumps(category_results, ensure_ascii=False, indent=2)
    local = _local_summary(category_results, {})
    fallback = json.dumps({"bid_key_points": local.get("bid_key_points") or {}}, ensure_ascii=False)
    started = time.perf_counter()
    result = await qwen_client.chat_completion(
        [
            {"role": "system", "content": "你是资深投标策略顾问。只输出合法 JSON。"},
            {
                "role": "user",
                "content": (
                    "请对以下分块提取结果去重、合并、排序，并生成投标策略结论。\n"
                    "只输出 bid_key_points，不要重复输出各分类的详细条目。\n"
                    "bid_key_points 必须包含 core_points、high_score_priorities、catalog_suggestions、invalid_risks，"
                    "每个数组 3-8 条，每条不超过 120 字。不得编造原文没有的内容。\n\n"
                    f"标书类型：{bid_type}\n\n分块结果：\n{payload}\n\n原文参考片段：\n{_clip(full_markdown, 6000)}"
                ),
            },
        ],
        temperature=0.0,
        scene="tender_parse",
        max_tokens=2500,
        fallback=fallback,
        json_mode=True,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info("AI parse summary duration_ms=%s used_api=%s error=%s", duration_ms, result.usedApi, result.error)
    return _loads_json(result.content), {"used_api": result.usedApi, "error": result.error, "duration_ms": duration_ms}


def _category_prompt(category: str, context: str, bid_type: str) -> str:
    config = CATEGORY_CONFIG[category]
    return (
        f"当前任务：{config['name']}结构化提取。\n"
        f"标书类型：{bid_type}\n"
        f"提取要求：{config['instruction']}\n"
        "必须为每条信息标注 source_title/source_location；评分项必须保留 score；表格内容不得遗漏。\n"
        "禁止扩写、禁止主观补充、禁止输出原文没有的要求。合并重复项，最多输出 40 条，每条内容不超过 500 字。\n"
        f"输出 JSON schema 示例：{json.dumps(config['schema'], ensure_ascii=False)}\n\n"
        f"候选章节原文：\n{context}"
    )


def _blocks_to_context(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return ""
    parts: list[str] = []
    for block in blocks[:8]:
        title = block.get("chapter_title") or "未命名章节"
        page_range = block.get("page_range") or []
        content = str(block.get("content") or "")
        parts.append(f"<!-- block:{block.get('block_id')} title:{title} page:{page_range} -->\n{_clip(content, 12000)}")
    return "\n\n---\n\n".join(parts)[:36000]


def _loads_json(content: str) -> dict[str, Any]:
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
        raise ValueError("AI response is not a JSON object")
    return data


def _normalize_final_result(
    category_results: dict[str, Any],
    summary: dict[str, Any],
    rule_result: dict[str, Any],
    used_api: bool,
    degraded_categories: list[str],
) -> dict[str, Any]:
    local = _local_summary(category_results, rule_result)
    project_basic_info = _pick_dict(summary.get("project_basic_info"), local.get("project_basic_info"))
    tech_items = _merge_scoring_by_title(local.get("tech_scoring_items"), summary.get("tech_scoring_items"))
    biz_items = _merge_scoring_by_title(local.get("biz_scoring_items"), summary.get("biz_scoring_items"), summary.get("price_scoring_items"))
    tech_items, biz_items = _reconcile_scoring(tech_items, biz_items, rule_result)
    qualifications = _dedupe_items(_pick_list(summary.get("qualification_requirements"), local.get("qualification_requirements")))
    invalid = _dedupe_items(_pick_list(summary.get("invalid_bid_clauses"), local.get("invalid_bid_clauses")))
    key_points = _pick_dict(summary.get("bid_key_points"), local.get("bid_key_points"))

    return {
        "project_basic_info": project_basic_info,
        "tech_scoring_items": _sort_scoring_items(tech_items),
        "biz_scoring_items": _sort_scoring_items(biz_items),
        "qualification_requirements": qualifications,
        "invalid_bid_clauses": invalid,
        "bid_key_points": key_points,
        "used_api": used_api,
        "degraded_categories": degraded_categories,
        "parsed_project_overview": _project_overview_text(project_basic_info),
        "parsed_score_requirements": _scoring_text(_sort_scoring_items(tech_items), _sort_scoring_items(biz_items)),
    }


def _merge_scoring_by_title(*groups):
    result, seen = [], set()
    for group in groups:
        for item in _dedupe_items(_to_items(group)):
            key = re.sub(r"\s+", "", item["title"])
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    return result


def _reconcile_scoring(tech, biz, rule_result):
    """Keep complete source tables intact when the model summarizes away rows.

    A full 100-point table is authoritative for names, maxima and source text.
    AI remains the fallback when the source cannot be reconstructed completely.
    """
    raw = (rule_result.get("raw") or {}).get("scoring") or {}
    rows = [item for item in raw.get("items", []) if item.get("source_text") and item.get("source_line")]
    if not rows or abs(sum(_score_value(item.get("score")) for item in rows) - 100) > 0.01:
        return tech, biz
    known = {re.sub(r"\s+", "", item.get("title", "")): item for item in [*tech, *biz]}
    merged_tech, merged_biz = [], []
    for row in rows:
        key = re.sub(r"\s+", "", row.get("title", ""))
        item = {**known.get(key, {}), **row}
        item["criteria"] = row.get("text") or row["source_text"]
        item["score"] = _score_value(row.get("score"))
        item["source_location"] = f"第{row.get('source_page', '?')}页" if row.get('source_page_end') in (None, row.get('source_page')) else f"第{row.get('source_page', '?')}-{row['source_page_end']}页"
        item["extraction_source"] = "source_scoring_table"
        (merged_biz if row.get("category") in {"商务", "价格"} else merged_tech).append(item)
    return merged_tech, merged_biz


def _local_summary(category_results: dict[str, Any], rule_result: dict[str, Any]) -> dict[str, Any]:
    project = category_results.get("project_overview") or {}
    scoring = category_results.get("scoring") or {}
    qualification = category_results.get("qualification") or {}
    commercial = category_results.get("commercial") or {}
    invalid = category_results.get("invalid") or {}

    tech = _pick_list(scoring.get("tech_scoring_items"), scoring.get("tech_scoring"), rule_result.get("parsed_tech_scoring"))
    biz_score = _pick_list(
        scoring.get("biz_scoring_items"),
        scoring.get("business_scoring_items"),
        scoring.get("biz_scoring"),
        rule_result.get("biz_scoring_items"),
    )
    biz_score = _merge_scoring_by_title(biz_score, scoring.get("price_scoring_items"), rule_result.get("price_scoring_items"))
    qualifications = _pick_list(qualification.get("qualification_requirements"), rule_result.get("parsed_biz_requirements"))
    commercial_items = _pick_list(commercial.get("commercial_requirements"))
    invalid_items = _pick_list(invalid.get("invalid_bid_clauses"), invalid.get("invalid_clauses"), rule_result.get("parsed_invalid_clauses"))

    project_info = _project_info_with_rule_fallback(project, rule_result)
    return {
        "project_basic_info": project_info,
        "tech_scoring_items": tech,
        "biz_scoring_items": biz_score,
        "qualification_requirements": qualifications + commercial_items,
        "invalid_bid_clauses": invalid_items,
        "bid_key_points": {
            "core_points": _top_titles(tech, 5),
            "high_score_priorities": _top_titles(_sort_scoring_items(tech), 5),
            "catalog_suggestions": _catalog_suggestions(tech),
            "invalid_risks": _top_titles(invalid_items, 5),
        },
    }


def _fallback_from_rules(rule_result: dict[str, Any], located_blocks: dict[str, Any]) -> dict[str, Any]:
    tech = _pick_list(rule_result.get("parsed_tech_scoring")) or _text_items(rule_result.get("score_requirements"))
    biz_scoring = _pick_list(rule_result.get("biz_scoring_items"))
    tech, biz_scoring = _reconcile_scoring(tech, biz_scoring, rule_result)
    biz = _pick_list(rule_result.get("parsed_biz_requirements"))
    invalid = _pick_list(rule_result.get("parsed_invalid_clauses"))
    project = {"overview": rule_result.get("project_overview") or "", "sources": []}
    return {
        "project_basic_info": project,
        "tech_scoring_items": tech,
        "biz_scoring_items": biz_scoring,
        "qualification_requirements": biz,
        "invalid_bid_clauses": invalid,
        "bid_key_points": {
            "core_points": _top_titles(tech, 5),
            "high_score_priorities": _top_titles(_sort_scoring_items(tech), 5),
            "catalog_suggestions": _catalog_suggestions(tech),
            "invalid_risks": _top_titles(invalid, 5),
            "candidate_block_summary": (located_blocks or {}).get("summary") or {},
        },
        "used_api": False,
        "degraded_categories": ["all"],
        "parsed_project_overview": rule_result.get("project_overview") or "",
        "parsed_score_requirements": rule_result.get("score_requirements") or _scoring_text(tech, []),
    }


def _fallback_category(category: str, rule_result: dict[str, Any], blocks: list[dict[str, Any]], error: str) -> dict[str, Any]:
    base = _empty_category_result(category, blocks)
    base["error"] = error
    if category == "project_overview":
        base["overview"] = rule_result.get("project_overview") or ""
    if category == "scoring":
        base["tech_scoring_items"] = _pick_list(rule_result.get("parsed_tech_scoring")) or _text_items(rule_result.get("score_requirements"))
        base["biz_scoring_items"] = _pick_list(rule_result.get("biz_scoring_items"))
    if category == "qualification":
        base["qualification_requirements"] = _pick_list(rule_result.get("parsed_biz_requirements"))
    if category == "invalid":
        base["invalid_bid_clauses"] = _pick_list(rule_result.get("parsed_invalid_clauses"))
    return base


def _project_info_with_rule_fallback(project: dict[str, Any], rule_result: dict[str, Any]) -> dict[str, Any]:
    nested = project.get("project_basic_info")
    if isinstance(nested, dict) and any(value not in (None, "", [], {}) for value in nested.values()):
        return nested

    keys = ("project_name", "buyer", "agency", "service_scope", "service_period", "budget", "core_goals", "overview")
    info = {key: project.get(key) for key in keys if project.get(key) not in (None, "", [], {})}
    if info:
        return info
    return {"overview": rule_result.get("project_overview") or ""}


def _empty_category_result(category: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(CATEGORY_CONFIG[category]["schema"])
    result["sources"] = [{"block_id": b.get("block_id"), "chapter_title": b.get("chapter_title"), "page_range": b.get("page_range")} for b in blocks[:8]]
    return result


def _pick_list(*values: object) -> list[dict[str, Any]]:
    for value in values:
        items = _to_items(value)
        if items:
            return items
    return []


def _pick_dict(*values: object) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and any(v not in (None, "", [], {}) for v in value.values()):
            return value
    return {}


def _to_items(value: object) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            return _to_items(json.loads(value))
        except Exception:
            return _text_items(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                result.append(item)
            elif str(item or "").strip():
                result.extend(_text_items(str(item)))
        return result
    return []


def _text_items(text: object) -> list[dict[str, Any]]:
    lines = [line.strip(" -\t") for line in str(text or "").splitlines() if line.strip(" -\t")]
    return [{"title": line[:100], "criteria": line[:1600], "text": line[:1600]} for line in lines[:80]]


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = str(item.get("title") or item.get("name") or item.get("item") or item.get("指标") or item.get("评分项目") or item.get("评分内容") or item.get("requirement_type") or item.get("category") or "").strip()
        text = str(item.get("criteria") or item.get("text") or item.get("requirement") or item.get("clause") or item.get("评审标准") or item.get("评分标准") or item.get("content") or "").strip()
        score = str(item.get("score") or item.get("points") or item.get("分值") or item.get("分值范围") or "").strip()
        key = re.sub(r"\s+", "", f"{title}|{score}|{text[:120]}")
        if not key or key in seen:
            continue
        seen.add(key)
        normalized = dict(item)
        normalized["title"] = title or text[:80] or "未命名条目"
        normalized["criteria"] = text or str(item)[:1600]
        if score:
            normalized["score"] = _score_value(score)
        result.append(normalized)
    return result


def _sort_scoring_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: _score_value(item.get("score") or item.get("points") or item.get("分值")), reverse=True)


def _score_value(value: object) -> float:
    values = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    return max((float(number) for number in values), default=0.0)


def _top_titles(items: list[dict[str, Any]], limit: int) -> list[str]:
    result = []
    for item in items[:limit]:
        title = str(item.get("title") or item.get("name") or item.get("criteria") or item.get("text") or "").strip()
        if title:
            result.append(title[:160])
    return result


def _catalog_suggestions(scoring_items: list[dict[str, Any]]) -> list[str]:
    suggestions = []
    for item in _sort_scoring_items(scoring_items)[:8]:
        title = str(item.get("title") or item.get("criteria") or "").strip()
        if title:
            suggestions.append(f"围绕“{title[:60]}”设置响应章节")
    return suggestions


def _project_overview_text(info: dict[str, Any]) -> str:
    if not info:
        return ""
    labels = [
        ("项目名称", info.get("project_name") or info.get("name")),
        ("采购单位", info.get("buyer") or info.get("purchaser")),
        ("代理机构", info.get("agency")),
        ("服务范围", info.get("service_scope") or info.get("scope")),
        ("服务周期", info.get("service_period") or info.get("duration")),
        ("预算金额", info.get("budget")),
    ]
    lines = [f"{label}：{value}" for label, value in labels if value]
    goals = info.get("core_goals") or info.get("goals") or []
    if isinstance(goals, list) and goals:
        lines.append("核心目标：" + "；".join(str(goal) for goal in goals[:6]))
    overview = info.get("overview")
    if overview:
        lines.append(str(overview))
    return "\n".join(lines)


def _scoring_text(tech_items: list[dict[str, Any]], biz_items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for label, items in (("技术评分", tech_items), ("商务评分", biz_items)):
        if not items:
            continue
        lines.append(f"【{label}】")
        for item in items[:80]:
            score = f"（{item.get('score')}分）" if item.get("score") not in (None, "") else ""
            title = item.get("title") or "评分项"
            criteria = item.get("criteria") or item.get("text") or ""
            lines.append(f"- {title}{score}：{criteria}" if criteria else f"- {title}{score}")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    value = text or ""
    if len(value) <= limit:
        return value
    return value[: int(limit * 0.7)] + "\n\n<!-- 内容过长，保留后段 -->\n" + value[-int(limit * 0.25) :]
