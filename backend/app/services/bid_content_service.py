from __future__ import annotations

import asyncio
import html
import json
import re
import time
from contextlib import aclosing
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any, AsyncIterator

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from app.core.config import settings
from app.core.tenant_context import get_current_tenant
from app.services import chapter_collaboration
from app.schemas.bid_content import ChapterContentSaveRequest
from app.services import bid_case_reference_service, bid_draft_guard, bid_reference_service, bid_requirement_context, bid_word_count_service, rag_case_service, storage
from app.services.qwen_client import stream_chat_completion
from app.services.model_preferences import selected_model
from app.services.officecli_docx_formatter import enhance_docx_with_officecli


class BidContentServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


STREAM_DB_SAVE_MIN_CHARS = 260
STREAM_DB_SAVE_MIN_SECONDS = 0.8
KNOWLEDGE_IMAGE_MARKER_RE = re.compile(r"^\[\[knowledge-image:item_id=(\d+)\]\]$")
_ACTIVE_CONTENT_GENERATIONS: dict[tuple[str, int, int], object] = {}
_CONTENT_GENERATION_LOCK = Lock()


def _claim_content_generation(bid_id: int, chapter_id: int, owner: object) -> tuple[str, int, int]:
    tenant = get_current_tenant()
    key = (tenant.company_id if tenant else "default", bid_id, chapter_id)
    with _CONTENT_GENERATION_LOCK:
        if key in _ACTIVE_CONTENT_GENERATIONS:
            raise BidContentServiceError(40901, "本章节正在生成，请等待完成或停止后重试", 409)
        _ACTIVE_CONTENT_GENERATIONS[key] = owner
    return key


def _release_content_generation(key: tuple[str, int, int] | None, owner: object) -> None:
    with _CONTENT_GENERATION_LOCK:
        if key is not None and _ACTIVE_CONTENT_GENERATIONS.get(key) is owner:
            del _ACTIVE_CONTENT_GENERATIONS[key]


def _now_text() -> str:
    return storage.now_utc().isoformat()


def _ensure_bid(bid_id: int):
    try:
        return storage.ensure_bid_exists(bid_id)
    except FileNotFoundError as exc:
        raise BidContentServiceError(40401, f"标书不存在：{bid_id}", 404) from exc


