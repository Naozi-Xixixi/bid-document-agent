from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services.qwen_client import chat_completion


logger = logging.getLogger(__name__)

SKILL_PATH = Path(__file__).resolve().parents[4] / "Bid-Analysis-Skill-main" / "SKILL.md"

CHECK_LABELS = {
    "invalid_bid": "废标风险条款核查",
    "scoring_response": "评分项完整响应校验",
    "qualification": "投标人资格要求匹配检查",
    "business_conflict": "商务条款、工期、保证金冲突校验",
    "format_signature": "投标文件格式、签署要求核查",
    "key_requirement": "关键需求点漏响应筛查",
}

CHECK_CONTEXT_KEYWORDS = {
    "invalid_bid": ("废标", "否决", "无效投标", "实质性响应", "签字", "盖章"),
    "scoring_response": ("评分", "评审", "分值", "得分", "技术方案", "响应"),
    "qualification": ("资格", "资质", "业绩", "人员", "证书", "财务"),
    "business_conflict": ("商务", "工期", "服务期", "付款", "保证金", "履约", "验收"),
    "format_signature": ("格式", "签署", "签字", "盖章", "装订", "密封", "页码"),
    "key_requirement": ("需求", "要求", "必须", "应当", "响应", "技术参数", "服务内容"),
}


def load_skill_instructions() -> str:
    if not SKILL_PATH.exists():
        logger.warning("Optional bid-analysis guidance not found at %s; using built-in safe rules", SKILL_PATH)
        return (
            "逐条对照招标文件和投标文件中的可核实内容。仅引用输入材料明确提供的依据；"
            "不推测、不补造事实。无法确认的事项标记为需要人工复核。重点检查废标条款、评分点响应、"
            "资格证明、商务约定和签章格式。输出问题时附招标原文、投标响应原文和可执行的修正建议。"
        )
    return SKILL_PATH.read_text(encoding="utf-8", errors="replace")


