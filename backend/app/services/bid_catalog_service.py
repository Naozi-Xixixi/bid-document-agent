from __future__ import annotations

from app.services import tender_form_service

from app.services import chapter_collaboration

import asyncio
import json
import re
import threading
import uuid
from io import BytesIO
from typing import Any, AsyncIterator

from anyio import CancelScope
from docx import Document

from app.core.tenant_context import get_current_tenant
from app.schemas.bid_catalog import CatalogChapterActionRequest, CatalogChapterCreateRequest, CatalogChapterMoveRequest, CatalogChapterUpdateRequest
from app.services import bid_case_reference_service, bid_reference_service, bid_word_count_service, storage
from app.services.qwen_client import chat_completion, stream_chat_completion


CATALOG_TASKS: dict[tuple[str, int], dict[str, Any]] = {}
_CATALOG_TASK_LOCK = threading.Lock()


def _catalog_task_key(bid_id: int) -> tuple[str, int]:
    tenant = get_current_tenant()
    return (tenant.company_id if tenant else "company_default", int(bid_id))


def _update_catalog_task(key: tuple[str, int], task_id: str, **changes: Any) -> None:
    with _CATALOG_TASK_LOCK:
        task = CATALOG_TASKS.get(key)
        if task and task["task_id"] == task_id:
            task.update(changes)


def _publish_catalog_event(key: tuple[str, int], task_id: str, event: dict[str, Any]) -> None:
    with _CATALOG_TASK_LOCK:
        task = CATALOG_TASKS.get(key)
        if not task or task["task_id"] != task_id:
            return
        events = task["events"]
        # Each delta carries the full readable preview, so reconnects only need
        # the latest delta in each uninterrupted run of model output.
        if events and events[-1]["type"] == event["type"] == "delta":
            events[-1] = event
        else:
            events.append(event)
        for queue in task["subscribers"]:
            queue.put_nowait(event)


async def _subscribe_catalog_stream(key: tuple[str, int], task_id: str) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    with _CATALOG_TASK_LOCK:
        task = CATALOG_TASKS.get(key)
        if task and task["task_id"] == task_id:
            # Register and snapshot together so events cannot be lost in between.
            replay = list(task["events"])
            task["subscribers"].add(queue)
        else:
            task = None
            replay = []
    if task is None:
        yield {"type": "error", "task_id": task_id, "code": 40901, "message": "目录生成任务已结束，请刷新目录"}
        return
    try:
        for event in replay:
            yield event
            if event["type"] in {"done", "error"}:
                return
        while True:
            event = await queue.get()
            yield event
            if event["type"] in {"done", "error"}:
                return
    finally:
        with _CATALOG_TASK_LOCK:
            task["subscribers"].discard(queue)


VERSION_STYLES = [
    ("目录一【标准均衡型】", "标准均衡型", "standard"),
    ("目录二【技术深度型】", "技术深度型", "tech"),
    ("目录三【服务落地型】", "服务落地型", "service"),
]

VERSION_PROMPTS = {
    "standard": "结构全面均衡，完整覆盖所有评分点，章节数量适中，适合常规投标项目；技术、商务、服务、风险章节均衡配置。",
    "tech": "侧重技术方案、实施路径、核心算法/架构和质量控制；技术类章节拆分更细、层级更多，商务与服务章节适当精简。",
    "service": "侧重服务体系、项目管理、运维保障、人员配置和交付落地；服务与管理类章节更丰富，技术章节保持框架性。",
}

CHAPTER_NUMBERS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
SECTION_NUMBERS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


class BidCatalogServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _ensure_bid(bid_id: int):
    try:
        return storage.ensure_bid_exists(bid_id)
    except FileNotFoundError as exc:
        raise BidCatalogServiceError(40401, f"标书不存在：{bid_id}", 404) from exc


def _now_text() -> str:
    return storage.now_utc().isoformat()


def _json_from_text(raw: str) -> Any | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    candidates = [cleaned]
    for left, right in [("[", "]"), ("{", "}")]:
        start = cleaned.find(left)
        end = cleaned.rfind(right)
        if start >= 0 and end > start:
            candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _split_lines(text: str, limit: int = 16) -> list[str]:
    values = [item.strip(" -•\t") for item in re.split(r"[\r\n;；]+", text or "")]
    return [item for item in values if item][:limit]


def _structured_text(value: str, limit: int = 24) -> str:
    value = (value or "").strip()
    if not value or value == "[]":
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(parsed, list):
        return value
    lines: list[str] = []
    for item in parsed[:limit]:
        if isinstance(item, dict):
            category = item.get("category") or item.get("type") or "要求"
            title = item.get("title") or item.get("name") or ""
            text = item.get("text") or item.get("criteria") or item.get("requirement") or item.get("clause") or ""
            score = f"（{item.get('score')}分）" if item.get("score") is not None else ""
            line = f"- [{category}] {title}{score}：{text}" if title else f"- [{category}] {text}"
        else:
            line = f"- {item}"
        line = re.sub(r"\s+", " ", str(line)).strip()
        if line:
            lines.append(line[:500])
    return "\n".join(lines)


def _scoring_points_from_text(text: str) -> list[str]:
    lines = _split_lines(text, 20)
    scored = [line for line in lines if any(key in line for key in ("评分", "分值", "技术", "服务", "实施", "质量", "团队", "保障"))]
    return scored[:10] or lines[:8]


def _required_format_sections(raw_text: str) -> list[str]:
    return tender_form_service.required_format_sections(raw_text)