def _word_count(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    return len(compact)


def _similarity(left: str, right: str) -> float:
    def normalize(value: str) -> str:
        return re.sub(r"[\s\W_]+", "", value or "").lower()

    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _deduplicate_generated_text(text: str) -> tuple[str, dict[str, Any]]:
    """Remove consecutive repeated paragraphs and near-identical sentences."""
    original = (text or "").strip()
    if not original:
        return "", {"removed_characters": 0, "repeat_ratio": 0.0}

    paragraphs = [item.strip() for item in re.split(r"\n+", original) if item.strip()]
    kept_paragraphs: list[str] = []
    kept_sentences_global: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        consecutive_repeat = bool(kept_paragraphs and _similarity(kept_paragraphs[-1], paragraph) >= 0.70)
        repeated_elsewhere = any(_similarity(previous, paragraph) >= 0.88 for previous in kept_paragraphs[-24:])
        if consecutive_repeat or repeated_elsewhere:
            removed += len(paragraph)
            continue

        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
        kept_sentences: list[str] = []
        for sentence in sentences:
            consecutive_sentence = bool(kept_sentences and len(sentence) >= 12 and _similarity(kept_sentences[-1], sentence) >= 0.78)
            repeated_sentence = len(sentence) >= 20 and any(
                _similarity(previous, sentence) >= 0.90 for previous in kept_sentences_global[-80:]
            )
            if consecutive_sentence or repeated_sentence:
                removed += len(sentence)
                continue
            kept_sentences.append(sentence)
            kept_sentences_global.append(sentence)
        cleaned = "".join(kept_sentences).strip()
        if cleaned:
            kept_paragraphs.append(cleaned)

    result = "\n\n".join(kept_paragraphs)
    ratio = removed / max(1, len(re.sub(r"\s+", "", original)))
    return result, {"removed_characters": removed, "repeat_ratio": round(ratio, 4)}


def _append_unique_chunk(buffer: str, chunk: str) -> tuple[str, str]:
    """Append only the non-overlapping portion of a streamed delta."""
    incoming = chunk or ""
    if not incoming:
        return buffer, ""
    max_overlap = min(len(buffer), len(incoming), 300)
    for size in range(max_overlap, 1, -1):
        if buffer.endswith(incoming[:size]):
            incoming = incoming[size:]
            break
    if len(incoming.strip()) >= 20:
        tail = buffer[-len(incoming) :]
        if tail and _similarity(tail, incoming) >= 0.80:
            return buffer, ""
    return buffer + incoming, incoming


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


def _load_biaoshu_industry_configs() -> dict[str, Any]:
    skill_root = Path(__file__).resolve().parents[3] / "BiaoShu-SKILL-main" / "BiaoShu-writer-pro"
    config_path = skill_root / "references" / "industry_configs.json"
    try:
        with config_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        industries = data.get("industries")
        if isinstance(industries, dict):
            return industries
    except Exception:
        pass
    return {
        "general": {
            "name": "通用行业",
            "keywords": [],
            "key_sections": {
                "技术方案": ["总体方案", "详细设计", "技术参数", "创新点"],
                "项目实施": ["实施计划", "人员配置", "进度安排", "风险控制"],
                "售后服务": ["服务体系", "响应时间", "培训计划", "备品备件"],
            },
        }
    }


def _biaoshu_industry_guidance(title: str, path: list[str], context: dict[str, Any]) -> dict[str, Any]:
    industries = _load_biaoshu_industry_configs()
    source = " ".join(
        [title, *path, context.get("project_overview", "")[:2000], context.get("score_requirements", "")[:2000]]
    )
    best_code = "general"
    best_score = 0
    for code, config in industries.items():
        keywords = config.get("keywords") or []
        score = sum(1 for keyword in keywords if keyword and keyword in source)
        if score > best_score:
            best_code = code
            best_score = score
    config = industries.get(best_code) or industries.get("general") or {}
    key_sections = config.get("key_sections") or {}
    matched_sections: list[str] = []
    for section_name, section_items in key_sections.items():
        if section_name in source or any(str(item) in source for item in section_items):
            matched_sections.extend(str(item) for item in section_items[:6])
    if not matched_sections:
        for section_items in list(key_sections.values())[:2]:
            matched_sections.extend(str(item) for item in section_items[:4])
    return {
        "industry_code": best_code,
        "industry_name": config.get("name") or "通用行业",
        "matched_sections": list(dict.fromkeys(matched_sections))[:8],
    }


def _biaoshu_paragraph_plan(title: str, path: list[str], children: list[str], context: dict[str, Any], target_words: int) -> str:
    guidance = _biaoshu_industry_guidance(title, path, context)
    if target_words <= 450:
        ratios = [0.34, 0.36, 0.30]
        topics = ["主题定位与响应边界", "关键措施与执行方式", "效果校验与交付价值"]
    elif target_words <= 800:
        ratios = [0.24, 0.28, 0.25, 0.23]
        topics = ["项目理解与章节定位", "关键措施与技术/服务路径", "过程控制与协同机制", "成果交付与评分响应"]
    else:
        ratios = [0.18, 0.22, 0.22, 0.20, 0.18]
        topics = ["项目理解与建设目标", "实施路径与核心措施", "资源配置与过程管理", "质量控制与风险响应", "成果交付与评分响应"]
    child_hint = "、".join(children[:5]) if children else "围绕本小节主题展开"
    industry_hint = "、".join(guidance["matched_sections"][:6]) or "按通用标书章节逻辑组织"
    lines = [
        f"BiaoShu-writer-pro写作策略：行业={guidance['industry_name']}；章节路径={' / '.join(path)}；子要点={child_hint}。",
        f"行业侧重点参考：{industry_hint}。",
        "段落预算如下，必须按完整段落自然收尾，不要用标题、编号或项目符号：",
    ]
    for index, (topic, ratio) in enumerate(zip(topics, ratios), 1):
        words = max(90, round(target_words * ratio))
        lines.append(f"第{index}段约{words}字：{topic}")
    return "\n".join(lines)


def _humanize_bid_text(text: str) -> str:
    # Adapted from BiaoShu-writer-pro/scripts/humanizer.py, kept local to avoid importing the CLI pipeline.
    replacements = {
        "赋能": "支持",
        "助力": "帮助",
        "驱动": "推动",
        "引领": "带动",
        "生态": "体系",
        "抓手": "手段",
        "颗粒度": "细致程度",
        "底层逻辑": "基本原理",
        "顶层设计": "整体规划",
    }
    filler_phrases = [
        "首先，",
        "其次，",
        "再次，",
        "最后，",
        "一方面，",
        "另一方面，",
        "不仅如此，",
        "更重要的是，",
        "值得注意的是，",
        "众所周知，",
        "显而易见，",
        "毋庸置疑，",
    ]
    result = text or ""
    for old, new in replacements.items():
        result = result.replace(old, new)
    paragraphs: list[str] = []
    for paragraph in [item.strip() for item in re.split(r"\n+", result) if item.strip()]:
        for phrase in filler_phrases:
            if paragraph.startswith(phrase):
                paragraph = paragraph[len(phrase):].strip()
                break
        sentences = paragraph.split("。")
        normalized_sentences: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > 110 and "，" in sentence:
                parts = [part.strip() for part in sentence.split("，") if part.strip()]
                mid = max(1, len(parts) // 2)
                normalized_sentences.append("，".join(parts[:mid]))
                normalized_sentences.append("，".join(parts[mid:]))
            else:
                normalized_sentences.append(sentence)
        paragraph = "。".join(normalized_sentences)
        if paragraph and paragraph[-1] not in "。！？!?；;":
            paragraph += "。"
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs).strip()


def _html_from_text(text: str) -> str:
    paragraphs = [item.strip() for item in re.split(r"\n+", text or "") if item.strip()]
    return "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)


def _plain_from_html(content_html: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", content_html or "", flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _content_context(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError as exc:
        raise BidContentServiceError(40401, f"标书不存在：{bid_id}", 404) from exc

    with storage.connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        parse = conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = ?", (bid_id,)).fetchone()
    if bid is None:
        raise BidContentServiceError(40401, f"标书不存在：{bid_id}", 404)
    if int(bid["current_step"]) < 3:
        raise BidContentServiceError(40001, "请先完成目录编写")
    if parse is None:
        raise BidContentServiceError(40001, "请先完成招标文件解读")

    raw_full_text = (parse["raw_full_text"] or "").strip()
    project_overview = (parse["parsed_project_overview"] or "").strip() or raw_full_text[:1600]
    score_requirements = (parse["parsed_score_requirements"] or "").strip() or raw_full_text[:1600]
    tech_scoring = _structured_text(parse["parsed_tech_scoring"] or "")
    biz_requirements = _structured_text(parse["parsed_biz_requirements"] or "")
    invalid_clauses = _structured_text(parse["parsed_invalid_clauses"] or "")
    ai_tech_scoring = _structured_text(parse["tech_scoring_items"] or "")
    ai_biz_scoring = _structured_text(parse["biz_scoring_items"] or "")
    qualification_requirements = _structured_text(parse["qualification_requirements"] or "")
    ai_invalid_clauses = _structured_text(parse["invalid_bid_clauses"] or "")
    bid_key_points = _structured_text(parse["bid_key_points"] or "")
    if tech_scoring and tech_scoring not in score_requirements:
        score_requirements = "\n".join(item for item in [score_requirements, tech_scoring] if item).strip()
    if ai_tech_scoring and ai_tech_scoring not in score_requirements:
        score_requirements = "\n".join(item for item in [score_requirements, ai_tech_scoring] if item).strip()
    biz_requirements = "\n".join(item for item in [biz_requirements, ai_biz_scoring, qualification_requirements] if item).strip()
    invalid_clauses = "\n".join(item for item in [invalid_clauses, ai_invalid_clauses, bid_key_points] if item).strip()
    return {
        "bid_id": bid_id,
        "project_id": project.id,
        "bid_title": project.settings.bidTitle or bid["bid_title"] or "投标文件",
        "page_count": int(project.settings.pageCount or bid["page_count"] or 200),
        "words_per_page": int(project.settings.wordsPerPage or bid["words_per_page"] or 800),
        "estimated_word_count": int(project.settings.estimatedWordCount or bid["estimated_word_count"] or 160000),
        "bid_type": project.settings.bidType,
        "selected_model": project.settings.selectedModel,
        "bid_category": parse["bid_category"],
        "project_overview": project_overview,
        "score_requirements": score_requirements,
        "biz_requirements": biz_requirements,
        "invalid_clauses": invalid_clauses,
    }


def _select_version(conn, bid_id: int, version_id: int | None = None):
    if version_id:
        row = conn.execute(
            "SELECT * FROM bid_catalog_versions WHERE bid_id = ? AND version_id = ?",
            (bid_id, version_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM bid_catalog_versions WHERE bid_id = ? ORDER BY is_final DESC, version_id ASC LIMIT 1",
            (bid_id,),
        ).fetchone()
    if row is None:
        raise BidContentServiceError(40001, "请先生成目录")
    return row


def _chapter_rows(conn, bid_id: int, version_id: int) -> list[Any]:
    return conn.execute(
        """
        SELECT * FROM bid_catalog_chapters
        WHERE bid_id = ? AND version_id = ?
        ORDER BY level ASC, parent_id ASC, sort_order ASC, chapter_id ASC
        """,
        (bid_id, version_id),
    ).fetchall()


def _ordered_leaf_rows(rows: list[Any]) -> list[Any]:
    rows_by_id = {int(row["chapter_id"]): row for row in rows}
    children: dict[int, list[Any]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(row)
    for child_rows in children.values():
        child_rows.sort(key=lambda item: (int(item["sort_order"]), int(item["chapter_id"])))

    leaves: list[Any] = []

    def walk(row: Any) -> None:
        chapter_id = int(row["chapter_id"])
        child_rows = children.get(chapter_id, [])
        if not child_rows:
            leaves.append(row)
            return
        for child in child_rows:
            walk(child)

    for root in children.get(0, []):
        walk(root)
    # Be defensive for any orphaned nodes caused by older edited catalogs.
    seen = {int(row["chapter_id"]) for row in leaves}
    for row in rows:
        chapter_id = int(row["chapter_id"])
        if chapter_id not in seen and chapter_id not in children:
            leaves.append(rows_by_id[chapter_id])
    return leaves


def _generatable_rows(conn, bid_id: int, version_id: int) -> list[Any]:
    return _ordered_leaf_rows(_chapter_rows(conn, bid_id, version_id))


def _has_children(conn, chapter_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM bid_catalog_chapters WHERE parent_id = ? LIMIT 1",
        (chapter_id,),
    ).fetchone()
    return row is not None


def _ensure_content_records(conn, bid_id: int, version_id: int) -> None:
    now = _now_text()
    for row in _generatable_rows(conn, bid_id, version_id):
        conn.execute(
            """
            INSERT OR IGNORE INTO bid_chapter_contents (
                bid_id, version_id, chapter_id, content_text, content_html, word_count, status, style_config, create_time, update_time
            ) VALUES (?, ?, ?, '', '', 0, 'unwritten', NULL, ?, ?)
            """,
            (bid_id, version_id, int(row["chapter_id"]), now, now),
        )


def _content_map(conn, bid_id: int, version_id: int) -> dict[int, Any]:
    rows = conn.execute(
        "SELECT * FROM bid_chapter_contents WHERE bid_id = ? AND version_id = ?",
        (bid_id, version_id),
    ).fetchall()
    return {int(row["chapter_id"]): row for row in rows}


def _normalize_heading_text(value: str) -> str:
    value = re.sub(r"^[#\s>*-]+", "", value or "").strip()
    value = re.sub(r"^(?:第?[一二三四五六七八九十百千万0-9]+[章节、.．\-\s]*)+", "", value)
    return re.sub(r"[\s\W_]+", "", value).lower()


def _split_text_by_child_titles(text: str, child_rows: list[Any]) -> dict[int, str]:
    child_titles = [(int(row["chapter_id"]), _normalize_heading_text(row["chapter_title"])) for row in child_rows]
    lines = [line.strip() for line in re.split(r"\r?\n", text or "")]
    segments: dict[int, list[str]] = {chapter_id: [] for chapter_id, _ in child_titles}
    intro: list[str] = []
    current_id: int | None = None
    matched_any = False
    for line in lines:
        if not line:
            if current_id:
                segments[current_id].append("")
            elif intro:
                intro.append("")
            continue
        normalized = _normalize_heading_text(line)
        matched_id = None
        for child_id, child_title in child_titles:
            if child_title and (normalized == child_title or child_title in normalized):
                matched_id = child_id
                break
        looks_like_heading = line.startswith("#") or len(re.sub(r"\s+", "", line)) <= 80
        if matched_id is not None and looks_like_heading:
            current_id = matched_id
            matched_any = True
            continue
        if current_id is not None:
            segments[current_id].append(line)
        else:
            intro.append(line)
    if not matched_any:
        return {}
    first_with_content = next((chapter_id for chapter_id, lines_ in segments.items() if any(item.strip() for item in lines_)), None)
    if intro and first_with_content is not None:
        segments[first_with_content] = intro + [""] + segments[first_with_content]
    return {
        chapter_id: re.sub(r"\n{3,}", "\n\n", "\n".join(lines_)).strip()
        for chapter_id, lines_ in segments.items()
        if any(item.strip() for item in lines_)
    }


def _migrate_parent_content_to_children(conn, bid_id: int, version_id: int) -> None:
    rows = _chapter_rows(conn, bid_id, version_id)
    children: dict[int, list[Any]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(row)
    contents = _content_map(conn, bid_id, version_id)
    for parent_id, child_rows in children.items():
        if not parent_id or not child_rows:
            continue
        parent_content = contents.get(parent_id)
        parent_text = (parent_content["content_text"] or "").strip() if parent_content else ""
        if not parent_text:
            continue
        split_segments = _split_text_by_child_titles(parent_text, child_rows)
        for child_id, segment in split_segments.items():
            child_content = contents.get(child_id)
            if child_content and (child_content["content_text"] or "").strip():
                continue
            child_row = next((row for row in child_rows if int(row["chapter_id"]) == child_id), None)
            if child_row is None:
                continue
            cleaned = _strip_generated_headings(segment, [child_row["chapter_title"]])
            if cleaned and chapter_collaboration.permissions(dict(child_row))["can_edit"]:
                _set_content_status(conn, bid_id, version_id, child_id, "completed", cleaned, _html_from_text(cleaned), {"migrated_from_parent_id": parent_id}, expected_revision=child_row["edit_revision"])


def _rows_to_tree(rows: list[Any], contents: dict[int, Any]) -> list[dict[str, Any]]:
    nodes: dict[int, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for row in rows:
        chapter_id = int(row["chapter_id"])
        content = contents.get(chapter_id)
        node = {
            "chapter_id": chapter_id,
            "version_id": int(row["version_id"]),
            "parent_id": int(row["parent_id"]),
            "title": row["chapter_title"],
            "level": int(row["level"]),
            "sort_order": int(row["sort_order"]),
            "children": [],
            "target_word_count": int(row["target_word_count"] or 800),
            "content_id": int(content["content_id"]) if content else None,
            "content_status": content["status"] if content else None,
            "word_count": int(content["word_count"] or 0) if content else 0,
        }
        nodes[chapter_id] = node
    for row in rows:
        node = nodes[int(row["chapter_id"])]
        parent_id = int(row["parent_id"])
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    def aggregate(node: dict[str, Any]) -> tuple[int, int, int]:
        children = node.get("children") or []
        if not children:
            completed = 1 if node.get("content_status") == "completed" else 0
            return completed, 1, int(node.get("word_count") or 0)
        completed_total = 0
        leaf_total = 0
        word_total = 0
        target_total = 0
        for child in children:
            child_completed, child_leaves, child_words = aggregate(child)
            completed_total += child_completed
            leaf_total += child_leaves
            word_total += child_words
            target_total += int(child.get("target_word_count") or 0)
        node["content_status"] = "completed" if leaf_total and completed_total == leaf_total else "unwritten"
        node["word_count"] = word_total
        if target_total:
            node["target_word_count"] = target_total
        return completed_total, leaf_total, word_total

    for root in roots:
        aggregate(root)
    return roots


def _stats(contents: dict[int, Any], leaf_ids: set[int]) -> dict[str, int]:
    items = [item for chapter_id, item in contents.items() if chapter_id in leaf_ids]
    total = len(items)
    completed = sum(1 for item in items if item["status"] == "completed")
    total_words = sum(int(item["word_count"] or 0) for item in items)
    progress = round(completed / total * 100) if total else 0
    return {
        "total_chapters": total,
        "completed_chapters": completed,
        "total_words": total_words,
        "progress": progress,
    }


def _leaf_nodes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for node in tree:
        if node.get("children"):
            leaves.extend(_leaf_nodes(node["children"]))
        else:
            leaves.append(node)
    return leaves


def get_content_info(bid_id: int, version_id: int | None = None) -> dict[str, Any]:
    context = _content_context(bid_id)
    with storage.connect() as conn:
        version = _select_version(conn, bid_id, version_id)
    bid_word_count_service.calculate_and_assign_word_count(bid_id, int(version["version_id"]))
    with storage.connect() as conn:
        version = _select_version(conn, bid_id, int(version["version_id"]))
        _ensure_content_records(conn, bid_id, int(version["version_id"]))
        _migrate_parent_content_to_children(conn, bid_id, int(version["version_id"]))
        conn.commit()
        rows = _chapter_rows(conn, bid_id, int(version["version_id"]))
        contents = _content_map(conn, bid_id, int(version["version_id"]))
    chapters = _rows_to_tree(rows, contents)
    leaves = _leaf_nodes(chapters)
    stats = _stats(contents, {int(node["chapter_id"]) for node in leaves})
    total_target_words = sum(int(node.get("target_word_count") or 800) for node in leaves)
    stats["total_target_words"] = total_target_words
    stats["word_progress"] = round(stats["total_words"] / total_target_words * 100) if total_target_words else 0
    return {
        "bid_id": bid_id,
        "version_id": int(version["version_id"]),
        "version_name": version["version_name"],
        "version_style": version["version_style"],
        "current_step": 4 if storage.ensure_bid_exists(bid_id)["current_step"] >= 4 else int(storage.ensure_bid_exists(bid_id)["current_step"]),
        "chapters": chapters,
        "stats": stats,
        "context": {
            "bid_title": context["bid_title"],
            "page_count": context["page_count"],
        },
    }


def complete_step3(bid_id: int) -> dict[str, Any]:
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        version = _select_version(conn, bid_id)
    storage.update_bid_current_step(bid_id, 4)
    return {"bid_id": bid_id, "current_step": 4, "version_id": int(version["version_id"])}


def _get_chapter(conn, bid_id: int, chapter_id: int, version_id: int | None = None):
    if version_id:
        row = conn.execute(
            "SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ? AND version_id = ?",
            (bid_id, chapter_id, version_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM bid_catalog_chapters WHERE bid_id = ? AND chapter_id = ?",
            (bid_id, chapter_id),
        ).fetchone()
    if row is None:
        raise BidContentServiceError(40401, "目录章节不存在", 404)
    return row


def _chapter_path(conn, row: Any) -> list[str]:
    path = [row["chapter_title"]]
    parent_id = int(row["parent_id"])
    while parent_id:
        parent = conn.execute("SELECT * FROM bid_catalog_chapters WHERE chapter_id = ?", (parent_id,)).fetchone()
        if parent is None:
            break
        path.insert(0, parent["chapter_title"])
        parent_id = int(parent["parent_id"])
    return path


def _chapter_children(conn, row: Any) -> list[str]:
    children = conn.execute(
        "SELECT chapter_title FROM bid_catalog_chapters WHERE parent_id = ? ORDER BY sort_order ASC, chapter_id ASC",
        (int(row["chapter_id"]),),
    ).fetchall()
    return [child["chapter_title"] for child in children]


def _target_words(conn, bid_id: int, version_id: int, chapter: Any, context: dict[str, Any]) -> int:
    explicit_target = int(chapter["target_word_count"] or 0)
    if explicit_target > 0:
        return explicit_target
    count = max(1, len(_generatable_rows(conn, bid_id, version_id)))
    total_words = int(context.get("page_count", 200)) * bid_word_count_service.WORDS_PER_PAGE
    return max(300, round(total_words / count))


def _fallback_content(title: str, path: list[str], children: list[str], context: dict[str, Any], target_words: int) -> str:
    points = "、".join(children[:5]) if children else "目标理解、实施措施、质量保障、风险控制"
    bid_title = context.get("bid_title") or "本项目"
    return (
        f"{title}\n\n"
        f"本节围绕{bid_title}的招标要求展开，重点响应“{points}”等内容。投标人将以项目目标为牵引，结合招标文件中关于技术方案、服务能力、组织保障和质量控制的评分要求，形成可执行、可检查、可追溯的实施安排。\n\n"
        "在具体实施过程中，首先建立统一的工作机制，明确项目负责人、技术负责人、质量负责人及现场执行人员的职责边界，确保需求确认、计划编制、过程执行、成果交付和问题闭环均有责任主体。其次，围绕招标人关注的关键指标设置阶段性控制点，通过例会沟通、过程记录、风险清单和质量复核等方式，保证各项工作按照既定标准推进。\n\n"
        "针对可能出现的进度偏差、资源不足、沟通不畅和交付质量波动等问题，项目团队将提前制定应急预案，建立分级响应机制，并通过动态跟踪持续修正实施策略。所有过程资料、交付成果和改进记录均纳入统一台账管理，确保后续评审、验收和运维交接具备充分依据。\n\n"
        f"通过上述安排，本节内容能够与目录路径“{' / '.join(path)}”保持一致，并有效支撑评分要求中关于方案完整性、实施可行性、团队保障和质量管理的评价重点。"
    )[: max(900, target_words * 2)]


def _strip_generated_headings(text: str, titles: list[str]) -> str:
    title_set = {re.sub(r"\s+", "", title or "") for title in titles if title}
    cleaned_lines: list[str] = []
    for line in re.split(r"\r?\n", text or ""):
        raw = line.strip()
        if not raw:
            cleaned_lines.append("")
            continue
        without_marks = re.sub(r"^[#\s>*-]+", "", raw).strip()
        normalized = re.sub(r"\s+", "", without_marks)
        normalized_without_number = re.sub(r"^(?:第?[一二三四五六七八九十百千万0-9]+[章节、.．\-\s]*)+", "", normalized)
        if raw.startswith("#") or normalized in title_set or normalized_without_number in title_set:
            continue
        cleaned_lines.append(raw)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()


def _finalize_sentence_endings(text: str) -> str:
    value = re.sub(r"[，、；：]\s*。", "。", (text or "").strip())
    value = re.sub(r"\s+([。！？；])", r"\1", value)
    paragraphs: list[str] = []
    sentence_end = "。！？!?；;"
    for paragraph in [item.strip() for item in re.split(r"\n+", value) if item.strip()]:
        if paragraph[-1] not in sentence_end:
            last_pos = max(paragraph.rfind(mark) for mark in sentence_end)
            if last_pos >= 0:
                paragraph = paragraph[: last_pos + 1].strip()
            else:
                continue
        if re.fullmatch(r"(?:其[一二三四五六七八九十]|第[一二三四五六七八九十]+|首先|其次|再次|最后)[，、；：。\s]*", paragraph):
            continue
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs).strip()


def _take_complete_sentences(text: str, word_limit: int) -> str:
    limit = max(0, int(word_limit))
    if limit <= 0:
        return ""
    kept: list[str] = []
    current_words = 0
    sentence_pattern = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")
    for paragraph in [item.strip() for item in re.split(r"\n+", text or "") if item.strip()]:
        paragraph_sentences: list[str] = []
        for match in sentence_pattern.finditer(paragraph):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            if sentence[-1] not in "。！？!?；;":
                continue
            sentence_words = _word_count(sentence)
            if sentence_words <= 0:
                continue
            if current_words + sentence_words > limit:
                break
            paragraph_sentences.append(sentence)
            current_words += sentence_words
        if paragraph_sentences:
            kept.append("".join(paragraph_sentences))
        if current_words >= limit:
            break
    return _finalize_sentence_endings("\n\n".join(kept))


def _trim_to_word_limit(text: str, word_limit: int) -> str:
    finalized = _finalize_sentence_endings(text)
    if _word_count(finalized) <= word_limit:
        return finalized
    sentence_trimmed = _take_complete_sentences(finalized, word_limit)
    if sentence_trimmed:
        return sentence_trimmed
    # Very long first sentences are rare, but keep the fallback grammatical.
    rough = finalized[: max(0, word_limit - 1)].strip()
    last_pos = max(rough.rfind(mark) for mark in "，、；：,; ")
    if last_pos > 80:
        rough = rough[:last_pos].strip()
    return _finalize_sentence_endings(f"{rough}。")


def _quality_word_bounds(target_words: int) -> tuple[int, int, int]:
    low_ratio = 0.70 if target_words < 500 else 0.75
    high_ratio = 1.35 if target_words < 500 else 1.25
    severe_ratio = 1.65 if target_words < 500 else 1.50
    return round(target_words * low_ratio), round(target_words * high_ratio), round(target_words * severe_ratio)


def _fit_content_to_target(text: str, title: str, path: list[str], children: list[str], context: dict[str, Any], target_words: int) -> tuple[str, dict[str, Any]]:
    low, high, severe_high = _quality_word_bounds(target_words)
    adjusted, initial_dedup = _deduplicate_generated_text(_finalize_sentence_endings(text))
    before = _word_count(adjusted)
    action = "none"
    if before > high:
        action = "over_quality_range_kept"
    elif before < low:
        # A short answer is visible to the reviewer.  Generic filler disguised
        # as case-specific work made the old output diverge from approved bids.
        action = "under_target_needs_review"
    adjusted, final_dedup = _deduplicate_generated_text(adjusted)
    adjusted = _finalize_sentence_endings(adjusted)
    after = _word_count(adjusted)
    return adjusted, {
        "target_word_count": target_words,
        "word_count_before_adjustment": before,
        "word_count_after_adjustment": after,
        "word_count_low": low,
        "word_count_high": high,
        "word_count_severe_high": severe_high,
        "adjustment_action": action,
        "word_count_abnormal": after < low or after > severe_high,
        "quality_first": True,
        "deduplication": {
            "removed_characters": initial_dedup["removed_characters"] + final_dedup["removed_characters"],
            "repeat_ratio": max(initial_dedup["repeat_ratio"], final_dedup["repeat_ratio"]),
        },
    }


async def _rewrite_repetitive_content(text: str, title: str, target_words: int) -> str:
    _low, high, _severe_high = _quality_word_bounds(target_words)
    messages = [
        {
            "role": "system",
            "content": "你是投标文件正文质量审校专家，只输出修改后的正文。",
        },
        {
            "role": "user",
            "content": (
                f"请对《{title}》正文做一次轻量去重改写。保留事实、措施和专业信息，删除重复段落与同义复述，"
                f"建议控制在{target_words}-{high}字之间，但不要为了字数牺牲内容完整性。每段只表达一个核心要点，从不同维度递进展开，并自然收尾。\n\n"
                f"待修改正文：\n{text[:12000]}"
            ),
        },
    ]
    chunks: list[str] = []
    max_tokens = max(700, min(2600, round(high * 1.2)))
    async for chunk in stream_chat_completion(messages, scene="bid_content", temperature=0.2, max_tokens=max_tokens, strict=True):
        chunks.append(chunk)
    return "".join(chunks).strip() or text


async def _rewrite_to_quality_range_content(text: str, title: str, target_words: int) -> str:
    low, high, _severe_high = _quality_word_bounds(target_words)
    messages = [
        {
            "role": "system",
            "content": "你是投标文件正文质量审校专家。只输出修改后的正文，不输出标题、编号、解释或Markdown。",
        },
        {
            "role": "user",
            "content": (
                f"请将《{title}》正文优化为一篇完整自然的标书正文。\n"
                f"建议字数区间：{low}-{high}字。可以适度超出，但必须保留项目事实、关键措施、评分响应和专业表达；"
                "只删除重复表述、空泛套话和无关铺垫，不要删掉有效论证；最后一句必须完整自然。\n\n"
                f"原正文：\n{text[:12000]}"
            ),
        },
    ]
    chunks: list[str] = []
    max_tokens = max(700, min(2600, round(high * 1.2)))
    async for chunk in stream_chat_completion(messages, scene="bid_content", temperature=0.18, max_tokens=max_tokens, strict=True):
        chunks.append(chunk)
    return "".join(chunks).strip() or text


def _sync_project_section(bid_id: int, chapter_id: int, title: str, content_text: str, word_count: int) -> None:
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError:
        return
    section_id = str(chapter_id)
    project.sections[section_id] = {
        "sectionId": section_id,
        "title": title,
        "subItems": [],
        "content": content_text,
        "wordCount": word_count,
        "wordTarget": max(800, word_count),
        "status": "done",
        "llm": {"provider": "qwen", "model": selected_model("bid_content"), "usedApi": True, "error": None},
    }
    for row in project.directory:
        if str(row.get("id")) == section_id:
            row["status"] = "done"
            row["wordsDone"] = word_count
    storage.save_project(project)


def _set_content_status(conn, bid_id: int, version_id: int, chapter_id: int, status: str, text: str = "", content_html: str = "", style_config: dict[str, Any] | None = None, *, expected_revision: int | None = None) -> int:
    with chapter_collaboration.change(conn, bid_id, chapter_id, expected_revision, "content_" + status) as previous:
        _write_content_status(conn, bid_id, version_id, chapter_id, status, text, content_html, style_config)
    return previous["edit_revision"] + 1


def _write_content_status(conn, bid_id, version_id, chapter_id, status, text, content_html, style_config):
    now = _now_text()
    word_count = _word_count(text)
    conn.execute(
        """
        INSERT INTO bid_chapter_contents (
            bid_id, version_id, chapter_id, content_text, content_html, word_count, status, style_config, create_time, update_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bid_id, version_id, chapter_id) DO UPDATE SET
            content_text = excluded.content_text,
            content_html = excluded.content_html,
            word_count = excluded.word_count,
            status = excluded.status,
            style_config = excluded.style_config,
            update_time = excluded.update_time
        """,
        (bid_id, version_id, chapter_id, text, content_html, word_count, status, json.dumps(style_config or {}, ensure_ascii=False), now, now),
    )


async def generate_chapter_events(
    bid_id: int,
    chapter_id: int,
    *,
    version_id: int | None = None,
    style: str | None = None,
    rewrite: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    chunk_id = 0
    active_version_id: int | None = None
    completed = False
    interrupted = False
    generation_key: tuple[str, int, int] | None = None
    generation_owner = object()
    generation_started = False
    generation_revision = None
    try:
        generation_revision = chapter_collaboration.start_revision(bid_id, chapter_id)
        generation_key = _claim_content_generation(bid_id, chapter_id, generation_owner)
        context = _content_context(bid_id)
        with storage.connect() as conn:
            chapter = _get_chapter(conn, bid_id, chapter_id, version_id)
            if _has_children(conn, int(chapter["chapter_id"])):
                raise BidContentServiceError(40001, "请选择最末级章节生成正文，父级目录只用于标题汇总")
            version = _select_version(conn, bid_id, int(chapter["version_id"]))
            active_version_id = int(version["version_id"])
            content = conn.execute(
                "SELECT * FROM bid_chapter_contents WHERE bid_id = ? AND version_id = ? AND chapter_id = ?",
                (bid_id, int(version["version_id"]), chapter_id),
            ).fetchone()
            if content and content["status"] == "generating" and not rewrite:
                buffer = (content["content_text"] or "").strip()
                try:
                    previous_style = json.loads(content["style_config"] or "{}")
                except json.JSONDecodeError:
                    previous_style = {}
                chunk_id = int(previous_style.get("last_chunk_id") or 0)
            path = _chapter_path(conn, chapter)
            children = _chapter_children(conn, chapter)
            target_words = _target_words(conn, bid_id, int(version["version_id"]), chapter, context)
            relevant_scores = bid_requirement_context.select_requirements(
                context["score_requirements"], " / ".join(path), max_chars=3600,
            )
            low_words, high_words, severe_high_words = _quality_word_bounds(target_words)
            writer_plan = _biaoshu_paragraph_plan(chapter["chapter_title"], path, children, context, target_words)
            reference_context = bid_reference_service.retrieve_reference_context(
                bid_id,
                f"章节：{' '.join([*path, *children])}\n评分要求：{context['score_requirements'][:1200]}",
                context["bid_type"],
            )
            reference_prompt = bid_reference_service.format_reference_prompt(reference_context, context["bid_type"])
            case_context = await asyncio.to_thread(
                bid_case_reference_service.retrieve_case_context,
                bid_id,
                f"章节：{' / '.join(path)}；评分要求：{relevant_scores[:1000]}",
            )
            case_prompt = bid_case_reference_service.format_case_prompt(case_context)
            try:
                knowledge_evidence = await asyncio.to_thread(
                    bid_case_reference_service.retrieve_knowledge_evidence,
                    f"章节：{' / '.join(path)}；评分要求：{relevant_scores[:1000]}",
                )
            except Exception as exc:
                knowledge_evidence = {"excerpts": [], "error": str(exc)}
            knowledge_prompt = bid_case_reference_service.format_knowledge_evidence(knowledge_evidence)
            try:
                rag_context = rag_case_service.retrieve_scoring_context(
                    bid_id,
                    f"章节：{' '.join([*path, *children])}\n评分要求：{context['score_requirements'][:1600]}",
                    context["bid_type"],
                )
                rag_prompt = rag_case_service.format_scoring_context_prompt(rag_context, context["bid_type"])
            except Exception:
                rag_prompt = ""
            if rewrite:
                buffer = ""
                chunk_id = 0
            generation_revision = _set_content_status(
                conn,
                bid_id,
                int(version["version_id"]),
                chapter_id,
                "generating",
                buffer,
                _html_from_text(buffer),
                {"last_chunk_id": chunk_id, "resumed": bool(buffer)}, expected_revision=generation_revision,
            )
            conn.commit()
            generation_started = True

        yield {
            "type": "start",
            "chapter_id": chapter_id,
            "title": chapter["chapter_title"],
            "target_words": target_words,
            "content_so_far": buffer,
            "last_chunk_id": chunk_id,
        }

        resume_instruction = (
            f"\n已生成内容如下，请只从末尾继续，不得重新输出已有内容：\n{buffer[-3000:]}"
            if buffer
            else ""
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是资深投标文件正文写作专家。输出正式标书正文，不要解释，不要使用 Markdown 表格。"
                    "禁止重复表述相同内容，禁止整段改写复述前文，每个段落只阐述一个核心要点，逻辑层层递进。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"章节标题与路径：{' / '.join(path)}\n"
                    f"核心写作要点：{'；'.join(children[:8]) if children else chapter['chapter_title']}\n"
                    f"目标字数：约{target_words}字，建议控制在{low_words}-{high_words}字。允许适度超出，优先保证内容完整、论证充分和自然收尾。"
                    "禁止通过重复语句、无意义复述凑字数，优先保证内容质量和逻辑连贯。\n"
                    "不要输出章节编号、章节标题或 Markdown 标题符号，直接输出本小节正文内容。\n"
                    f"{('请以指定案例的相应章节组织和论证深度为写作结构，优先逐项响应当前评分点。' if case_context and case_context['excerpts'] else writer_plan)}\n"
                    f"正文风格：{style or '专业、稳健、政务标书表达'}\n"
                    f"项目核心信息：{context['project_overview'][:1600]}\n"
                    f"本章节相关评分点：{relevant_scores}\n"
                    f"{reference_prompt}\n"
                    f"{rag_prompt}\n"
                    f"{case_prompt}\n"
                    f"{knowledge_prompt}\n"
                    "只展开当前章节和评分点确有依据的维度；缺少企业事实时保留待核实，不要编造或以通用管理套话凑字数。"
                    f"{resume_instruction}"
                ),
            },
        ]
        max_tokens = max(900, min(6000, round(high_words * 1.6)))
        last_stream_save_len = len(buffer)
        last_stream_save_time = time.monotonic()
        async with aclosing(stream_chat_completion(messages, scene="bid_content", temperature=0.35, max_tokens=max_tokens, strict=True)) as tokens:
            async for chunk in tokens:
                if not chunk:
                    continue
                buffer += chunk
                chunk_id += 1
                yield {"type": "chunk", "chapter_id": chapter_id, "chunk_id": chunk_id, "content": chunk}
                should_save_stream_state = (
                    len(buffer) - last_stream_save_len >= STREAM_DB_SAVE_MIN_CHARS
                    or time.monotonic() - last_stream_save_time >= STREAM_DB_SAVE_MIN_SECONDS
                )
                if should_save_stream_state:
                    with storage.connect() as conn:
                        generation_revision = _set_content_status(
                            conn,
                            bid_id,
                            int(chapter["version_id"]),
                            chapter_id,
                            "generating",
                            buffer,
                            _html_from_text(buffer),
                            {"last_chunk_id": chunk_id, "resumed": bool(resume_instruction)}, expected_revision=generation_revision,
                        )
                        conn.commit()
                    last_stream_save_len = len(buffer)
                    last_stream_save_time = time.monotonic()

        if not buffer.strip():
            raise BidContentServiceError(50001, "模型未返回正文，本章未完成")
        final_text = _strip_generated_headings(buffer.strip(), [chapter["chapter_title"], *path, *children])
        final_text = _humanize_bid_text(final_text)
        if _word_count(final_text) > severe_high_words:
            yield {"type": "adjusting", "chapter_id": chapter_id, "message": "正在校订篇幅"}
            final_text = await _rewrite_to_quality_range_content(final_text, chapter["chapter_title"], target_words)
            final_text = _strip_generated_headings(final_text, [chapter["chapter_title"], *path, *children])
            final_text = _humanize_bid_text(final_text)
        final_text, word_control = _fit_content_to_target(final_text, chapter["chapter_title"], path, children, context, target_words)
        if word_control["deduplication"]["repeat_ratio"] >= 0.15:
            yield {"type": "adjusting", "chapter_id": chapter_id, "message": "正在整理重复内容"}
            rewritten = await _rewrite_repetitive_content(final_text, chapter["chapter_title"], target_words)
            rewritten = _strip_generated_headings(rewritten, [chapter["chapter_title"], *path, *children])
            rewritten = _humanize_bid_text(rewritten)
            final_text, rewritten_control = _fit_content_to_target(rewritten, chapter["chapter_title"], path, children, context, target_words)
            rewritten_control["quality_rewrite_triggered"] = True
            rewritten_control["writer_strategy"] = "biaoshu_paragraph_plan_humanizer"
            word_control = rewritten_control
        else:
            word_control["quality_rewrite_triggered"] = False
        word_control["writer_strategy"] = "approved_case" if case_context and case_context["excerpts"] else "biaoshu_paragraph_plan_humanizer"
        word_control["case_project_id"] = case_context["project_id"] if case_context else None
        word_control["knowledge_chunk_ids"] = [item["chunk_id"] for item in knowledge_evidence["excerpts"]]
        word_control["case_chunk_ids"] = [item["chunk_id"] for item in case_context["excerpts"]] if case_context else []
        final_text = bid_draft_guard.mark_unverified_facts(final_text, " / ".join(path))
        final_html = _html_from_text(final_text)
        with storage.connect() as conn:
            generation_revision = _set_content_status(conn, bid_id, int(chapter["version_id"]), chapter_id, "completed", final_text, final_html, word_control, expected_revision=generation_revision)
            conn.commit()
        _sync_project_section(bid_id, chapter_id, chapter["chapter_title"], final_text, _word_count(final_text))
        completed = True
        yield {"type": "done", "chapter_id": chapter_id, "chunk_id": chunk_id, "content_text": final_text, "word_count": _word_count(final_text), "target_word_count": target_words, "word_control": word_control}
    except (asyncio.CancelledError, GeneratorExit):
        interrupted = True
        raise
    except BidContentServiceError as exc:
        yield {"type": "error", "chapter_id": chapter_id, "message": exc.message, "code": exc.code}
    except Exception as exc:  # noqa: BLE001 - surface controlled error to SSE client.
        yield {"type": "error", "chapter_id": chapter_id, "message": str(exc), "code": 50001}
    finally:
        try:
            if generation_started and active_version_id is not None and not completed:
                with storage.connect() as conn:
                    _set_content_status(conn, bid_id, active_version_id, chapter_id, "failed", buffer, _html_from_text(buffer), {"interrupted": interrupted, "last_chunk_id": chunk_id}, expected_revision=generation_revision)
                    conn.commit()
        except Exception:
            pass
        finally:
            # Keep ownership through the final write so a canceled old request
            # cannot replace a newer generation's completed content.
            _release_content_generation(generation_key, generation_owner)


async def generate_all_events(bid_id: int, *, version_id: int | None = None, style: str | None = None) -> AsyncIterator[dict[str, Any]]:
    try:
        _content_context(bid_id)
        with storage.connect() as conn:
            version = _select_version(conn, bid_id, version_id)
            _ensure_content_records(conn, bid_id, int(version["version_id"]))
            candidate_rows = _generatable_rows(conn, bid_id, int(version["version_id"]))
            pending_ids = {
                int(row["chapter_id"])
                for row in conn.execute(
                    """
                    SELECT c.chapter_id FROM bid_catalog_chapters c
                    JOIN bid_chapter_contents bc
                      ON bc.bid_id = c.bid_id AND bc.version_id = c.version_id AND bc.chapter_id = c.chapter_id
                    WHERE c.bid_id = ? AND c.version_id = ?
                      AND (bc.status != 'completed' OR COALESCE(bc.content_text, '') = '')
                    """,
                    (bid_id, int(version["version_id"])),
                ).fetchall()
            }
            rows = [row for row in candidate_rows if int(row["chapter_id"]) in pending_ids]
            conn.commit()
        yield {"type": "all_start", "total": len(rows)}
        success_count = 0
        failure_count = 0
        for index, row in enumerate(rows, 1):
            yield {"type": "chapter_start", "index": index, "total": len(rows), "chapter_id": int(row["chapter_id"]), "title": row["chapter_title"]}
            async with aclosing(generate_chapter_events(bid_id, int(row["chapter_id"]), version_id=int(row["version_id"]), style=style)) as events:
                async for event in events:
                    if event["type"] == "done":
                        success_count += 1
                    elif event["type"] == "error":
                        failure_count += 1
                    yield event
        yield {"type": "all_done", "success_count": success_count, "failure_count": failure_count}
    except BidContentServiceError as exc:
        yield {"type": "error", "message": exc.message, "code": exc.code}


def get_chapter_content(bid_id: int, chapter_id: int, version_id: int | None = None) -> dict[str, Any]:
    _ensure_bid(bid_id)
    with storage.connect() as conn:
        chapter = _get_chapter(conn, bid_id, chapter_id, version_id)
        _ensure_content_records(conn, bid_id, int(chapter["version_id"]))
        _migrate_parent_content_to_children(conn, bid_id, int(chapter["version_id"]))
        conn.commit()
        content = conn.execute(
            "SELECT * FROM bid_chapter_contents WHERE bid_id = ? AND version_id = ? AND chapter_id = ?",
            (bid_id, int(chapter["version_id"]), chapter_id),
        ).fetchone()
    return {
        "bid_id": bid_id,
        "version_id": int(chapter["version_id"]),
        "chapter_id": chapter_id,
        "revision": chapter["edit_revision"],
        "chapter_title": chapter["chapter_title"],
        "target_word_count": int(chapter["target_word_count"] or 800),
        "content_text": content["content_text"] if content else "",
        "content_html": content["content_html"] if content else "",
        "word_count": int(content["word_count"] or 0) if content else 0,
        "status": content["status"] if content else "unwritten",
        "style_config": json.loads(content["style_config"] or "{}") if content and content["style_config"] else {},
    }


def save_chapter_content(bid_id: int, chapter_id: int, payload: ChapterContentSaveRequest, version_id: int | None = None) -> dict[str, Any]:
    _ensure_bid(bid_id)
    text = (payload.content_text or "").strip() or _plain_from_html(payload.content_html)
    content_html = payload.content_html or _html_from_text(text)
    with storage.connect() as conn:
        chapter = _get_chapter(conn, bid_id, chapter_id, version_id)
        status = "completed" if text else "unwritten"
        saved_revision = _set_content_status(conn, bid_id, int(chapter["version_id"]), chapter_id, status, text, content_html, expected_revision=payload.expected_revision)
        conn.commit()
    if text:
        _sync_project_section(bid_id, chapter_id, chapter["chapter_title"], text, _word_count(text))
    result = get_chapter_content(bid_id, chapter_id, int(chapter["version_id"]))
    return {**result, "revision": saved_revision, "content_text": text, "content_html": content_html, "word_count": _word_count(text), "status": status}


def _add_toc(document: Document) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "separate")
    fld_char3 = OxmlElement("w:fldChar")
    fld_char3.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char)
    run._r.append(instr)
    run._r.append(fld_char2)
    run._r.append(fld_char3)


DEFAULT_DOCX_FORMAT_OPTIONS: dict[str, str] = {
    "title_color": "3A3A32",
    "title_size": "20pt",
    "title_font": "宋体",
    "heading1_color": "3A3A32",
    "heading1_size": "16pt",
    "heading1_font": "宋体",
    "heading2_color": "3A3A32",
    "heading2_size": "14pt",
    "heading2_font": "宋体",
    "heading3_color": "3A3A32",
    "heading3_size": "13pt",
    "heading3_font": "宋体",
    "body_font": "宋体",
    "body_size": "12pt",
}


def _clean_hex_color(value: str | None, fallback: str) -> str:
    candidate = re.sub(r"[^0-9A-Fa-f]", "", value or "")[:6]
    return candidate.upper() if len(candidate) == 6 else fallback


def _clean_pt_size(value: str | None, fallback: str) -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    if raw.endswith("pt"):
        number = raw[:-2]
    else:
        number = raw
    try:
        parsed = float(number)
    except ValueError:
        return fallback
    if parsed < 6 or parsed > 72:
        return fallback
    return f"{parsed:g}pt"


def _normalize_docx_format_options(options: dict[str, Any] | None = None) -> dict[str, str]:
    normalized = dict(DEFAULT_DOCX_FORMAT_OPTIONS)
    source = options or {}
    for key in ("title_font", "heading1_font", "heading2_font", "heading3_font", "body_font"):
        value = str(source.get(key) or "").strip()
        if value:
            normalized[key] = value[:30]
    for key in ("title_color", "heading1_color", "heading2_color", "heading3_color"):
        normalized[key] = _clean_hex_color(str(source.get(key) or ""), normalized[key])
    for key in ("title_size", "heading1_size", "heading2_size", "heading3_size", "body_size"):
        normalized[key] = _clean_pt_size(str(source.get(key) or ""), normalized[key])
    return normalized


def _officecli_docx_heading_styles(options: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"selector": "paragraph[style=Title]", "props": {"color": options["title_color"], "bold": "true", "size": options["title_size"], "font": options["title_font"]}},
        {"selector": "paragraph[style=Heading1]", "props": {"color": options["heading1_color"], "bold": "true", "size": options["heading1_size"], "font": options["heading1_font"]}},
        {"selector": "paragraph[style=Heading2]", "props": {"color": options["heading2_color"], "bold": "true", "size": options["heading2_size"], "font": options["heading2_font"]}},
        {"selector": "paragraph[style=Heading3]", "props": {"color": options["heading3_color"], "bold": "true", "size": options["heading3_size"], "font": options["heading3_font"]}},
    ]


def _apply_doc_styles(doc: Document, format_options: dict[str, Any] | None = None) -> None:
    options = _normalize_docx_format_options(format_options)
    section = doc.sections[0]
    section.top_margin = Inches(0.9)
    section.bottom_margin = Inches(0.9)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    normal = doc.styles["Normal"]
    normal.font.name = options["body_font"]
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), options["body_font"])
    normal.font.size = Pt(float(options["body_size"].removesuffix("pt")))
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    normal.paragraph_format.first_line_indent = Pt(24)


