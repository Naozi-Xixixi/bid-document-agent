from __future__ import annotations

import asyncio
import copy
import json
import re
from io import BytesIO
from typing import Any

from docx import Document

from app.schemas.tender import (
    DirectoryActionRequest,
    DirectoryGenerateRequest,
    ProjectState,
    SectionActionRequest,
    SectionGenerateRequest,
    TenderParseRequest,
)
from app.schemas.wordagent import DocumentOutput, plain_text_document
from app.services import storage
from app.services.qwen_client import LLMResult, chat_completion
from app.services.qwen_client import chat_completion as generation_chat_completion
from app.services.wordagent_protocol import done, event, status, text


DEFAULT_SECTION_TARGET = 800

DEFAULT_DIRECTORY: list[dict[str, Any]] = [
    {"id": "c1", "level": 1, "title": "第一章 项目理解与总体方案", "status": "pending"},
    {
        "id": "c1s1",
        "level": 2,
        "title": "第一节 项目背景与需求理解",
        "subItems": ["项目概况", "服务目标", "关键约束"],
        "status": "pending",
        "wordsTotal": 800,
    },
    {
        "id": "c1s2",
        "level": 2,
        "title": "第二节 总体服务思路",
        "subItems": ["总体框架", "实施路径", "质量保障"],
        "status": "pending",
        "wordsTotal": 800,
    },
    {"id": "c2", "level": 1, "title": "第二章 服务实施方案", "status": "pending"},
    {
        "id": "c2s1",
        "level": 2,
        "title": "第一节 组织架构与人员配置",
        "subItems": ["团队组织", "岗位职责", "协同机制"],
        "status": "pending",
        "wordsTotal": 800,
    },
    {
        "id": "c2s2",
        "level": 2,
        "title": "第二节 进度计划与交付管理",
        "subItems": ["阶段计划", "里程碑", "交付验收"],
        "status": "pending",
        "wordsTotal": 800,
    },
    {"id": "c3", "level": 1, "title": "第三章 质量、安全与风险控制", "status": "pending"},
    {
        "id": "c3s1",
        "level": 2,
        "title": "第一节 质量保障措施",
        "subItems": ["质量标准", "检查机制", "持续改进"],
        "status": "pending",
        "wordsTotal": 800,
    },
    {
        "id": "c3s2",
        "level": 2,
        "title": "第二节 风险识别与应急预案",
        "subItems": ["风险清单", "响应机制", "应急演练"],
        "status": "pending",
        "wordsTotal": 800,
    },
]


def _project_title(project: ProjectState) -> str:
    return project.settings.bidTitle.strip() or "投标项目"


def _uploaded_context(project: ProjectState) -> str:
    notes = [storage.read_uploaded_text(info) for info in project.files]
    return "\n".join(item for item in notes if item).strip()


def _source_text(project: ProjectState, pasted_text: str = "") -> str:
    return (pasted_text or _uploaded_context(project)).strip()


def _llm_meta(result: LLMResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "model": result.model,
        "usedApi": result.usedApi,
        "error": result.error,
    }


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


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in re.split(r"[、,，;；\n]+", value) if item.strip()]
    return []


def _analysis_fallback(project: ProjectState, source_hint: str) -> dict[str, Any]:
    title = _project_title(project)
    preview = source_hint[:1200] if source_hint else "尚未上传或粘贴可解析文本，当前返回基础分析。"
    return {
        "summary": f"{title}已进入招标文件解读流程。系统将抽取项目概况、评分关注点和响应风险，用于后续目录和正文编写。",
        "sourcePreview": preview,
        "requirements": [
            "完整响应招标文件的实质性要求",
            "围绕评分点组织目录和正文内容",
            "商务标与技术标口径保持一致",
            "暗标项目需规避公司名称、人员姓名等敏感信息",
        ],
        "scoringPoints": ["项目理解", "实施方案", "团队配置", "质量保障", "风险控制"],
        "risks": [
            "需确认页数、字数、格式、盖章和签字要求",
            "需检查是否存在暗标、偏离表、服务清单等特殊约束",
        ],
    }