def _align_catalog_to_tender_format(
    catalog: list[dict[str, Any]], required: list[str], context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Keep the mandatory order and the useful subdivisions of each section."""
    if not required:
        return catalog

    def key(title: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]", "", _strip_catalog_number(title)).casefold()

    matched: set[int] = set()
    aligned: list[dict[str, Any]] = []
    for title in required:
        section_key = key(title)
        match_index = next(
            (index for index, item in enumerate(catalog) if index not in matched and key(item["title"]) == section_key),
            None,
        )
        if match_index is None and len(section_key) >= 5:
            match_index = next(
                (
                    index for index, item in enumerate(catalog)
                    if index not in matched and section_key in key(item["title"])
                ),
                None,
            )
        if match_index is not None:
            matched.add(match_index)
        children = catalog[match_index].get("children") if match_index is not None else None
        fixed_form = tender_form_service.is_fixed_form(title)
        if fixed_form:
            children = []
        # Preserve subdivisions supplied for this exact required heading. When
        # the model uses different headings or returns a flat list, keep the
        # prescribed form titles and add only known narrative subdivisions.
        defaults = [] if fixed_form else _required_section_children(title, context)
        aligned.append({"title": title, "children": children or defaults})
    return aligned


def _required_section_children(title: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Safe subdivisions for narrative sections in a prescribed file list."""
    section = re.sub(r"\s+", "", title)
    project_text = " ".join(str((context or {}).get(key) or "") for key in ("bid_title", "project_overview", "score_requirements"))
    food_project = any(keyword in project_text for keyword in ("食材", "食品原材料", "冷链"))
    sections: list[tuple[str, list[str]]] = []
    if "情况综合简介" in section:
        sections = [("企业基本情况", []), ("经营场地与设施", []), (("物流配送与服务能力" if food_project else "项目履约与服务能力"), [])]
    elif "投标业绩" in section:
        sections = [("类似项目业绩", []), ("履约反馈与证明材料", [])]
    elif section == "有关证明文件":
        sections = [("企业资质与荣誉证明", []), ("场地及设备证明", []), (("运输车辆证明" if food_project else "其他履约能力证明"), [])]
    elif "人员配备" in section or "人员情况" in section:
        sections = [("项目组织与岗位职责", []), (("配送人员配置" if food_project else "项目人员配置"), []), ("人员名单与证明材料", [])]
    elif "供货安装" in section or "供货方案" in section or section == "服务方案":
        if food_project:
            sections = [
                ("订单响应与供货计划", ["需求确认与采购安排", "货源保障"]),
                ("采购验收与质量控制", ["进货查验", "质量追溯"]),
                ("运输配送与交付验收", ["车辆与人员调度", "到货验收"]),
                ("应急供应与食品安全处置", ["临时及紧急采购", "食品安全事件处置"]),
            ]
        else:
            sections = [("需求确认与供货计划", []), ("交付与安装调试", []), ("质量验收与应急保障", [])]
    elif "售后服务体系" in section:
        sections = [("服务响应与沟通机制", []), ("质量问题处理", []), ("退换货及持续改进", [])]
    elif "货物的技术" in section:
        sections = [("产品技术指标与响应", []), ("质量检测与追溯", []), ("储运与维护保障", [])]
    return [{"title": heading, "children": items} for heading, items in sections]


def _catalog_context(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError as exc:
        raise BidCatalogServiceError(40401, f"标书不存在：{bid_id}", 404) from exc

    with storage.connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
    if bid is None:
        raise BidCatalogServiceError(40401, f"标书不存在：{bid_id}", 404)
    if parse is None or parse["parse_mode"] not in {"auto_upload", "paste_text"}:
        raise BidCatalogServiceError(40001, "请先完成招标文件解读")
    if int(bid["current_step"]) < 3:
        raise BidCatalogServiceError(40001, "请先完成第二步招标文件解读")

    raw_full_text = (parse["raw_full_text"] or "").strip()
    required_format_sections = _required_format_sections(raw_full_text)
    project_overview = (parse["parsed_project_overview"] or "").strip()
    score_requirements = (parse["parsed_score_requirements"] or "").strip()
    tech_scoring = _structured_text(parse["parsed_tech_scoring"] or "")
    biz_requirements = _structured_text(parse["parsed_biz_requirements"] or "")
    invalid_clauses = _structured_text(parse["parsed_invalid_clauses"] or "")
    ai_tech_scoring = _structured_text(parse["tech_scoring_items"] or "")
    ai_biz_scoring = _structured_text(parse["biz_scoring_items"] or "")
    qualification_requirements = _structured_text(parse["qualification_requirements"] or "")
    bid_key_points = _structured_text(parse["bid_key_points"] or "")
    if not project_overview and raw_full_text:
        project_overview = raw_full_text[:1600]
    if not score_requirements and raw_full_text:
        score_requirements = "\n".join(_scoring_points_from_text(raw_full_text))
    if tech_scoring and tech_scoring not in score_requirements:
        score_requirements = "\n".join(item for item in [score_requirements, tech_scoring] if item).strip()
    if ai_tech_scoring and ai_tech_scoring not in score_requirements:
        score_requirements = "\n".join(item for item in [score_requirements, ai_tech_scoring] if item).strip()
    biz_requirements = "\n".join(
        item for item in [biz_requirements, ai_biz_scoring, qualification_requirements, bid_key_points] if item
    ).strip()

    return {
        "bid_id": bid_id,
        "project_id": project.id,
        "bid_title": project.settings.bidTitle or bid["bid_title"] or "投标文件",
        "page_count": int(project.settings.pageCount or bid["page_count"] or 200),
        "words_per_page": int(project.settings.wordsPerPage or bid["words_per_page"] or 800),
        "estimated_word_count": int(project.settings.estimatedWordCount or bid["estimated_word_count"] or 160000),
        "bid_type": project.settings.bidType,
        "selected_model": project.settings.selectedModel,
        "parse_mode": parse["parse_mode"],
        "project_overview": project_overview,
        "score_requirements": score_requirements,
        "biz_requirements": biz_requirements,
        "invalid_clauses": invalid_clauses,
        "raw_full_text": raw_full_text,
        "required_format_sections": required_format_sections,
        "procurement_method": tender_form_service.procurement_method(raw_full_text),
        "price_score_conflict": tender_form_service.price_score_conflict(raw_full_text),
        "bid_category": parse["bid_category"],
    }


def _chapter_count(page_count: int) -> int:
    return max(4, min(8, round(max(page_count, 60) / 18)))


def _style_topics(style: str, bid_category: str) -> list[str]:
    common = [
        "项目理解与需求分析",
        "总体响应方案",
        "项目实施组织与进度计划",
        "质量保证与风险控制",
        "服务保障与售后承诺",
        "团队配置与类似业绩",
        "暗标格式响应与投标文件规范",
        "附件与证明材料",
    ]
    technical = [
        "项目理解与需求分析",
        "技术路线与总体架构",
        "关键技术方案与实施细节",
        "系统安全、质量与性能保障",
        "项目实施计划与交付管理",
        "测试验收与运维移交",
        "技术团队与类似案例",
        "附件与技术证明材料",
    ]
    service = [
        "项目理解与服务目标",
        "服务体系与组织保障",
        "服务流程与响应机制",
        "人员配置与现场管理",
        "质量考核与持续改进",
        "风险应急与安全保障",
        "培训计划与售后服务",
        "企业资质与业绩证明",
    ]
    if "技术" in style:
        return technical
    if "服务" in style:
        return service
    if bid_category == "goods":
        return ["项目理解与供货响应", "产品技术方案", "供货组织与进度计划", "质量检验与验收", "安装调试与培训", "售后服务与备品备件", "企业资质与业绩证明"]
    if bid_category == "engineering":
        return ["工程概况与施工理解", "施工组织设计", "关键施工技术方案", "质量安全文明施工", "工期保证与资源配置", "项目管理机构", "类似工程业绩与附件"]
    return common


def _build_rule_catalog(context: dict[str, Any], style: str) -> list[dict[str, Any]]:
    required = context.get("required_format_sections") or []
    if required:
        return [{"title": title, "children": _required_section_children(title, context)} for title in required]
    score_points = _scoring_points_from_text(context.get("score_requirements", ""))
    topics = _style_topics(style, str(context.get("bid_category") or "service"))
    count = min(len(topics), _chapter_count(int(context.get("page_count") or 200)))
    chapters: list[dict[str, Any]] = []
    for idx, topic in enumerate(topics[:count], 1):
        cn = CHAPTER_NUMBERS[idx - 1] if idx - 1 < len(CHAPTER_NUMBERS) else str(idx)
        chapter_title = f"第{cn}章 {topic}"
        focus = score_points[(idx - 1) % len(score_points)] if score_points else topic
        sections = [
            {"title": f"{idx}.1 {topic}总体说明", "children": [f"{idx}.1.1 编写目标", f"{idx}.1.2 响应重点"]},
            {"title": f"{idx}.2 {focus[:24]}响应方案", "children": [f"{idx}.2.1 关键措施", f"{idx}.2.2 落地路径"]},
            {"title": f"{idx}.3 保障措施与成果交付", "children": [f"{idx}.3.1 质量控制", f"{idx}.3.2 风险预案"]},
        ]
        chapters.append({"title": chapter_title, "children": sections})
    return chapters


def _normalize_catalog(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("catalog") or raw.get("chapters") or raw.get("outline")
    if not isinstance(raw, list):
        return fallback

    normalized: list[dict[str, Any]] = []
    for c_idx, chapter in enumerate(raw, 1):
        if isinstance(chapter, str):
            chapter = {"title": chapter, "children": []}
        if not isinstance(chapter, dict):
            continue
        title = _strip_catalog_number(str(chapter.get("title") or chapter.get("chapter_title") or ""))
        if not title:
            continue
        children: list[dict[str, Any]] = []
        raw_children = chapter.get("children") or chapter.get("sections") or []
        if isinstance(raw_children, list):
            for s_idx, section in enumerate(raw_children, 1):
                if isinstance(section, str):
                    section = {"title": section, "children": []}
                if not isinstance(section, dict):
                    continue
                section_title = _strip_catalog_number(str(section.get("title") or section.get("chapter_title") or ""))
                if not section_title:
                    continue
                sub_children = section.get("children") or section.get("subsections") or section.get("items") or []
                sub_titles = []
                if isinstance(sub_children, list):
                    for sub in sub_children[:8]:
                        if isinstance(sub, dict):
                            sub = sub.get("title") or sub.get("chapter_title") or ""
                        sub = _strip_catalog_number(str(sub))
                        if sub:
                            sub_titles.append(sub)
                children.append({"title": section_title, "children": sub_titles})
        if not children:
            children = fallback[min(c_idx - 1, len(fallback) - 1)].get("children", []) if fallback else []
        normalized.append({"title": title, "children": children[:8]})
    return normalized or fallback


def _catalog_messages(context: dict[str, Any], version_name: str, style: str, version_type: str, fallback: list[dict[str, Any]]) -> list[dict[str, str]]:
    style_instruction = VERSION_PROMPTS.get(version_type, VERSION_PROMPTS["standard"])
    reference_context = bid_reference_service.retrieve_reference_context(
        int(context["bid_id"]),
        f"目录章节：企业介绍 企业能力 资质 业绩\n评分要求：{context.get('score_requirements', '')[:1000]}",
        str(context["bid_type"]),
        limit=3,
    )
    reference_prompt = bid_reference_service.format_reference_prompt(reference_context, str(context["bid_type"]))
    case_context = bid_case_reference_service.retrieve_case_context(
        int(context["bid_id"]),
        f"投标文件目录章节结构；评分要求：{context.get('score_requirements', '')[:1000]}",
        limit=5,
    )
    case_prompt = bid_case_reference_service.format_case_prompt(case_context)
    case_outline = bid_case_reference_service.retrieve_case_outline(int(context["bid_id"]))
    return [
        {
            "role": "system",
            "content": (
                "你是资深投标文件目录专家。只输出包含 catalog 数组的 JSON 对象，不要解释。"
                "目录层级最多三级：章、节、小节。catalog 数组字段格式："
                "[{\"title\":\"第一章 ...\",\"children\":[{\"title\":\"1.1 ...\",\"children\":[\"1.1.1 ...\"]}]}]。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"项目名称：{context['bid_title']}\n"
                f"目录版本：{version_name}\n生成风格：{style}\n"
                f"版本定位：{style_instruction}\n"
                f"标书类型：{context['bid_type']}\n预设页数：{context['page_count']}\n"
                f"项目概述：{context.get('project_overview', '')[:2400]}\n"
                f"评分要求：{context.get('score_requirements', '')[:2600]}\n"
                f"商务/资质要求：{context.get('biz_requirements', '')[:1600]}\n"
                f"废标/否决条款：{context.get('invalid_clauses', '')[:1200]}\n"
                f"本次招标文件明确规定的投标文件资料清单（必须保持原顺序和标题，不可用案例目录替代）：{json.dumps(context.get('required_format_sections', []), ensure_ascii=False)}\n"
                f"历史标书范本参考：{reference_prompt[:5000]}\n"
                f"甲方认可案例参考：{case_prompt[:6000]}\n"
                f"甲方认可案例目录骨架（只参考层级和覆盖面，旧项目专属章节要按本次招标调整）：\n{case_outline[:5000]}\n"
                f"规则兜底目录：{json.dumps(fallback, ensure_ascii=False)}\n"
                "请在不超过三级层级的前提下，生成覆盖评分点、符合招投标规范且与版本定位明显一致的目录。"
                "招标文件列有投标文件资料清单时，以该清单作为顶层目录；历史案例只能辅助相关章节的细分，不得改变必备表单和顺序。"
                "有资料清单时，每个顶层标题必须与清单逐字一致；综合简介、业绩、证明文件、人员配备和各类方案应在原章节下细化二级标题，重点方案可细化到三级。"
                "固定表格、授权书、承诺书和声明通常保持单章，不能把评分点或方案小节提升为与清单并列的顶层章节。"
                "没有资料清单时，才参考案例目录骨架，并依本次评分点增删；不得照搬旧项目名称、金额和人员。"
                "明标且范本存在企业事实时，可在评分要求适合的位置安排企业概况、能力、资质或业绩章节；"
                "暗标不得出现企业名称、人员姓名、品牌或可识别案例。"
            ),
        },
    ]


def _catalog_preview(raw: str) -> str:
    """Display partial JSON titles without treating a partial tree as saved data."""
    stack: list[tuple[str, str | None]] = []
    key: str | None = None
    lines: list[str] = []
    arrays = {"catalog", "chapters", "outline", "children", "sections", "subsections", "items"}
    for match in re.finditer(r'"(?:\\.|[^"\\])*"?|[{}\[\],:]', raw):
        token = match.group()
        if token.startswith('"'):
            complete = len(token) > 1 and token.endswith('"') and len(re.search(r'\\*$', token[:-1]).group()) % 2 == 0
            value = token[1:-1] if complete else token[1:]
            if not complete:
                value = re.sub(r'\\u[0-9a-fA-F]{0,3}$|\\$', '', value)
            try:
                text = json.loads('"' + value + '"')
            except json.JSONDecodeError:
                continue
            if complete and raw[match.end():].lstrip().startswith(':'):
                key = text
                continue
            in_title = key in {"title", "chapter_title"}
            in_list = bool(stack and stack[-1][0] == '[' and stack[-1][1] in arrays)
            if text and (in_title or in_list):
                depth = max(1, sum(kind == '[' and role in arrays for kind, role in stack))
                lines.append('  ' * (depth - 1) + text)
            key = None
        elif token in ('[', '{'):
            role = key or ('catalog' if token == '[' and not stack else None)
            stack.append((token, role))
            key = None
        elif token in (']', '}'):
            if stack:
                stack.pop()
            key = None
        elif token == ',':
            key = None
    return '\n'.join(lines)


def _validate_streamed_catalog(raw: str) -> list[dict[str, Any]]:
    """Only a complete, valid JSON document may replace a persisted version."""
    cleaned = raw.strip()
    fence = re.fullmatch(r'\x60{3}(?:json)?\s*(.*?)\x60{3}', cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise BidCatalogServiceError(50201, "目录返回的 JSON 不完整或格式不正确，请重新生成；本次未保存该方案。", 502) from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("catalog") or parsed.get("chapters") or parsed.get("outline")

    def validate(nodes: Any, depth: int = 1) -> None:
        if not isinstance(nodes, list) or (depth == 1 and not nodes):
            raise BidCatalogServiceError(50202, "目录结构无效，未保存该方案。", 502)
        for node in nodes:
            if isinstance(node, str):
                title, children = node, []
            elif isinstance(node, dict):
                title = node.get("title") or node.get("chapter_title")
                children = node.get("children", node.get("sections", node.get("subsections", node.get("items", []))))
            else:
                raise BidCatalogServiceError(50202, "目录章节结构无效，未保存该方案。", 502)
            if not isinstance(title, str) or not _strip_catalog_number(title):
                raise BidCatalogServiceError(50202, "目录存在空标题，未保存该方案。", 502)
            if depth >= 3 and children:
                raise BidCatalogServiceError(50202, "目录层级超过三级，未保存该方案。", 502)
            if children is not None:
                validate(children, depth + 1)

    validate(parsed)
    return _normalize_catalog(parsed, [])


def _insert_catalog_tree(conn, bid_id: int, version_id: int, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now_text()

    def insert_node(title: str, level: int, parent_id: int, sort_order: int, children: list[Any]) -> dict[str, Any]:
        title = _strip_catalog_number(title)
        cursor = conn.execute(
            """
            INSERT INTO bid_catalog_chapters (bid_id, version_id, parent_id, chapter_title, level, sort_order, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (bid_id, version_id, parent_id, title, level, sort_order, now),
        )
        chapter_id = int(cursor.lastrowid)
        node = {"chapter_id": chapter_id, "title": title, "level": level, "parent_id": parent_id, "sort_order": sort_order, "children": []}
        if level < 3:
            for idx, child in enumerate(children or [], 1):
                if isinstance(child, str):
                    child_title = child
                    grand_children: list[Any] = []
                elif isinstance(child, dict):
                    child_title = str(child.get("title") or "").strip()
                    grand_children = list(child.get("children") or [])
                else:
                    continue
                if child_title:
                    node["children"].append(insert_node(child_title, level + 1, chapter_id, idx, grand_children))
        return node

    tree: list[dict[str, Any]] = []
    for idx, chapter in enumerate(catalog, 1):
        title = str(chapter.get("title") or "").strip()
        if title:
            tree.append(insert_node(title, 1, 0, idx, list(chapter.get("children") or [])))
    return tree


def _replace_catalog_versions(bid_id: int, versions: list[tuple[str, str, str, list[dict[str, Any]]]]) -> None:
    now = _now_text()
    version_ids: list[int] = []
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        conn.execute("DELETE FROM bid_catalog_chapters WHERE bid_id = ?", (bid_id,))
        conn.execute("DELETE FROM bid_catalog_versions WHERE bid_id = ?", (bid_id,))
        first_tree: list[dict[str, Any]] | None = None
        for index, (version_name, version_style, version_type, catalog) in enumerate(versions):
            cursor = conn.execute(
                """
                INSERT INTO bid_catalog_versions (bid_id, version_name, version_style, version_type, is_final, catalog_content, create_time, update_time)
                VALUES (?, ?, ?, ?, ?, '[]', ?, ?)
                """,
                (bid_id, version_name, version_style, version_type, 1 if index == 0 else 0, now, now),
            )
            version_id = int(cursor.lastrowid)
            version_ids.append(version_id)
            tree = _insert_catalog_tree(conn, bid_id, version_id, catalog)
            if first_tree is None:
                first_tree = tree
            conn.execute(
                "UPDATE bid_catalog_versions SET catalog_content = ?, update_time = ? WHERE version_id = ?",
                (json.dumps(tree, ensure_ascii=False), now, version_id),
            )
        conn.commit()
    for version_id in version_ids:
        bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=False)
    if version_ids:
        with storage.connect() as conn:
            row = conn.execute("SELECT catalog_content FROM bid_catalog_versions WHERE version_id = ?", (version_ids[0],)).fetchone()
        try:
            first_tree = json.loads(row["catalog_content"] or "[]") if row else first_tree
        except json.JSONDecodeError:
            pass
    if first_tree:
        _sync_project_directory(bid_id, first_tree)


def _clear_catalog_versions(bid_id: int) -> None:
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        conn.execute("DELETE FROM bid_catalog_chapters WHERE bid_id = ?", (bid_id,))
        conn.execute("DELETE FROM bid_catalog_versions WHERE bid_id = ?", (bid_id,))
        conn.commit()


def _store_catalog_version(bid_id: int, version_name: str, version_style: str, version_type: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    now = _now_text()
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        previous = conn.execute("SELECT is_final FROM bid_catalog_versions WHERE bid_id = ? AND version_name = ?", (bid_id, version_name)).fetchone()
        was_final = bool(previous and previous["is_final"])
        conn.execute(
            "DELETE FROM bid_catalog_chapters WHERE bid_id = ? AND version_id IN (SELECT version_id FROM bid_catalog_versions WHERE bid_id = ? AND version_name = ?)",
            (bid_id, bid_id, version_name),
        )
        conn.execute("DELETE FROM bid_catalog_versions WHERE bid_id = ? AND version_name = ?", (bid_id, version_name))
        existing_count = conn.execute("SELECT COUNT(*) FROM bid_catalog_versions WHERE bid_id = ?", (bid_id,)).fetchone()[0]
        cursor = conn.execute(
            """
            INSERT INTO bid_catalog_versions (bid_id, version_name, version_style, version_type, is_final, catalog_content, create_time, update_time)
            VALUES (?, ?, ?, ?, ?, '[]', ?, ?)
            """,
            (bid_id, version_name, version_style, version_type, 1 if was_final or int(existing_count or 0) == 0 else 0, now, now),
        )
        version_id = int(cursor.lastrowid)
        tree = _insert_catalog_tree(conn, bid_id, version_id, catalog)
        conn.execute(
            "UPDATE bid_catalog_versions SET catalog_content = ?, update_time = ? WHERE version_id = ?",
            (json.dumps(tree, ensure_ascii=False), now, version_id),
        )
        row = conn.execute("SELECT * FROM bid_catalog_versions WHERE version_id = ?", (version_id,)).fetchone()
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=False)
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_catalog_versions WHERE version_id = ?", (version_id,)).fetchone()
    return _version_to_dict(row)


def _sync_project_directory(bid_id: int, tree: list[dict[str, Any]]) -> None:
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError:
        return
    rows: list[dict[str, Any]] = []
    for chapter in tree:
        cid = str(chapter["chapter_id"])
        rows.append({"id": cid, "level": 1, "title": chapter["title"], "status": "pending"})
        for section in chapter.get("children", []):
            sid = str(section["chapter_id"])
            target_words = int(section.get("target_word_count") or 800)
            rows.append(
                {
                    "id": sid,
                    "level": 2,
                    "title": section["title"],
                    "subItems": [child["title"] for child in section.get("children", [])],
                    "status": "pending",
                    "wordsTotal": target_words,
                }
            )
    project.directory = rows
    storage.save_project(project)


def _should_sync_project_directory(bid_id: int, version_id: int) -> bool:
    with storage.connect() as conn:
        final_row = conn.execute("SELECT version_id FROM bid_catalog_versions WHERE bid_id = ? AND is_final = 1 LIMIT 1", (bid_id,)).fetchone()
        if final_row:
            return int(final_row["version_id"]) == int(version_id)
        first_row = conn.execute("SELECT version_id FROM bid_catalog_versions WHERE bid_id = ? ORDER BY version_id ASC LIMIT 1", (bid_id,)).fetchone()
        return bool(first_row and int(first_row["version_id"]) == int(version_id))


def _sync_project_directory_for_version(bid_id: int, version_id: int, tree: list[dict[str, Any]]) -> None:
    if _should_sync_project_directory(bid_id, version_id):
        _sync_project_directory(bid_id, tree)


async def _commit_catalog_version(bid_id: int, version_name: str, version_style: str, version_type: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    def commit() -> dict[str, Any]:
        version = _store_catalog_version(bid_id, version_name, version_style, version_type, catalog)
        _sync_project_directory_for_version(bid_id, int(version["version_id"]), version["catalog_content"])
        return version

    pending = asyncio.create_task(asyncio.to_thread(commit))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # A thread cannot be cancelled after it starts writing. Keep the task
        # lease until both the database save and project sync have finished.
        # Shield against Starlette's level cancellation as well as Task.cancel().
        with CancelScope(shield=True):
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not pending.cancelled():
                pending.exception()  # Retrieve a failed commit even after disconnect.
        raise


def _rows_to_tree(rows: list[Any]) -> list[dict[str, Any]]:
    nodes: dict[int, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for row in rows:
        node = {
            "chapter_id": int(row["chapter_id"]),
            "title": _strip_catalog_number(row["chapter_title"]),
            "level": int(row["level"]),
            "parent_id": int(row["parent_id"]),
            "sort_order": int(row["sort_order"]),
            "target_word_count": int(row["target_word_count"] or 0),
            "children": [],
        }
        nodes[node["chapter_id"]] = node
    for row in rows:
        node = nodes[int(row["chapter_id"])]
        parent_id = int(row["parent_id"])
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _rebuild_version_content(conn, version_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM bid_catalog_chapters WHERE version_id = ? ORDER BY level ASC, parent_id ASC, sort_order ASC, chapter_id ASC",
        (version_id,),
    ).fetchall()
    tree = _rows_to_tree(rows)
    conn.execute(
        "UPDATE bid_catalog_versions SET catalog_content = ?, update_time = ? WHERE version_id = ?",
        (json.dumps(tree, ensure_ascii=False), _now_text(), version_id),
    )
    return tree


def get_catalog(bid_id: int, version_id: int | None = None) -> dict[str, Any]:
    context = _catalog_context(bid_id)
    with storage.connect() as conn:
        id_rows = conn.execute(
            "SELECT version_id FROM bid_catalog_versions WHERE bid_id = ?" + (" AND version_id = ?" if version_id else ""),
            (bid_id, version_id) if version_id else (bid_id,),
        ).fetchall()
        for id_row in id_rows:
            _rebuild_version_content(conn, int(id_row["version_id"]))
        if id_rows:
            conn.commit()
        if version_id:
            rows = conn.execute(
                "SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ? ORDER BY version_id ASC",
                (bid_id, version_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bid_catalog_versions WHERE bid_id = ? ORDER BY version_id ASC",
                (bid_id,),
            ).fetchall()

        body_rows = conn.execute(
            "SELECT chapter_id FROM bid_catalog_chapters WHERE bid_id = ? AND TRIM(COALESCE(graphic_markdown, '')) <> ''"
            + (" AND version_id = ?" if version_id else ""),
            (bid_id, version_id) if version_id else (bid_id,),
        ).fetchall()

    versions = [_version_to_dict(row) for row in rows]
    task = CATALOG_TASKS.get(_catalog_task_key(bid_id))
    status = task["status"] if task else ("completed" if versions else "idle")
    return {
        "bid_id": bid_id,
        "generation_status": status,
        "task_id": task.get("task_id") if task else None,
        "message": task.get("message") if task else "",
        "versions": versions,
        "graphic_body_chapter_ids": [int(row["chapter_id"]) for row in body_rows],
            "context": {
                "project_overview": context.get("project_overview") or "",
                "score_requirements": context.get("score_requirements") or "",
                "biz_requirements": context.get("biz_requirements") or "",
                "invalid_clauses": context.get("invalid_clauses") or "",
                "bid_title": context.get("bid_title") or "",
                "page_count": context.get("page_count") or 0,
                "required_format_sections": context.get("required_format_sections") or [],
                "required_fixed_forms": [title for title in context.get("required_format_sections", []) if tender_form_service.is_fixed_form(title)],
                "procurement_method": context.get("procurement_method") or "",
                "price_score_conflict": context.get("price_score_conflict") or "",
            },
    }


def _version_to_dict(row: Any) -> dict[str, Any]:
    try:
        content = json.loads(row["catalog_content"] or "[]")
    except json.JSONDecodeError:
        content = []
    return {
        "version_id": int(row["version_id"]),
        "bid_id": int(row["bid_id"]),
        "version_name": row["version_name"],
        "version_style": row["version_style"],
        "version_type": row["version_type"] if "version_type" in row.keys() else "standard",
        "is_final": bool(row["is_final"] if "is_final" in row.keys() else 0),
        "catalog_content": content,
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


def start_catalog_generation(bid_id: int, *, force: bool = False, style: str | None = None) -> dict[str, Any]:
    _catalog_context(bid_id)
    existing = get_catalog(bid_id).get("versions", [])
    key = _catalog_task_key(bid_id)
    with _CATALOG_TASK_LOCK:
        current = CATALOG_TASKS.get(key)
        if current and current.get("status") == "processing":
            return {"task_id": current["task_id"], "status": "processing", "newly_created": False}
        if existing and not force:
            return {"task_id": None, "status": "completed", "versions": existing, "newly_created": False}
        task_id = uuid.uuid4().hex
        CATALOG_TASKS[key] = {"task_id": task_id, "status": "processing", "message": "目录生成中", "running": False, "events": [], "subscribers": set()}
    return {"task_id": task_id, "status": "processing", "newly_created": True}


async def _generate_catalog_events(bid_id: int, task_id: str, style: str | None = None) -> AsyncIterator[dict[str, Any]]:
    key = _catalog_task_key(bid_id)
    with _CATALOG_TASK_LOCK:
        task = CATALOG_TASKS.get(key)
        if not task or task["task_id"] != task_id or task.get("running") or task.get("status") != "processing":
            return
        task["running"] = True
    try:
        yield {"type": "start", "task_id": task_id, "message": "正在生成目录"}
        context = await asyncio.to_thread(_catalog_context, bid_id)
        for version_index, (version_name, version_style, version_type) in enumerate(VERSION_STYLES, 1):
            final_style = style or version_style
            common = {"task_id": task_id, "version_name": version_name, "index": version_index}
            yield {"type": "version_start", **common, "version_style": final_style, "version_type": version_type}
            yield {"type": "progress", **common, "stage": "reference", "message": f"正在整理{version_name}的生成依据"}
            fallback = _build_rule_catalog(context, final_style)
            messages = await asyncio.to_thread(_catalog_messages, context, version_name, final_style, version_type, fallback)
            yield {"type": "progress", **common, "stage": "generating", "message": f"正在生成{version_name}"}
            raw = ""
            upstream = stream_chat_completion(messages, scene="catalog", temperature=0.35, max_tokens=6000, strict=True, json_mode=True)
            try:
                async for chunk in upstream:
                    raw += chunk
                    yield {"type": "delta", **common, "content": chunk, "preview": _catalog_preview(raw)}
            finally:
                await upstream.aclose()
            catalog = _align_catalog_to_tender_format(
                _validate_streamed_catalog(raw), context.get("required_format_sections") or [], context
            )
            yield {"type": "progress", **common, "stage": "saving", "message": f"{version_name}已生成，正在校验和保存"}
            # Keep the previous version until this complete JSON has validated.
            version = await _commit_catalog_version(bid_id, version_name, final_style, version_type, catalog)
            for chapter in version.get("catalog_content", []):
                yield {"type": "chapter", **common, "version_id": version["version_id"], "chapter": chapter}
            yield {"type": "version", **common, "version": version}
        _update_catalog_task(key, task_id, status="completed", message="目录生成完成")
        yield {"type": "done", "task_id": task_id}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = exc.message if isinstance(exc, BidCatalogServiceError) else str(exc)
        _update_catalog_task(key, task_id, status="failed", message=message)
        yield {"type": "error", "task_id": task_id, "message": message, "code": getattr(exc, "code", 50201)}
    finally:
        with _CATALOG_TASK_LOCK:
            task = CATALOG_TASKS.get(key)
            if task and task["task_id"] == task_id:
                task["running"] = False
                if task.get("status") == "processing":
                    task.update(status="cancelled", message="目录生成已中断，未完成的方案未保存")


async def _run_catalog_stream(bid_id: int, task_id: str, style: str | None = None) -> AsyncIterator[dict[str, Any]]:
    key = _catalog_task_key(bid_id)
    stream = _generate_catalog_events(bid_id, task_id, style)
    try:
        async for event in stream:
            _publish_catalog_event(key, task_id, event)
            yield event
    finally:
        try:
            await stream.aclose()
        finally:
            with _CATALOG_TASK_LOCK:
                task = CATALOG_TASKS.get(key)
                cancelled = task and task["task_id"] == task_id and task.get("status") == "cancelled"
            if cancelled:
                _publish_catalog_event(key, task_id, {"type": "error", "task_id": task_id, "code": 49901, "message": "目录生成已中断，未完成的方案未保存"})


async def run_catalog_generation_task(bid_id: int, task_id: str, style: str | None = None) -> None:
    async for _ in _run_catalog_stream(bid_id, task_id, style):
        pass


async def stream_catalog_generation_events(bid_id: int, *, force: bool = False, style: str | None = None) -> AsyncIterator[dict[str, Any]]:
    try:
        result = start_catalog_generation(bid_id, force=force, style=style)
    except Exception as exc:
        yield {"type": "error", "message": str(exc), "code": getattr(exc, "code", 50201)}
        return
    if result["status"] == "completed":
        for version in result.get("versions", []):
            yield {"type": "version", "version": version}
        yield {"type": "done", "task_id": None}
        return
    if result.get("newly_created"):
        stream = _run_catalog_stream(bid_id, str(result["task_id"]), style)
    else:
        stream = _subscribe_catalog_stream(_catalog_task_key(bid_id), str(result["task_id"]))
    try:
        async for event in stream:
            yield event
    finally:
        await stream.aclose()


def update_chapter_title(bid_id: int, chapter_id: int, payload: CatalogChapterUpdateRequest) -> dict[str, Any]:
    _ensure_bid(bid_id)
    title = _strip_catalog_number(payload.chapter_title)
    if not title:
        raise BidCatalogServiceError(40001, "章节标题不能为空")
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        row = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id)).fetchone()
        if row is None:
            raise BidCatalogServiceError(40401, "目录章节不存在", 404)
        conn.execute("UPDATE bid_catalog_chapters SET chapter_title = ? WHERE chapter_id = ?", (title, chapter_id))
        tree = _rebuild_version_content(conn, int(row["version_id"]))
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, int(row["version_id"]))
    with storage.connect() as conn:
        tree = _rebuild_version_content(conn, int(row["version_id"]))
        conn.commit()
    _sync_project_directory_for_version(bid_id, int(row["version_id"]), tree)
    return get_catalog(bid_id, int(row["version_id"]))


def create_chapter(bid_id: int, payload: CatalogChapterCreateRequest) -> dict[str, Any]:
    _ensure_bid(bid_id)
    title = _strip_catalog_number(payload.chapter_title)
    if not title:
        raise BidCatalogServiceError(40001, "章节标题不能为空")
    now = _now_text()
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        version = conn.execute("SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ?", (bid_id, payload.version_id)).fetchone()
        if version is None:
            raise BidCatalogServiceError(40401, "目录版本不存在", 404)
        parent_id = int(payload.parent_id or 0)
        level = int(payload.level)
        if parent_id:
            parent = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND version_id = ? AND chapter_id = ?", (bid_id, payload.version_id, parent_id)).fetchone()
            if parent is None:
                raise BidCatalogServiceError(40401, "父级章节不存在", 404)
            expected_level = int(parent["level"]) + 1
            if expected_level > 3:
                raise BidCatalogServiceError(40001, "最多支持三级目录")
            level = expected_level
        elif level != 1:
            raise BidCatalogServiceError(40001, "一级章节的 parent_id 必须为 0")
        sort_order = int(payload.sort_order or 0)
        if sort_order <= 0:
            sort_order = int(conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM bid_catalog_chapters WHERE version_id = ? AND parent_id = ?",
                (payload.version_id, parent_id),
            ).fetchone()[0])
        else:
            conn.execute(
                "UPDATE bid_catalog_chapters SET sort_order = sort_order + 1 WHERE version_id = ? AND parent_id = ? AND sort_order >= ?",
                (payload.version_id, parent_id, sort_order),
            )
        conn.execute(
            """
            INSERT INTO bid_catalog_chapters (bid_id, version_id, parent_id, chapter_title, level, sort_order, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (bid_id, payload.version_id, parent_id, title[:200], level, sort_order, now),
        )
        _normalize_sibling_orders(conn, int(payload.version_id), parent_id)
        tree = _rebuild_version_content(conn, int(payload.version_id))
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, int(payload.version_id), preserve_manual=True)
    with storage.connect() as conn:
        tree = _rebuild_version_content(conn, int(payload.version_id))
        conn.commit()
    _sync_project_directory_for_version(bid_id, int(payload.version_id), tree)
    return get_catalog(bid_id, int(payload.version_id))


def delete_chapter(bid_id: int, chapter_id: int) -> dict[str, Any]:
    row = _get_chapter_row(bid_id, chapter_id)
    version_id = int(row["version_id"])
    parent_id = int(row["parent_id"])
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        if int(row["level"]) == 1:
            root_count = int(conn.execute("SELECT COUNT(*) FROM bid_catalog_chapters WHERE bid_id = ? AND version_id = ? AND parent_id = 0", (bid_id, version_id)).fetchone()[0])
            if root_count <= 1:
                raise BidCatalogServiceError(40001, "至少需要保留一个一级章节")
        ids = _descendant_ids(conn, version_id, chapter_id)
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM bid_chapter_contents WHERE bid_id = ? AND chapter_id IN ({placeholders})", (bid_id, *ids))
        conn.execute(f"DELETE FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id IN ({placeholders})", (bid_id, *ids))
        _normalize_sibling_orders(conn, version_id, parent_id)
        tree = _rebuild_version_content(conn, version_id)
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=True)
    _sync_project_directory_for_version(bid_id, version_id, tree)
    return get_catalog(bid_id, version_id)


def move_chapter(bid_id: int, chapter_id: int, payload: CatalogChapterMoveRequest) -> dict[str, Any]:
    move_type = (payload.move_type or "").strip().lower()
    if move_type not in {"up", "down", "upgrade", "downgrade"}:
        raise BidCatalogServiceError(40001, "移动类型不支持")
    row = _get_chapter_row(bid_id, chapter_id)
    version_id = int(row["version_id"])
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        current = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id)).fetchone()
        if current is None:
            raise BidCatalogServiceError(40401, "目录章节不存在", 404)
        if move_type in {"up", "down"}:
            _move_within_siblings(conn, current, move_type)
        elif move_type == "upgrade":
            _upgrade_chapter(conn, current)
        else:
            _downgrade_chapter(conn, current)
        _normalize_all_orders(conn, version_id)
        tree = _rebuild_version_content(conn, version_id)
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=True)
    _sync_project_directory_for_version(bid_id, version_id, tree)
    return get_catalog(bid_id, version_id)


def set_final_version(bid_id: int, version_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ?", (bid_id, version_id)).fetchone()
        if row is None:
            raise BidCatalogServiceError(40401, "目录版本不存在", 404)
        conn.execute("UPDATE bid_catalog_versions SET is_final = 0 WHERE bid_id = ?", (bid_id,))
        conn.execute("UPDATE bid_catalog_versions SET is_final = 1, update_time = ? WHERE version_id = ?", (_now_text(), version_id))
        tree = _rebuild_version_content(conn, version_id)
        conn.commit()
    _sync_project_directory(bid_id, tree)
    return get_catalog(bid_id)


def replace_version_tree(bid_id: int, version_id: int, catalog_content: list[dict[str, Any]]) -> dict[str, Any]:
    _ensure_bid(bid_id)
    if not catalog_content:
        raise BidCatalogServiceError(40001, "目录内容不能为空")
    snapshot_rows = _flatten_catalog_snapshot(catalog_content)
    if not snapshot_rows:
        raise BidCatalogServiceError(40001, "目录内容不能为空")
    now = _now_text()
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        version = conn.execute("SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ?", (bid_id, version_id)).fetchone()
        if version is None:
            raise BidCatalogServiceError(40401, "目录版本不存在", 404)
        existing_ids = {int(row["chapter_id"]) for row in conn.execute("SELECT chapter_id FROM bid_catalog_chapters WHERE bid_id = ? AND version_id = ?", (bid_id, version_id)).fetchall()}
        snapshot_ids = {item["chapter_id"] for item in snapshot_rows if item.get("chapter_id")}
        stale_ids = sorted(existing_ids - snapshot_ids)
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            conn.execute(f"DELETE FROM bid_chapter_contents WHERE bid_id = ? AND version_id = ? AND chapter_id IN ({placeholders})", (bid_id, version_id, *stale_ids))
            conn.execute(f"DELETE FROM bid_catalog_chapters WHERE bid_id = ? AND version_id = ? AND chapter_id IN ({placeholders})", (bid_id, version_id, *stale_ids))
        for item in snapshot_rows:
            chapter_id = int(item["chapter_id"])
            if chapter_id in existing_ids:
                conn.execute(
                    """
                    UPDATE bid_catalog_chapters
                    SET parent_id = ?, chapter_title = ?, level = ?, sort_order = ?
                    WHERE bid_id = ? AND version_id = ? AND chapter_id = ?
                    """,
                    (item["parent_id"], item["title"], item["level"], item["sort_order"], bid_id, version_id, chapter_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO bid_catalog_chapters (chapter_id, bid_id, version_id, parent_id, chapter_title, level, sort_order, create_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chapter_id, bid_id, version_id, item["parent_id"], item["title"], item["level"], item["sort_order"], now),
                )
        tree = _rebuild_version_content(conn, version_id)
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=False)
    with storage.connect() as conn:
        tree = _rebuild_version_content(conn, version_id)
        conn.commit()
    _sync_project_directory_for_version(bid_id, version_id, tree)
    return get_catalog(bid_id, version_id)


def _flatten_catalog_snapshot(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]], parent_id: int, level: int) -> None:
        for index, node in enumerate(items, 1):
            try:
                chapter_id = int(node.get("chapter_id") or 0)
            except (TypeError, ValueError):
                chapter_id = 0
            title = _strip_catalog_number(str(node.get("title") or ""))
            if not chapter_id or not title or level > 3:
                continue
            result.append(
                {
                    "chapter_id": chapter_id,
                    "parent_id": parent_id,
                    "title": title[:200],
                    "level": level,
                    "sort_order": index,
                }
            )
            children = node.get("children") or []
            if isinstance(children, list):
                walk([child for child in children if isinstance(child, dict)], chapter_id, level + 1)

    walk(nodes, 0, 1)
    return result


