from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services import storage


MAX_CHUNK_CHARS = 1800
MAX_RETRIEVED_CHARS = 5200
COMPANY_KEYWORDS = (
    "公司", "集团", "成立", "注册资本", "企业简介", "公司简介", "资质", "认证",
    "业绩", "案例", "项目经验", "团队", "人员", "专利", "著作权", "荣誉",
)
COMPANY_SECTION_KEYWORDS = (
    "企业", "公司", "投标人", "资质", "认证", "业绩", "案例", "团队", "人员",
    "项目经验", "服务能力", "技术能力", "综合实力", "组织保障",
)


def build_reference_chunks(markdown: str, chapter_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    sources = chapter_blocks or [{"chapter_title": "全文", "content": markdown}]
    for block in sources:
        title = str(block.get("chapter_title") or "范本内容").strip()
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if buffer and len(candidate) > MAX_CHUNK_CHARS:
                chunks.append(_chunk_payload(len(chunks) + 1, title, buffer))
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            chunks.append(_chunk_payload(len(chunks) + 1, title, buffer))
    return chunks


def _chunk_payload(index: int, title: str, content: str) -> dict[str, Any]:
    return {
        "chunk_id": f"R{index:04d}",
        "title": title[:200],
        "content": content[:MAX_CHUNK_CHARS],
        "tokens": sorted(_search_tokens(f"{title} {content}"))[:1200],
    }


def _search_tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    latin = set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,30}", compact))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", compact)
    chinese: set[str] = set()
    for run in chinese_runs:
        chinese.update(run[index : index + 2] for index in range(len(run) - 1))
        chinese.update(run[index : index + 3] for index in range(len(run) - 2))
    return latin | chinese


def fallback_profiles(raw_text: str) -> tuple[str, str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (raw_text or "").splitlines()]
    company_lines = [line for line in lines if 12 <= len(line) <= 500 and any(key in line for key in COMPANY_KEYWORDS)]
    company_profile = "\n".join(dict.fromkeys(company_lines))[:5000]
    paragraph_lengths = [len(line) for line in lines if len(line) >= 30]
    average = round(sum(paragraph_lengths) / len(paragraph_lengths)) if paragraph_lengths else 0
    has_numbered = any(re.match(r"^(?:第[一二三四五六七八九十]+|\d+(?:\.\d+)*)", line) for line in lines)
    style_profile = (
        f"参考范本以正式、审慎、可执行的投标表述为主；平均段落约{average or 120}字；"
        f"{'经常使用分级编号组织论述' if has_numbered else '主要使用连续段落组织论述'}。"
        "生成时学习其段落节奏、论证顺序和措辞正式度，但不得复制原句或与当前项目无关的事实。"
    )
    return company_profile, style_profile


def parse_profile_json(content: str, fallback_company: str, fallback_style: str) -> tuple[str, str]:
    value = (content or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback_company, fallback_style
    company = payload.get("company_profile")
    if isinstance(company, list):
        company = "\n".join(str(item).strip() for item in company if str(item).strip())
    style = payload.get("style_profile")
    if isinstance(style, list):
        style = "；".join(str(item).strip() for item in style if str(item).strip())
    return str(company or fallback_company).strip()[:6000], str(style or fallback_style).strip()[:3000]


def retrieve_reference_context(bid_id: int, query: str, bid_type: str, limit: int = 4) -> dict[str, Any]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT ref_id, file_name, material_type, chunks_json, company_profile, style_profile
            FROM bid_reference_materials
            WHERE bid_id = ? AND parse_status = 'completed'
            ORDER BY ref_id DESC
            """,
            (bid_id,),
        ).fetchall()
    if not rows:
        return {"has_reference": False, "company_profile": "", "style_profile": "", "fragments": []}

    style_profiles = list(dict.fromkeys(str(row["style_profile"] or "").strip() for row in rows if str(row["style_profile"] or "").strip()))
    if bid_type == "blind":
        return {
            "has_reference": True,
            "company_profile": "",
            "style_profile": "\n".join(style_profiles)[:3000],
            "fragments": [],
        }

    query_tokens = _search_tokens(query)
    chapter_scope = query.split("\n", 1)[0]
    include_company_profile = any(keyword in chapter_scope for keyword in COMPANY_SECTION_KEYWORDS)
    company_profiles = list(dict.fromkeys(
        str(row["company_profile"] or "").strip()
        for row in rows
        if include_company_profile and str(row["company_profile"] or "").strip()
    ))
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for row in rows:
        try:
            chunks = json.loads(str(row["chunks_json"] or "[]"))
        except json.JSONDecodeError:
            chunks = []
        for chunk in chunks if isinstance(chunks, list) else []:
            chunk_tokens = set(chunk.get("tokens") or [])
            overlap = query_tokens & chunk_tokens
            title = str(chunk.get("title") or "")
            title_tokens = _search_tokens(title)
            score = len(overlap) + 2.5 * len(query_tokens & title_tokens)
            if any(keyword in query for keyword in COMPANY_KEYWORDS) and any(keyword in str(chunk.get("content") or "") for keyword in COMPANY_KEYWORDS):
                score += 4
            ranked.append((score, int(row["ref_id"]), str(row["file_name"]), chunk))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [item for item in ranked if item[0] > 0][:limit] or ranked[: min(2, limit)]
    fragments: list[dict[str, str]] = []
    used_chars = 0
    for _score, _ref_id, file_name, chunk in selected:
        content = str(chunk.get("content") or "").strip()
        if not content or used_chars >= MAX_RETRIEVED_CHARS:
            continue
        content = content[: MAX_RETRIEVED_CHARS - used_chars]
        used_chars += len(content)
        fragments.append({"file_name": file_name, "title": str(chunk.get("title") or "范本内容"), "content": content})
    return {
        "has_reference": True,
        "company_profile": "\n".join(company_profiles)[:6000],
        "style_profile": "\n".join(style_profiles)[:3000],
        "fragments": fragments,
    }


def format_reference_prompt(context: dict[str, Any], bid_type: str) -> str:
    if not context.get("has_reference"):
        return ""
    style = str(context.get("style_profile") or "").strip()
    if bid_type == "blind":
        return (
            "\n参考范本写作风格（仅学习表达，不得复用企业身份或案例事实）：\n"
            f"{style}\n暗标强约束：不得出现范本中的公司名称、品牌、人员姓名、业绩名称或其他可识别信息。"
        )
    return (
        "\n参考范本写作方式：正式、分层、逐项响应评分要求。"
        "范本中的企业事实和历史项目内容不能作为当前项目证明；缺少现行资料时标注待核实。"
    )