async def parse_tender(project: ProjectState, payload: TenderParseRequest) -> ProjectState:
    source = _source_text(project, payload.pastedText)
    fallback = _analysis_fallback(project, source)
    result = await chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是投标文件解读助手。请根据招标资料提炼结构化信息，只输出 JSON，"
                    "字段为 summary、requirements、scoringPoints、risks。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"项目名称：{_project_title(project)}\n"
                    f"标书类型：{project.settings.bidType}\n"
                    "请解读以下招标信息，输出可供目录和章节生成使用的 JSON：\n"
                    f"{source[:12000] if source else '暂无招标正文，请生成通用分析。'}"
                ),
            },
        ], scene="tender_parse",
        max_tokens=1600,
        fallback=json.dumps(fallback, ensure_ascii=False),
    )
    parsed = _json_from_text(result.content)
    if not isinstance(parsed, dict):
        parsed = fallback
    project.tenderAnalysis = {
        "summary": str(parsed.get("summary") or fallback["summary"]),
        "sourcePreview": (source or fallback["sourcePreview"])[:1200],
        "requirements": _string_list(parsed.get("requirements")) or fallback["requirements"],
        "scoringPoints": _string_list(parsed.get("scoringPoints")) or fallback["scoringPoints"],
        "risks": _string_list(parsed.get("risks")) or fallback["risks"],
        "llm": _llm_meta(result),
    }
    return storage.save_project(project)


def _default_directory() -> list[dict[str, Any]]:
    return copy.deepcopy(DEFAULT_DIRECTORY)