def _normalize_sibling_orders(conn, version_id: int, parent_id: int) -> None:
    rows = conn.execute(
        "SELECT chapter_id FROM bid_catalog_chapters WHERE version_id = ? AND parent_id = ? ORDER BY sort_order ASC, chapter_id ASC",
        (version_id, parent_id),
    ).fetchall()
    for index, item in enumerate(rows, 1):
        conn.execute("UPDATE bid_catalog_chapters SET sort_order = ? WHERE chapter_id = ?", (index, int(item["chapter_id"])))


def _normalize_all_orders(conn, version_id: int) -> None:
    parents = conn.execute("SELECT DISTINCT parent_id FROM bid_catalog_chapters WHERE version_id = ?", (version_id,)).fetchall()
    for row in parents:
        _normalize_sibling_orders(conn, version_id, int(row["parent_id"]))


def _descendant_ids(conn, version_id: int, chapter_id: int) -> list[int]:
    rows = conn.execute("SELECT chapter_id, parent_id FROM bid_catalog_chapters WHERE version_id = ?", (version_id,)).fetchall()
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(int(row["chapter_id"]))
    result: list[int] = []

    def walk(node_id: int) -> None:
        result.append(node_id)
        for child_id in children.get(node_id, []):
            walk(child_id)

    walk(chapter_id)
    return result