def _resolve_knowledge_file_path(raw_path: str) -> Path:
    candidate = Path(raw_path or "")
    return candidate if candidate.is_absolute() else (settings.backend_root / candidate).resolve()


def _knowledge_image_item(item_id: int) -> dict[str, Any] | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT item_id, title, source_name, file_path, status
            FROM knowledge_items
            WHERE item_id = ?
            """,
            (item_id,),
        ).fetchone()
    if row is None:
        return None
    image_path = _resolve_knowledge_file_path(str(row["file_path"] or ""))
    if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not image_path.exists():
        return None
    return {
        "item_id": int(row["item_id"]),
        "title": row["title"] or row["source_name"] or f"知识库图片 {item_id}",
        "source_name": row["source_name"] or image_path.name,
        "status": row["status"],
        "path": image_path,
    }


def _picture_source_for_docx(path: Path) -> str | BytesIO:
    if path.suffix.lower() != ".webp":
        return str(path)
    try:
        from PIL import Image

        stream = BytesIO()
        with Image.open(path) as image:
            image.convert("RGB").save(stream, format="PNG")
        stream.seek(0)
        return stream
    except Exception:
        return str(path)


def _add_body_paragraph(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text)
    p.paragraph_format.first_line_indent = Pt(24)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE


def _add_knowledge_image_block(doc: Document, item_id: int) -> None:
    item = _knowledge_image_item(item_id)
    if item is None:
        _add_body_paragraph(doc, f"（知识库图片条目 {item_id} 未找到或文件不可用，请人工检查素材。）")
        return
    try:
        image_paragraph = doc.add_paragraph()
        image_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        image_paragraph.add_run().add_picture(_picture_source_for_docx(item["path"]), width=Inches(5.5))

        status_note = "｜状态：已过期" if item["status"] == "expired" else ""
        caption = doc.add_paragraph(f"图：{item['title']}（来源：{item['source_name']}｜条目ID：{item_id}{status_note}）")
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.paragraph_format.first_line_indent = Pt(0)
    except Exception as exc:
        _add_body_paragraph(doc, f"（图片插入失败：{item['source_name']}，请人工检查。原因：{exc}）")


def _add_content_blocks(doc: Document, text: str) -> None:
    for para in [item.strip() for item in re.split(r"\n+", text or "") if item.strip()]:
        marker = KNOWLEDGE_IMAGE_MARKER_RE.match(para)
        if marker:
            _add_knowledge_image_block(doc, int(marker.group(1)))
            continue
        _add_body_paragraph(doc, para)


def content_docx_bytes(bid_id: int, version_id: int | None = None, format_options: dict[str, Any] | None = None) -> tuple[bytes, str]:
    context = _content_context(bid_id)
    docx_format = _normalize_docx_format_options(format_options)
    with storage.connect() as conn:
        version = _select_version(conn, bid_id, version_id)
        _ensure_content_records(conn, bid_id, int(version["version_id"]))
        _migrate_parent_content_to_children(conn, bid_id, int(version["version_id"]))
        conn.commit()
        rows = _chapter_rows(conn, bid_id, int(version["version_id"]))
        contents = _content_map(conn, bid_id, int(version["version_id"]))
        tree = _rows_to_tree(rows, contents)

    doc = Document()
    _apply_doc_styles(doc, docx_format)
    doc.add_heading(context["bid_title"], level=0)
    doc.add_paragraph(f"目录版本：{version['version_name']}｜生成风格：{version['version_style']}")
    doc.add_page_break()
    doc.add_heading("目录", level=1)
    _add_toc(doc)
    doc.add_page_break()

    def add_node(node: dict[str, Any]) -> None:
        doc.add_heading(node["title"], level=min(int(node["level"]), 3))
        content = contents.get(int(node["chapter_id"]))
        is_leaf = not node.get("children")
        text = _strip_generated_headings((content["content_text"] or "").strip(), [node["title"]]) if content and is_leaf else ""
        if is_leaf and text:
            _add_content_blocks(doc, text)
        elif is_leaf:
            doc.add_paragraph("（本章节待生成）")
        for child in node.get("children", []):
            add_node(child)

    for root in tree:
        add_node(root)

    stream = BytesIO()
    doc.save(stream)
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", context["bid_title"] or "投标文件")
    return enhance_docx_with_officecli(
        stream.getvalue(),
        heading_styles=_officecli_docx_heading_styles(docx_format),
        body_font=docx_format["body_font"],
        body_size=docx_format["body_size"],
    ), f"{safe_name}.docx"


def chapter_docx_bytes(bid_id: int, chapter_id: int, version_id: int | None = None, format_options: dict[str, Any] | None = None) -> tuple[bytes, str]:
    content = get_chapter_content(bid_id, chapter_id, version_id)
    docx_format = _normalize_docx_format_options(format_options)
    doc = Document()
    _apply_doc_styles(doc, docx_format)
    doc.add_heading(content["chapter_title"], level=1)
    text = _strip_generated_headings((content["content_text"] or "").strip(), [content["chapter_title"]])
    if text:
        _add_content_blocks(doc, text)
    else:
        doc.add_paragraph("（本章节待生成）")
    stream = BytesIO()
    doc.save(stream)
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", content["chapter_title"] or "章节正文")
    return enhance_docx_with_officecli(
        stream.getvalue(),
        heading_styles=_officecli_docx_heading_styles(docx_format),
        body_font=docx_format["body_font"],
        body_size=docx_format["body_size"],
    ), f"{safe_name}.docx"