def _clean_title(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[\-•*\s]+", "", line)
    line = re.sub(r"^\d+(?:\.\d+)*[、.)）\s]+", "", line)
    return line.strip()


def _parse_manual_directory(text_value: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    chapter_index = 0
    section_index = 0
    for raw in text_value.splitlines():
        title = _clean_title(raw)
        if not title:
            continue
        is_chapter = bool(re.search(r"第.+章", title)) or ("章" in title and "节" not in title)
        if is_chapter:
            chapter_index += 1
            section_index = 0
            rows.append({"id": f"c{chapter_index}", "level": 1, "title": title, "status": "pending"})
        else:
            if chapter_index == 0:
                chapter_index = 1
            section_index += 1
            rows.append(
                {
                    "id": f"c{chapter_index}s{section_index}",
                    "level": 2,
                    "title": title,
                    "subItems": [],
                    "status": "pending",
                    "wordsTotal": DEFAULT_SECTION_TARGET,
                }
            )
    return rows


def _normalize_directory_rows(raw_rows: Any, fallback_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if isinstance(raw_rows, dict):
        raw_rows = raw_rows.get("directory") or raw_rows.get("rows") or raw_rows.get("outline")
    if not isinstance(raw_rows, list):
        return copy.deepcopy(fallback_rows or DEFAULT_DIRECTORY)

    normalized: list[dict[str, Any]] = []
    chapter_index = 0
    section_index = 0
    for raw in raw_rows:
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("name") or "").strip()
        if not title:
            continue
        level_value = raw.get("level", 2)
        try:
            level = 1 if int(level_value) == 1 else 2
        except (TypeError, ValueError):
            level = 1 if "章" in title and "节" not in title else 2
        if level == 1:
            chapter_index += 1
            section_index = 0
            normalized.append(
                {
                    "id": str(raw.get("id") or f"c{chapter_index}"),
                    "level": 1,
                    "title": title,
                    "status": str(raw.get("status") or "pending"),
                }
            )
        else:
            if chapter_index == 0:
                chapter_index = 1
            section_index += 1
            normalized.append(
                {
                    "id": str(raw.get("id") or f"c{chapter_index}s{section_index}"),
                    "level": 2,
                    "title": title,
                    "subItems": _string_list(raw.get("subItems") or raw.get("items") or raw.get("children")),
                    "status": str(raw.get("status") or "pending"),
                    "wordsTotal": int(raw.get("wordsTotal") or raw.get("wordTarget") or DEFAULT_SECTION_TARGET),
                    "wordsDone": int(raw.get("wordsDone") or 0),
                    "hasPPT": bool(raw.get("hasPPT", False)),
                    "hasRef": bool(raw.get("hasRef", False)),
                    "isFree": bool(raw.get("isFree", False)),
                }
            )
    return normalized or copy.deepcopy(fallback_rows or DEFAULT_DIRECTORY)


def _directory_prompt(project: ProjectState, mode: str, instruction: str = "") -> list[dict[str, str]]:
    analysis = project.tenderAnalysis or {}
    return [
        {
            "role": "system",
            "content": (
                "你是投标技术标目录策划助手。只生成目录结构，不生成正文内容。"
                "请只输出 JSON 数组，每行包含 id、level、title、subItems、status、wordsTotal。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"任务：{mode}\n"
                f"项目名称：{_project_title(project)}\n"
                f"页数目标：{project.settings.pageCount}\n"
                f"招标解读：{json.dumps(analysis, ensure_ascii=False)[:4000]}\n"
                f"当前目录：{json.dumps(project.directory, ensure_ascii=False)[:4000]}\n"
                f"额外要求：{instruction or '无'}\n"
                "注意：目录行只需要章/节标题和小要点，正文由后续按单节按钮逐节生成。"
            ),
        },
    ]


async def generate_directory(project: ProjectState, payload: DirectoryGenerateRequest) -> ProjectState:
    if payload.strategy == "manual" and payload.manualDirectory.strip():
        rows = _parse_manual_directory(payload.manualDirectory)
        project.directory = _normalize_directory_rows(rows)
        return storage.save_project(project)

    fallback_rows = _default_directory()
    result = await generation_chat_completion(
        _directory_prompt(project, "根据评分点生成技术标目录"),
        max_tokens=1800, scene="catalog",
        fallback=json.dumps(fallback_rows, ensure_ascii=False),
    )
    project.directory = _normalize_directory_rows(_json_from_text(result.content), fallback_rows)
    project.sections = {}
    return storage.save_project(project)


def _expanded_directory_fallback(project: ProjectState) -> list[dict[str, Any]]:
    rows = _normalize_directory_rows(project.directory or DEFAULT_DIRECTORY)
    for row in rows:
        if row.get("level") == 2 and not row.get("subItems"):
            row["subItems"] = ["需求响应", "实施措施", "交付成果"]
    return rows


async def expand_directory(project: ProjectState, payload: DirectoryActionRequest) -> ProjectState:
    fallback_rows = _expanded_directory_fallback(project)
    result = await generation_chat_completion(
        _directory_prompt(project, "AI扩写目录：补充节下小要点，但不要生成正文", payload.instruction),
        max_tokens=1800,
        fallback=json.dumps(fallback_rows, ensure_ascii=False),
        scene="catalog",
    )
    project.directory = _normalize_directory_rows(_json_from_text(result.content), fallback_rows)
    return storage.save_project(project)


async def optimize_directory(project: ProjectState, payload: DirectoryActionRequest) -> ProjectState:
    fallback_rows = _normalize_directory_rows(project.directory or DEFAULT_DIRECTORY)
    result = await generation_chat_completion(
        _directory_prompt(project, "AI优化目录：调整顺序、标题表达和评分点覆盖，但不要生成正文", payload.instruction),
        max_tokens=1800,
        fallback=json.dumps(fallback_rows, ensure_ascii=False),
        scene="catalog",
    )
    project.directory = _normalize_directory_rows(_json_from_text(result.content), fallback_rows)
    return storage.save_project(project)


def _find_directory_row(project: ProjectState, section_id: str) -> dict[str, Any] | None:
    for row in project.directory:
        if str(row.get("id")) == section_id:
            return row
    return None


def next_pending_row(project: ProjectState) -> dict[str, Any] | None:
    for row in project.directory:
        row_id = str(row.get("id") or "")
        if row.get("level") == 2 and row_id and row_id not in project.sections:
            return row
    return None


def _requirements(project: ProjectState, extra: list[str] | None = None) -> list[str]:
    analysis = project.tenderAnalysis or {}
    result = _string_list(extra) or _string_list(analysis.get("requirements"))
    return result or ["响应招标文件要求", "突出方案可执行性", "体现质量和风险控制"]


def _section_fallback_text(title: str, sub_items: list[str], requirements: list[str], word_target: int) -> str:
    points = "、".join(sub_items or ["需求响应", "实施措施", "交付成果"])
    reqs = "；".join(requirements[:4])
    paragraphs = [
        f"本节围绕“{title}”展开，结合招标文件要求和项目实施目标，形成可直接纳入技术标的正文内容。编写时重点保证内容与评分点对应、逻辑层次清晰、表达稳健专业。",
        f"在实施层面，本节重点覆盖{points}。相关工作将通过责任分工、过程管控、节点检查和成果验收形成闭环，确保方案可执行、可检查、可追踪。",
        f"针对招标响应要求，本节将重点落实：{reqs}。后续接入真实素材库后，可继续补充企业案例、人员资质、服务承诺和历史成果，使章节内容更贴合实际投标场景。",
        f"当前章节目标字数约为{word_target}字。章节内容将围绕招标要求、实施措施和交付成果展开，并在后续持续结合素材库与 WordAgent 工具链增强。",
    ]
    return "\n\n".join(paragraphs)


def _split_paragraphs(content: str) -> list[str]:
    lines = [line.strip() for line in re.split(r"\n{2,}|\r?\n", content) if line.strip()]
    if not lines:
        return ["当前章节尚未生成有效内容。"]
    return lines


async def generate_section(project: ProjectState, payload: SectionGenerateRequest) -> ProjectState:
    row = _find_directory_row(project, payload.sectionId)
    title = payload.title or str(row.get("title") if row else "未命名章节")
    sub_items = payload.subItems or _string_list(row.get("subItems") if row else None)
    requirements = _requirements(project, payload.requirements)
    word_target = max(200, int(payload.wordTarget or (row or {}).get("wordsTotal") or DEFAULT_SECTION_TARGET))
    fallback = _section_fallback_text(title, sub_items, requirements, word_target)

    result = await generation_chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "你是投标技术标正文写作助手。必须只生成用户指定的单个章节，"
                    "不要生成其它章节，不要输出整篇标书，不要输出目录。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"项目名称：{_project_title(project)}\n"
                    f"章节ID：{payload.sectionId}\n"
                    f"章节标题：{title}\n"
                    f"节内要点：{'、'.join(sub_items) if sub_items else '按标题自行拆解'}\n"
                    f"响应要求：{'；'.join(requirements)}\n"
                    f"目标字数：约{word_target}字\n"
                    f"招标解读：{json.dumps(project.tenderAnalysis or {}, ensure_ascii=False)[:3000]}\n"
                    "请输出正式正文段落，语言适合投标技术标，分段清晰，不要使用 Markdown 标题。"
                ),
            },
        ], scene="bid_content",
        max_tokens=max(1600, min(6000, word_target * 3)),
        fallback=fallback,
    )
    paragraphs = _split_paragraphs(result.content)
    content = "\n\n".join(paragraphs)
    doc = plain_text_document(title, paragraphs)
    word_count = sum(len(paragraph) for paragraph in paragraphs)

    project.sections[payload.sectionId] = {
        "sectionId": payload.sectionId,
        "title": title,
        "subItems": sub_items,
        "requirements": requirements,
        "content": content,
        "wordCount": word_count,
        "wordTarget": word_target,
        "document": doc.model_dump(),
        "status": "done",
        "llm": _llm_meta(result),
    }
    if row:
        row["status"] = "done"
        row["wordsDone"] = word_count
        row["wordsTotal"] = word_target
    return storage.save_project(project)