def _move_within_siblings(conn, row: Any, move_type: str) -> None:
    op = "<" if move_type == "up" else ">"
    direction = "DESC" if move_type == "up" else "ASC"
    sibling = conn.execute(
        f"""
        SELECT * FROM bid_catalog_chapters
        WHERE version_id = ? AND parent_id = ? AND sort_order {op} ?
        ORDER BY sort_order {direction}, chapter_id {direction} LIMIT 1
        """,
        (row["version_id"], row["parent_id"], row["sort_order"]),
    ).fetchone()
    if sibling is None:
        return
    conn.execute("UPDATE bid_catalog_chapters SET sort_order = ? WHERE chapter_id = ?", (int(sibling["sort_order"]), int(row["chapter_id"])))
    conn.execute("UPDATE bid_catalog_chapters SET sort_order = ? WHERE chapter_id = ?", (int(row["sort_order"]), int(sibling["chapter_id"])))


def _update_subtree_levels(conn, version_id: int, chapter_id: int, delta: int) -> None:
    ids = _descendant_ids(conn, version_id, chapter_id)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT chapter_id, level FROM bid_catalog_chapters WHERE chapter_id IN ({placeholders})", ids).fetchall()
    for row in rows:
        next_level = int(row["level"]) + delta
        if next_level < 1 or next_level > 3:
            raise BidCatalogServiceError(40001, "调整后目录层级会超过三级限制")
    conn.execute(f"UPDATE bid_catalog_chapters SET level = level + ? WHERE chapter_id IN ({placeholders})", (delta, *ids))