def _extract_json(text: str) -> dict[str, Any]:
    value = (text or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if not match:
            raise ValueError("Qwen did not return a JSON object")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Qwen result must be a JSON object")
    return parsed


def _normalize_result(item: Any, check_type: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    level = str(item.get("risk_level") or item.get("level") or "medium").lower()
    level_map = {
        "高": "high", "高风险": "high", "red": "high", "critical": "high",
        "中": "medium", "中风险": "medium", "yellow": "medium", "warning": "medium",
        "低": "suggestion", "低风险": "suggestion", "green": "suggestion", "advice": "suggestion",
        "优化建议": "suggestion", "建议": "suggestion",
    }
    level = level_map.get(level, level)
    if level not in {"high", "medium", "suggestion"}:
        level = "medium"
    title = str(item.get("risk_title") or item.get("title") or "检查发现").strip()
    problem = str(item.get("problem_text") or item.get("problem") or item.get("issue") or "").strip()
    suggestion = str(item.get("suggestion_text") or item.get("suggestion") or "请结合招标原文人工复核并补充响应。").strip()
    source = str(item.get("source_text") or item.get("source") or "").strip()
    if not problem:
        return None
    chapter_id = item.get("chapter_id")
    try:
        chapter_id = int(chapter_id) if chapter_id not in (None, "") else None
    except (TypeError, ValueError):
        chapter_id = None
    return {
        "check_type": check_type,
        "risk_level": level,
        "risk_title": title[:300],
        "source_text": source[:4000],
        "problem_text": problem[:4000],
        "suggestion_text": suggestion[:4000],
        "chapter_id": chapter_id,
        "chapter_title": str(item.get("chapter_title") or "").strip()[:300] or None,
    }


def _compact_markdown(text: str, keywords: tuple[str, ...], limit: int = 18000) -> str:
    source = str(text or "")
    if len(source) <= limit:
        return source
    lines = source.splitlines()
    selected: set[int] = set(range(min(len(lines), 80)))
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") or any(keyword in line for keyword in keywords):
            selected.update(range(max(0, index - 3), min(len(lines), index + 7)))
    output: list[str] = []
    total = 0
    for index in sorted(selected):
        line = lines[index]
        if total + len(line) + 1 > limit:
            break
        output.append(line)
        total += len(line) + 1
    return "\n".join(output)[:limit]


def _scope_standalone_payload(payload: dict[str, Any], check_type: str) -> dict[str, Any]:
    if str((payload.get("bid") or {}).get("type")) != "standalone_file_check":
        return payload
    scoped = json.loads(json.dumps(payload, ensure_ascii=False))
    keywords = CHECK_CONTEXT_KEYWORDS.get(check_type, ())
    tender = scoped.get("tender_rules") or {}
    current = scoped.get("current_bid_document") or {}
    tender["full_markdown"] = _compact_markdown(tender.get("full_markdown", ""), keywords)
    current["full_markdown"] = _compact_markdown(current.get("full_markdown", ""), keywords)
    tender["context_selected_for"] = check_type
    current["context_selected_for"] = check_type
    return scoped


async def analyze_check_item(check_type: str, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    label = CHECK_LABELS[check_type]
    skill_text = load_skill_instructions()
    payload = _scope_standalone_payload(payload, check_type)
    messages = [
        {
            "role": "system",
            "content": (
                "你是 bid-analysis skill 的执行器。下面是唯一允许使用的标书校验规范，必须严格按其规则判断，"
                "不得另创校验标准。无法从提供的数据确认的事项必须明确标为需人工复核，禁止臆测。\n\n"
                f"<bid-analysis-skill>\n{skill_text}\n</bid-analysis-skill>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"本次仅执行检查类别：{label}（代码：{check_type}）。\n"
                "请逐项对照招标文件结构化规则与当前投标文件目录/正文。响应完整度以 skill 规定的 95% 为标准；"
                "冲突项必须给出招标原文和当前标书问题。只输出 JSON，不要输出 Markdown 或解释。\n"
                "results 最多返回 12 条，优先保留高风险和中风险；source_text、problem_text、suggestion_text "
                "每个字段不超过 280 字，summary 不超过 300 字，确保 JSON 完整闭合。\n"
                "输出格式：{\"results\":[{\"risk_level\":\"high|medium|suggestion\"," 
                "\"risk_title\":\"标题\",\"source_text\":\"招标文件原文或结构化条款\","
                "\"problem_text\":\"当前标书存在的问题\",\"suggestion_text\":\"可执行整改建议\","
                "\"chapter_id\":数字或null,\"chapter_title\":\"最相关目录章节或空字符串\"}],"
                "\"summary\":\"本类检查结论\"}。没有风险时 results 返回空数组，禁止虚构风险。\n\n"
                f"校验输入：\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    result = await chat_completion(messages, temperature=0.1, max_tokens=4500, json_mode=True, scene="tender_parse")
    if not result.usedApi:
        raise RuntimeError(result.error or "Qwen API was not used")
    try:
        parsed = _extract_json(result.content)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("bid-analysis JSON incomplete; retrying compact output check_type=%s error=%s", check_type, exc)
        retry_messages = [
            messages[0],
            {
                "role": "user",
                "content": (
                    messages[1]["content"]
                    + "\n\n上一次输出因过长未形成完整 JSON。请重新检查并输出更精简的完整 JSON："
                    "最多 8 条 results，每个文本字段不超过 180 字，必须闭合所有引号、数组和对象。"
                ),
            },
        ]
        result = await chat_completion(retry_messages, temperature=0.0, max_tokens=3600, json_mode=True, scene="tender_parse")
        if not result.usedApi:
            raise RuntimeError(result.error or "Qwen API retry was not used")
        parsed = _extract_json(result.content)
    normalized = [value for item in parsed.get("results", []) if (value := _normalize_result(item, check_type))]
    meta = {
        "provider": result.provider,
        "model": result.model,
        "used_api": result.usedApi,
        "summary": str(parsed.get("summary") or "").strip(),
    }
    logger.info("bid-analysis skill completed check_type=%s results=%s model=%s", check_type, len(normalized), result.model)
    return normalized, meta