async def optimize_section(project: ProjectState, section_id: str, payload: SectionActionRequest) -> ProjectState:
    current = project.sections.get(section_id)
    row = _find_directory_row(project, section_id)
    if not current:
        title = str(row.get("title") if row else section_id)
        generated = SectionGenerateRequest(
            sectionId=section_id,
            title=title,
            subItems=_string_list(row.get("subItems") if row else None),
            wordTarget=payload.wordTarget or int((row or {}).get("wordsTotal") or DEFAULT_SECTION_TARGET),
        )
        return await generate_section(project, generated)

    fallback = str(current.get("content") or "")
    result = await generation_chat_completion(
        [
            {
                "role": "system",
                "content": "你是投标文件润色助手。只优化指定章节正文，不新增其它章节。",
            },
            {
                "role": "user",
                "content": (
                    f"章节标题：{current.get('title')}\n"
                    f"优化要求：{payload.instruction or '增强专业性、评分点覆盖和表达流畅度'}\n"
                    f"目标字数：{payload.wordTarget or current.get('wordTarget') or DEFAULT_SECTION_TARGET}\n"
                    f"原文：\n{fallback[:8000]}"
                ),
            },
        ], scene="bid_content",
        max_tokens=3000,
        fallback=fallback,
    )
    paragraphs = _split_paragraphs(result.content)
    content = "\n\n".join(paragraphs)
    current.update(
        {
            "content": content,
            "wordCount": sum(len(paragraph) for paragraph in paragraphs),
            "document": plain_text_document(str(current.get("title") or section_id), paragraphs).model_dump(),
            "status": "done",
            "llm": _llm_meta(result),
        }
    )
    if row:
        row["status"] = "done"
        row["wordsDone"] = current["wordCount"]
    return storage.save_project(project)