def _upgrade_chapter(conn, row: Any) -> None:
    level = int(row["level"])
    if level <= 1:
        raise BidCatalogServiceError(40001, "一级章节不能继续升级")
    parent = conn.execute("SELECT * FROM bid_catalog_chapters WHERE chapter_id = ?", (int(row["parent_id"]),)).fetchone()
    if parent is None:
        raise BidCatalogServiceError(40001, "父级章节不存在，无法升级")
    new_parent_id = int(parent["parent_id"])
    new_order = int(parent["sort_order"]) + 1
    conn.execute(
        "UPDATE bid_catalog_chapters SET sort_order = sort_order + 1 WHERE version_id = ? AND parent_id = ? AND sort_order >= ?",
        (row["version_id"], new_parent_id, new_order),
    )
    conn.execute("UPDATE bid_catalog_chapters SET parent_id = ?, sort_order = ? WHERE chapter_id = ?", (new_parent_id, new_order, int(row["chapter_id"])))
    _update_subtree_levels(conn, int(row["version_id"]), int(row["chapter_id"]), -1)


def _downgrade_chapter(conn, row: Any) -> None:
    level = int(row["level"])
    if level >= 3:
        raise BidCatalogServiceError(40001, "三级小节不能继续降级")
    previous = conn.execute(
        """
        SELECT * FROM bid_catalog_chapters
        WHERE version_id = ? AND parent_id = ? AND sort_order < ?
        ORDER BY sort_order DESC, chapter_id DESC LIMIT 1
        """,
        (row["version_id"], row["parent_id"], row["sort_order"]),
    ).fetchone()
    if previous is None:
        raise BidCatalogServiceError(40001, "当前章节前方没有同级章节，无法降级")
    _update_subtree_levels(conn, int(row["version_id"]), int(row["chapter_id"]), 1)
    new_order = int(conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM bid_catalog_chapters WHERE version_id = ? AND parent_id = ?",
        (row["version_id"], int(previous["chapter_id"])),
    ).fetchone()[0])
    conn.execute(
        "UPDATE bid_catalog_chapters SET parent_id = ?, sort_order = ? WHERE chapter_id = ?",
        (int(previous["chapter_id"]), new_order, int(row["chapter_id"])),
    )


