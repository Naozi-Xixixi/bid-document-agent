from __future__ import annotations

from typing import Any

from .overview_extractor import extract_project_overview
from .requirement_extractor import extract_requirements, requirements_json
from .scoring_extractor import extract_scoring, scoring_json


CORE_BLOCK_KEYWORDS = {
    "project_overview": ["招标公告", "采购公告", "项目概况", "项目名称", "采购范围", "服务内容", "项目背景", "建设内容", "采购需求"],
    "scoring": ["评分办法", "评标办法", "评分标准", "评审标准", "详细评审", "评审因素", "评分因素", "分值", "得分", "技术评分", "商务评分"],
    "qualification": ["资格要求", "资质条件", "投标人资格", "合格条件", "资格审查", "资质", "证书", "业绩", "项目负责人"],
    "commercial": ["商务要求", "服务周期", "服务期限", "工期", "付款方式", "履约", "验收", "售后", "质保", "合同条款"],
    "invalid": ["废标", "无效投标", "否决投标", "不予通过", "不予受理", "实质性响应", "符合性审查", "资格性审查"],
}


def extract_bid_info(full_text: str, bid_type: str = "service") -> dict[str, Any]:
    """Extract structured bid information using BiaoShu-style rule fallback.

    The BiaoShu scripts are organized around keyword matching and regular
    expressions. This adapter keeps that no-token, deterministic approach while
    returning fields that the frontend and downstream catalog/content modules can
    consume directly.
    """

    text = (full_text or "").strip()
    if not text:
        return {
            "bid_type": bid_type,
            "project_overview": "",
            "score_requirements": "",
            "tech_scoring_text": "",
            "biz_requirements_text": "",
            "invalid_clauses_text": "",
            "parsed_tech_scoring": "[]",
            "parsed_biz_requirements": "[]",
            "parsed_invalid_clauses": "[]",
            "raw": {"scoring": {}, "requirements": {}},
        }

    try:
        scoring = extract_scoring(text) or {}
    except Exception:  # Rule misses and malformed source text degrade to empty output.
        scoring = {}
    try:
        requirements = extract_requirements(text) or {}
    except Exception:
        requirements = {}
    try:
        project_overview = extract_project_overview(text) or ""
    except Exception:
        project_overview = ""

    tech_scoring = scoring.get("tech_text") or scoring.get("summary_text") or ""
    score_requirements = scoring.get("summary_text") or tech_scoring or ""
    biz_requirements = requirements.get("biz_text") or scoring.get("biz_text") or ""
    invalid_clauses = requirements.get("invalid_text") or ""

    return {
        "bid_type": bid_type,
        "project_overview": project_overview,
        "score_requirements": score_requirements,
        "tech_scoring_text": tech_scoring,
        "biz_requirements_text": biz_requirements,
        "invalid_clauses_text": invalid_clauses,
        "parsed_tech_scoring": scoring_json(scoring, "tech_items"),
        "biz_scoring_items": scoring.get("biz_items") or [],
        "price_scoring_items": [item for item in (scoring.get("items") or []) if item.get("category") == "价格"],
        "parsed_biz_requirements": requirements_json(requirements.get("qualification_items", []) + requirements.get("commercial_items", [])),
        "parsed_invalid_clauses": requirements_json(requirements.get("invalid_items", [])),
        "raw": {
            "scoring": scoring,
            "requirements": requirements,
        },
    }


def locate_core_blocks(chapter_blocks: list[dict[str, Any]], full_markdown: str = "") -> dict[str, Any]:
    """Locate candidate blocks for AI extraction; rules only narrow the scope."""

    blocks = chapter_blocks or []
    if not blocks and full_markdown:
        blocks = [
            {
                "block_id": "B001",
                "chapter_title": "全文",
                "content": full_markdown[:18000],
                "block_type": "mixed" if "|" in full_markdown else "text",
                "page_range": [1, 1],
            }
        ]

    located: dict[str, list[dict[str, Any]]] = {}
    for category, keywords in CORE_BLOCK_KEYWORDS.items():
        matches: list[dict[str, Any]] = []
        for block in blocks:
            haystack = f"{block.get('chapter_title', '')}\n{block.get('content', '')}"
            hit_keywords = [keyword for keyword in keywords if keyword in haystack]
            if not hit_keywords:
                continue
            score = min(1.0, 0.35 + 0.12 * len(hit_keywords))
            if block.get("block_type") in {"table", "mixed"} and category == "scoring":
                score = min(1.0, score + 0.2)
                compact = "".join(haystack.split())
                if "评审要素" in compact and ("分值" in compact or "满分" in compact):
                    score = min(1.0, score + 0.25)
                if any(marker in compact for marker in ("|1-1|", "|2-1|", "|3-1|")):
                    score = min(1.0, score + 0.25)
            matches.append(
                {
                    "block_id": block.get("block_id"),
                    "chapter_title": block.get("chapter_title"),
                    "page_range": block.get("page_range") or [1, 1],
                    "block_type": block.get("block_type") or "text",
                    "matched_keywords": hit_keywords[:10],
                    "confidence": round(score, 2),
                    "content": str(block.get("content") or "")[:18000],
                }
            )
        matches.sort(key=lambda item: (float(item.get("confidence") or 0), len(str(item.get("content") or ""))), reverse=True)
        limit = 12 if category == "scoring" else 8
        located[category] = matches[:limit] if matches else _fallback_blocks(blocks, category)

    return {
        "categories": located,
        "summary": {category: [item.get("block_id") for item in items] for category, items in located.items()},
    }


def _fallback_blocks(blocks: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    if not blocks:
        return []
    if category == "project_overview":
        selected = blocks[:4]
    elif category == "scoring":
        table_blocks = [block for block in blocks if block.get("block_type") in {"table", "mixed"}]
        selected = (table_blocks or blocks)[0:8]
    elif category == "invalid":
        selected = blocks[-6:]
    else:
        span = max(1, len(blocks) // 3)
        selected = blocks[:span] + blocks[span : span + 4]

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in selected:
        block_id = str(block.get("block_id") or "")
        if block_id in seen:
            continue
        seen.add(block_id)
        result.append(
            {
                "block_id": block.get("block_id"),
                "chapter_title": block.get("chapter_title"),
                "page_range": block.get("page_range") or [1, 1],
                "block_type": block.get("block_type") or "text",
                "matched_keywords": [],
                "confidence": 0.2,
                "content": str(block.get("content") or "")[:18000],
            }
        )
    return result[:8]