async def generate_ppt_outline(project: ProjectState, section_id: str) -> dict[str, Any]:
    section = project.sections.get(section_id) or {}
    row = _find_directory_row(project, section_id) or {}
    title = str(section.get("title") or row.get("title") or section_id)
    content = str(section.get("content") or "")
    fallback = {
        "sectionId": section_id,
        "title": title,
        "status": "ready",
        "slides": [
            {"title": title, "bullets": _string_list(row.get("subItems")) or ["核心目标", "实施路径", "交付成果"]},
            {"title": "方案亮点", "bullets": ["响应招标要求", "突出实施闭环", "便于后续扩展为真实 PPT 导出"]},
        ],
    }
    result = await generation_chat_completion(
        [
            {"role": "system", "content": "你是投标章节转 PPT 大纲助手。只输出 JSON。"},
            {
                "role": "user",
                "content": f"请把以下章节生成 2-4 页 PPT 大纲 JSON，字段 slides/title/bullets：\n标题：{title}\n正文：{content[:6000]}",
            },
        ], scene="bid_content",
        max_tokens=1200,
        fallback=json.dumps(fallback, ensure_ascii=False),
    )
    parsed = _json_from_text(result.content)
    if not isinstance(parsed, dict):
        parsed = fallback
    parsed["llm"] = _llm_meta(result)
    return parsed


async def chat_once(message: str, mode: str = "agent", project: ProjectState | None = None) -> tuple[str, DocumentOutput, list[dict[str, Any]]]:
    title = _project_title(project) if project else "投标项目"
    result = await generation_chat_completion(
        [
            {"role": "system", "content": "你是 WordAgent 桥接助手，回答要简洁、可执行。"},
            {"role": "user", "content": f"项目：{title}\n模式：{mode}\n用户问题：{message}"},
        ], scene="bid_content",
        max_tokens=1200,
        fallback=f"已收到请求：{message}\n\n当前将结合投标文件工作流继续处理。",
    )
    paragraphs = _split_paragraphs(result.content)
    document = plain_text_document("Agent 回复", paragraphs)
    return result.content, document, [{"type": "status", "content": "ready"}, {"type": "llm", **_llm_meta(result)}]


async def stream_chat(message: str, mode: str = "agent", project: ProjectState | None = None):
    yield status("已收到前端请求，正在处理", loading=True)
    await asyncio.sleep(0.05)
    yield event("token_stats", current_tokens=0, max_tokens=128000, percentage=0)
    answer, _, events = await chat_once(message, mode, project)
    for item in events:
        yield event(item.get("type", "status"), **{k: v for k, v in item.items() if k != "type"})
    yield text(answer)
    yield done()


def build_chat_document(message: str) -> DocumentOutput:
    return plain_text_document(
        "投标文档生成结果",
        [
            f"根据你的请求生成内容：{message}",
            "当前文档结构可继续接入千问、企业素材库和 WordAgent 工具链。",
        ],
    )


def directory_as_markdown(project: ProjectState) -> str:
    rows = project.directory or DEFAULT_DIRECTORY
    lines = [f"# {_project_title(project)}目录", ""]
    for row in rows:
        title = str(row.get("title") or "")
        if not title:
            continue
        if row.get("level") == 1:
            lines.append(f"## {title}")
        else:
            lines.append(f"- {title}")
            for item in _string_list(row.get("subItems")):
                lines.append(f"  - {item}")
    return "\n".join(lines).strip() + "\n"


def export_project_docx(project: ProjectState) -> bytes:
    doc = Document()
    doc.add_heading(project.settings.bidTitle or "投标文件", level=1)
    if project.tenderAnalysis:
        doc.add_heading("招标文件解读摘要", level=2)
        doc.add_paragraph(str(project.tenderAnalysis.get("summary", "")))
    if not project.directory:
        doc.add_paragraph("当前项目尚未生成目录。")
    for row in project.directory:
        if row.get("level") == 1:
            doc.add_heading(str(row.get("title", "")), level=1)
            continue
        section = project.sections.get(str(row.get("id")))
        if section:
            doc.add_heading(str(section.get("title") or row.get("title") or ""), level=2)
            for paragraph in _split_paragraphs(str(section.get("content", ""))):
                doc.add_paragraph(paragraph)
        else:
            doc.add_heading(str(row.get("title", "")), level=2)
            doc.add_paragraph("（本节尚未生成，可在前端点击“开始编写”逐节生成。）")
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