async def expand_chapter(bid_id: int, chapter_id: int, payload: CatalogChapterActionRequest) -> dict[str, Any]:
    row = _get_chapter_row(bid_id, chapter_id)
    if int(row["level"]) >= 3:
        raise BidCatalogServiceError(40001, "三级小节已是最深层级，不能继续扩写")
    context = _catalog_context(bid_id)
    fallback_titles = _fallback_expand_titles(row["chapter_title"], int(row["level"]))
    result = await chat_completion(
        [
            {"role": "system", "content": "你是投标目录扩写助手。只输出 JSON 字符串数组。"},
            {
                "role": "user",
                "content": (
                    f"项目名称：{context['bid_title']}\n章节：{row['chapter_title']}\n"
                    f"评分要求：{context.get('score_requirements', '')[:1500]}\n"
                    f"商务/资质要求：{context.get('biz_requirements', '')[:900]}\n"
                    f"扩写要求：{payload.instruction or '补充专业、可执行的下级目录'}\n"
                    "请生成3-5个下级目录标题，标题需适合投标文件。"
                ),
            },
        ], scene="catalog",
        max_tokens=800,
        fallback=json.dumps(fallback_titles, ensure_ascii=False),
    )
    titles = _json_from_text(result.content)
    if not isinstance(titles, list):
        titles = fallback_titles
    titles = [str(item).strip() for item in titles if str(item).strip()][:5] or fallback_titles
    return _append_children(bid_id, row, titles)


async def optimize_chapter(bid_id: int, chapter_id: int, payload: CatalogChapterActionRequest) -> dict[str, Any]:
    row = _get_chapter_row(bid_id, chapter_id)
    context = _catalog_context(bid_id)
    fallback = _optimize_title(str(row["chapter_title"]))
    result = await chat_completion(
        [
            {"role": "system", "content": "你是投标目录优化助手。只输出一个优化后的章节标题，不要解释。"},
            {
                "role": "user",
                "content": (
                    f"项目名称：{context['bid_title']}\n原章节标题：{row['chapter_title']}\n"
                    f"评分要求：{context.get('score_requirements', '')[:1200]}\n"
                    f"商务/资质要求：{context.get('biz_requirements', '')[:800]}\n"
                    f"优化要求：{payload.instruction or '表达更规范、更贴合评分点'}"
                ),
            },
        ], scene="catalog",
        max_tokens=300,
        fallback=fallback,
    )
    title = re.sub(r"^[`\"'\s]+|[`\"'\s]+$", "", result.content.strip()) or fallback
    return update_chapter_title(bid_id, chapter_id, CatalogChapterUpdateRequest(chapter_title=title[:200]))


def _get_chapter_row(bid_id: int, chapter_id: int):
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?", (bid_id, chapter_id)).fetchone()
    if row is None:
        raise BidCatalogServiceError(40401, "目录章节不存在", 404)
    return row


def _fallback_expand_titles(title: str, level: int) -> list[str]:
    if level == 1:
        return ["总体说明", "实施路径", "保障措施", "风险控制"]
    return ["编写目标", "关键措施", "执行流程", "成果交付"]


def _append_children(bid_id: int, row: Any, titles: list[str]) -> dict[str, Any]:
    now = _now_text()
    child_level = int(row["level"]) + 1
    with storage.connect() as conn:
        chapter_collaboration.guard_catalog_change(conn, bid_id)
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM bid_catalog_chapters WHERE version_id = ? AND parent_id = ?",
            (row["version_id"], row["chapter_id"]),
        ).fetchone()[0]
        for idx, title in enumerate(titles, 1):
            conn.execute(
                """
                INSERT INTO bid_catalog_chapters (bid_id, version_id, parent_id, chapter_title, level, sort_order, create_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (bid_id, row["version_id"], row["chapter_id"], title, child_level, int(max_order) + idx, now),
            )
        tree = _rebuild_version_content(conn, int(row["version_id"]))
        conn.commit()
    bid_word_count_service.calculate_and_assign_word_count(bid_id, int(row["version_id"]))
    with storage.connect() as conn:
        tree = _rebuild_version_content(conn, int(row["version_id"]))
        conn.commit()
    _sync_project_directory_for_version(bid_id, int(row["version_id"]), tree)
    return get_catalog(bid_id, int(row["version_id"]))


def _optimize_title(title: str) -> str:
    title = re.sub(r"\s+", "", title.strip())
    if "方案" not in title and "附件" not in title:
        title = f"{title}方案"
    return title


def catalog_docx_bytes(bid_id: int, version_id: int) -> tuple[bytes, str]:
    catalog = get_catalog(bid_id, version_id)
    versions = catalog.get("versions") or []
    if not versions:
        raise BidCatalogServiceError(40401, "目录版本不存在", 404)
    version = versions[0]
    doc = Document()
    doc.add_heading(version["version_name"], level=1)
    doc.add_paragraph(f"生成风格：{version['version_style']}")
    for chapter in version["catalog_content"]:
        doc.add_heading(chapter["title"], level=1)
        for section in chapter.get("children", []):
            doc.add_heading(section["title"], level=2)
            for item in section.get("children", []):
                doc.add_paragraph(item["title"], style="List Bullet")
    stream = BytesIO()
    doc.save(stream)
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", version["version_name"])
    return stream.getvalue(), f"{safe_name}.docx"


def _strip_catalog_number(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"^\s*(?:第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*)+", "", title)
    title = re.sub(r"^\s*\d+(?:\.\d+)+\s*", "", title)
    title = re.sub(r"^\s*\d+(?:[、.．]\s*|\s+)", "", title)
    return title.strip()
