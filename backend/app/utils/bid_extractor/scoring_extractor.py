from __future__ import annotations

import json
import re
from typing import Any


class ScoringExtractor:
    """BiaoShu-SKILL scoring extractor adapted for service-layer use.

    The upstream script is a CLI tool. This version keeps its keyword/regex
    extraction model, removes console printing and returns extra flattened fields
    used by the current bid workflow.
    """

    SCORING_KEYWORDS = [
        "评标办法",
        "评分办法",
        "评分标准",
        "评分细则",
        "评标标准",
        "评标细则",
        "综合评分",
        "技术评分",
        "商务评分",
        "价格评分",
        "评分因素",
        "评分项目",
        "评审因素",
        "评审项目",
        "评审标准",
        "详细评审",
        "技术部分",
        "商务部分",
        "分值",
    ]

    SCORING_METHODS = {
        "客观评分": ["客观", "量化", "硬性指标", "满足即得分"],
        "主观评分": ["主观", "综合评价", "评审委员会", "酌情"],
        "价格评分": ["价格", "报价", "低价优先", "基准价"],
    }

    ITEM_PATTERNS = [
        r"(\d+)[\.\s、]+([^\d\n]+?)[\.\s、]+(\d+(?:\.\d+)?)[\s]*分",
        r"([一二三四五六七八九十])[\.\s、]+([^\d\n]+?)[\.\s、]+(\d+(?:\.\d+)?)[\s]*分",
        r"([\(（]\d+[\)）])\s*([^\d\n]+?)[\.\s、]+(\d+(?:\.\d+)?)[\s]*分",
        r"([^\n]{2,80}?)(\d+(?:\.\d+)?)[\s]*分",
    ]

    TECH_KEYWORDS = ["技术", "方案", "实施", "服务", "质量", "进度", "安全", "运维", "施工组织", "响应"]
    BIZ_KEYWORDS = ["商务", "资质", "资格", "业绩", "人员", "财务", "信誉", "证书", "报价", "价格"]

    def __init__(self, text: str):
        self.text = _normalize_text(text)
        self.scoring_section = ""
        self.categories: list[dict[str, Any]] = []
        self.document_info: dict[str, Any] = {}

    def find_scoring_section(self) -> tuple[int, int, str]:
        # Prefer actual chapter headings over TOC entries and incidental references.
        lines = self.text.split("\n")
        candidates = []
        for idx, line in enumerate(lines):
            if not any(k in line for k in self.SCORING_KEYWORDS):
                continue
            if re.search(r"\.{3,}|…{2,}", line):
                continue
            heading = re.sub(r"^#+\s*", "", line)
            weight = 3 if re.match(r"第[一二三四五六七八九十\d]+章", heading) else 0
            weight += 2 if len(line) < 40 else 0
            weight += sum(1 for x in lines[idx:idx + 80] if "得分" in x or "得" in x and _extract_score(x) is not None)
            candidates.append((weight, idx))
        if not candidates:
            return 0, 0, ""
        _, start = max(candidates)
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if re.match(r"^(?:#+\s*)?第[一二三四五六七八九十\d]+章", lines[idx]):
                end = idx
                break
        return start, end, "\n".join(lines[start:end])

    def extract_document_info(self) -> dict[str, Any]:
        self.document_info = {
            "project_name": self._extract_project_name(),
            "bid_deadline": self._extract_bid_deadline(),
            "total_budget": self._extract_budget(),
        }
        return self.document_info

    def _extract_project_name(self) -> str:
        patterns = [
            r"项目名称[：:]\s*([^\n]+)",
            r"招标项目[：:]\s*([^\n]+)",
            r"(.{3,50}项目).{0,10}招标",
        ]
        return _first_match(self.text, patterns, "未知项目")

    def _extract_bid_deadline(self) -> str:
        patterns = [
            r"投标截止[时间]?[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)",
            r"截止[时间]?[：:]\s*(\d{4}[\.\-/]\d{1,2}[\.\-/]\d{1,2})",
            r"(\d{4}年\d{1,2}月\d{1,2}日).{0,10}开标",
        ]
        return _first_match(self.text, patterns, "未知")

    def _extract_budget(self) -> str:
        patterns = [
            r"预算[金额]?[：:]\s*([\d,\.]+\s*[万元元]+)",
            r"最高限价[：:]\s*([\d,\.]+\s*[万元元]+)",
            r"采购预算[：:]\s*([\d,\.]+)",
            r"([\d,\.]+)\s*万元",
        ]
        return _first_match(self.text, patterns, "未知")

    def extract_categories(self) -> None:
        category_patterns = [
            r"([技术商务价格]+)[部类]?[分]?[^\d]*(\d+(?:\.\d+)?)[\s]*分",
            r"([技术商务价格]+)评分[^\d]*(\d+(?:\.\d+)?)[\s]*分",
        ]
        total_score = 0.0
        seen: set[tuple[str, float]] = set()

        for line in self.scoring_section.split("\n"):
            for pattern in category_patterns:
                match = re.search(pattern, line.strip())
                if not match:
                    continue
                name = match.group(1)
                score = float(match.group(2))
                key = (name, score)
                if key in seen:
                    break
                seen.add(key)
                total_score += score
                self.categories.append({"name": name, "score": score, "weight": 0, "items": []})
                break

        if total_score > 0:
            for category in self.categories:
                category["weight"] = round(float(category["score"]) / total_score, 2)

    def extract_scoring_items(self) -> None:
        if not self.categories:
            self._extract_items_without_categories()
            return

        current_category_idx = -1
        item_counter = 0
        for line in self.scoring_section.split("\n"):
            line = line.strip()
            if not line:
                continue
            for idx, category in enumerate(self.categories):
                if category["name"] in line and str(int(float(category["score"]))) in line:
                    current_category_idx = idx
                    break
            if current_category_idx < 0:
                continue
            item = self._parse_scoring_item(line, item_counter)
            if item:
                self.categories[current_category_idx]["items"].append(item)
                item_counter += 1

    def _parse_scoring_item(self, line: str, counter: int) -> dict[str, Any] | None:
        if "分" not in line and "评分" not in line and "评审" not in line:
            return None
        if _extract_score(line) is None or _is_markdown_table_row(line):
            return None
        for pattern in self.ITEM_PATTERNS:
            match = re.search(pattern.replace("分", r"分(?!钟|鐘|秒)"), line)
            if not match:
                continue
            if len(match.groups()) == 2:
                item_id = str(counter + 1)
                name = match.group(1).strip(" ：:，,、")
                score = float(match.group(2))
            else:
                item_id = match.group(1)
                name = match.group(2).strip(" ：:，,、")
                score = float(match.group(3))
            if not name or _extract_score(match.group(0)) != score:
                continue
            return {
                "id": item_id,
                "name": name,
                "score": score,
                "scoring_method": self._detect_scoring_method(line),
                "criteria": line,
                "keywords": self._extract_keywords(name),
            }
        return None

    def _detect_scoring_method(self, text: str) -> str:
        for method, keywords in self.SCORING_METHODS.items():
            if any(keyword in text for keyword in keywords):
                return method
        return "综合评价"

    def _extract_keywords(self, text: str) -> list[str]:
        try:
            import jieba  # type: ignore

            return [word for word in jieba.lcut(text) if len(word) >= 2 and not word.isdigit()][:5]
        except Exception:
            return re.findall(r"[\u4e00-\u9fa5A-Za-z]{2,}", text)[:5]

    def _extract_items_without_categories(self) -> None:
        items: list[dict[str, Any]] = []
        for line in self.scoring_section.split("\n"):
            item = self._parse_scoring_item(line.strip(), len(items))
            if item:
                items.append(item)
        if not items:
            return
        total_score = sum(float(item.get("score") or 0) for item in items)
        self.categories.append({"name": "评分项目", "score": total_score, "weight": 1.0, "items": items})

    def extract(self) -> dict[str, Any]:
        _, _, section = self.find_scoring_section()
        if not section:
            self.scoring_section = _fallback_scoring_section(self.text)
        else:
            self.scoring_section = section

        self.extract_document_info()
        self.extract_categories()
        self.extract_scoring_items()
        result = self._build_result()
        result.update(
            _flatten_scoring_result(
                result,
                self.scoring_section,
                self.text,
                self.TECH_KEYWORDS,
                self.BIZ_KEYWORDS,
            )
        )
        # Keep the legacy structure consistent with the authoritative flattened
        # table rows; otherwise callers can still receive false totals/categories.
        if any(item.get("source_line") for item in result["items"]):
            groups = {}
            for item in result["items"]:
                groups.setdefault(item["category"], []).append(item)
            total = sum(item["score"] for item in result["items"])
            result["scoring_structure"] = {"total_score": total, "categories": [
                {"name": name, "score": sum(i["score"] for i in entries),
                 "weight": sum(i["score"] for i in entries) / total if total else 0,
                 "items": [{"id": i["id"], "name": i["title"], "score": i["score"],
                            "criteria": i["text"]} for i in entries]}
                for name, entries in groups.items()
            ]}
            result["extraction_metadata"].update(total_items=len(result["items"]), categories_count=len(groups))
        return result

    def _build_result(self) -> dict[str, Any]:
        return {
            "document_info": self.document_info,
            "scoring_structure": {
                "total_score": sum(float(category.get("score") or 0) for category in self.categories),
                "categories": self.categories,
            },
            "extraction_metadata": {
                "section_length": len(self.scoring_section),
                "categories_count": len(self.categories),
                "total_items": sum(len(category.get("items", [])) for category in self.categories),
            },
        }


def extract_scoring(full_text: str) -> dict[str, Any]:
    return ScoringExtractor(full_text).extract()


def scoring_json(data: dict[str, Any], key: str) -> str:
    return json.dumps(data.get(key) or [], ensure_ascii=False, indent=2)


def _flatten_scoring_result(
    result: dict[str, Any],
    scoring_section: str,
    full_text: str,
    tech_keywords: list[str],
    biz_keywords: list[str],
) -> dict[str, Any]:
    categories = result.get("scoring_structure", {}).get("categories", []) or []
    flat_items: list[dict[str, Any]] = []
    for category in categories:
        category_name = str(category.get("name") or "综合")
        for item in category.get("items", []) or []:
            criteria = str(item.get("criteria") or "")
            detected_category = _detect_flat_category(category_name, criteria, tech_keywords, biz_keywords)
            flat_items.append(
                {
                    "id": len(flat_items) + 1,
                    "category": detected_category,
                    "title": str(item.get("name") or "")[:120],
                    "score": item.get("score"),
                    "method": item.get("scoring_method"),
                    "text": criteria,
                    "keywords": item.get("keywords") or [],
                }
            )

    # PDF scoring tables are often separated from the first textual occurrence
    # of "评分标准". Scan every Markdown table instead of limiting fallback
    # extraction to the first matched scoring section.
    table_items = _extract_markdown_scoring_items(full_text, tech_keywords, biz_keywords)
    flat_items = _merge_scoring_items(table_items) if table_items else _merge_scoring_items(flat_items)

    if not flat_items:
        lines = [line.strip() for line in scoring_section.split("\n") if line.strip()]
        flat_items = [_line_to_flat_item(line, idx, tech_keywords, biz_keywords) for idx, line in enumerate(lines[:120])]
        flat_items = [item for item in flat_items if item]

    tech_items = [item for item in flat_items if item.get("category") == "技术"]
    biz_items = [item for item in flat_items if item.get("category") in {"商务", "价格"}]
    return {
        "summary_text": _format_items(flat_items[:40]),
        "tech_text": _format_items(tech_items[:30]),
        "biz_text": _format_items(biz_items[:30]),
        "items": flat_items[:100],
        "tech_items": tech_items[:80],
        "biz_items": biz_items[:80],
        "source_excerpt": "\n".join([line.strip() for line in scoring_section.split("\n") if line.strip()][:80]),
        "extraction_mode": "rule_fallback",
        "requires_review": True,
    }


def _compact(value: str) -> str:
    return re.sub(r"\s+|<br\s*/?>", "", value, flags=re.I)


def _score_cell(value: str) -> float | None:
    # Score columns may contain 0-5, 0～5分 or a single maximum; never use
    # the first number (zero) of a range, or extract a number from free prose.
    value = _compact(value).replace("（", "(").replace("）", ")")
    m = re.fullmatch(r"(?:满分|最高|最高得)?(\d+(?:\.\d+)?)(?:[-~～—–至](\d+(?:\.\d+)?))?(?:分|分值)?", value)
    if not m:
        return None
    score = float(m.group(2) or m.group(1))
    return score if 0 < score <= 1000 else None


def _header_map(row: list[str]) -> dict[str, int] | None:
    cells = [_compact(c) for c in row]
    def index(names):
        return next((i for i,c in enumerate(cells) if c in names), None)
    score = index({"分值", "分值范围", "满分", "得分", "分数", "最高分"})
    title = index({"评分内容", "评分项目", "评分项", "评审项目", "评审因素", "评分因素", "指标", "指标名称", "项目", "评审要素"})
    criteria = index({"评分标准", "评审标准", "评分细则", "指标描述", "评审内容", "评分办法", "评分说明"})
    if score is None or (title is None and criteria is None):
        return None
    title = title if title is not None else criteria
    return {"score": score, "title": title, "criteria": criteria if criteria is not None else title,
            "category": index({"类别", "分类", "评分类型"}), "number": index({"序号", "编号"})}


def _extract_markdown_scoring_items(text, tech_keywords, biz_keywords):
    items = []
    mapping = None
    width = 0
    last_page = None
    page = None
    lines = text.splitlines()
    for line_no, line in enumerate(lines, 1):
        marker = re.search(r"<!--\s*第(\d+)页\s*-->", line)
        if marker:
            page = int(marker.group(1))
        # A real chapter change ends a continuation table. Page/table headings don't.
        if re.match(r"^#{1,2}\s*第.+章", line):
            mapping = None
        if not _is_markdown_table_row(line):
            continue
        row = _split_markdown_row(line)
        if all(re.fullmatch(r"[:\- ]*", c) for c in row):
            continue
        header = _header_map(row)
        if header:
            mapping, width, last_page = header, len(row), page
            continue
        if not mapping or len(row) != width or (page and last_page and page - last_page > 1):
            mapping = None
            continue
        def cell(key):
            idx = mapping.get(key)
            return row[idx] if idx is not None else ""
        title, criteria, score_raw = cell("title"), cell("criteria"), cell("score")
        category_raw = cell("category")
        score = _score_cell(score_raw)
        # Price often spans all other cells: total is printed in category column.
        price_row = ("价格" in category_raw or "商务分" in category_raw) and any(k in "".join(row) for k in ("低价优先", "基准价", "报价得分", "价格分"))
        if price_row:
            score = _extract_score(category_raw)
            if score is None:
                score = _extract_score("".join(row))
            title, criteria = "价格评分", " ".join(c for c in row if c)
        elif score is None:
            if not score_raw and items and criteria and ("得分" in criteria or re.search(r"得\s*\d|不得分|注[：:]|响应文件|提供|扫描件", criteria)):
                # First row on a continued page is data, not a new header.
                previous = items[-1]
                previous["text"] += "\n" + criteria
                previous["criteria"] = previous["text"]
                suffix = _compact(title)
                if suffix and len(suffix) <= 6 and not previous["title"].endswith(suffix):
                    previous["title"] += suffix
                previous["source_page_end"] = page or previous.get("source_page")
                last_page = page
            else:
                mapping = None
            continue
        if score is None or not title or not criteria:
            continue
        title = _compact(title).strip("：:，,。；;")
        if mapping["title"] == mapping["criteria"]:
            title = re.split(r"[：:。；;]", title, 1)[0][:120]
        category = "价格" if price_row else _detect_flat_category("", title, tech_keywords, biz_keywords)
        items.append({"id":len(items)+1, "source_id":cell("number"), "title":title,
                      "score":score, "category":category, "method":"综合评价",
                      "text":criteria, "criteria":criteria, "source_page":page,
                      "source_page_end":page, "source_line":line_no,
                      "source_text":line.strip()})
        last_page = page
    return items


def _is_markdown_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3


def _split_markdown_row(line: str) -> list[str]:
    return [re.sub(r"\s+", " ", cell).strip() for cell in line.strip().strip("|").split("|")]


def _is_scoring_table_header(header: list[str]) -> bool:
    return _header_map(header) is not None


def _merge_scoring_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            source_id = str(item.get("source_id") or "").strip()
            title = re.sub(r"\s+", "", str(item.get("title") or ""))
            score = str(item.get("score") or "")
            key = f"{source_id}|{title[:80]}|{score}"
            if not title or key in seen:
                continue
            seen.add(key)
            normalized = dict(item)
            normalized["id"] = len(merged) + 1
            merged.append(normalized)
    return merged


def _fallback_scoring_section(text: str) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    matched = [line for line in lines if any(keyword in line for keyword in ScoringExtractor.SCORING_KEYWORDS)]
    if matched:
        return "\n".join(matched[:160])
    score_lines = [line for line in lines if "分" in line and ("评" in line or re.search(r"\d+(?:\.\d+)?\s*分", line))]
    return "\n".join(score_lines[:160])


def _line_to_flat_item(line: str, idx: int, tech_keywords: list[str], biz_keywords: list[str]) -> dict[str, Any] | None:
    if len(line) < 4 or not ("分" in line or "评分" in line or "评审" in line):
        return None
    score = _extract_score(line)
    if score is None:
        return None
    category = _detect_flat_category("", line, tech_keywords, biz_keywords)
    title = re.sub(r"[（(]?\d+(?:\.\d+)?\s*分[）)]?", "", line).strip()
    title = re.sub(r"^\d+(?:\.\d+)*[、.．]?\s*", "", title).strip()
    return {"id": idx + 1, "category": category, "title": title[:120] or line[:120], "score": score, "text": line}


def _detect_flat_category(category_name: str, text: str, tech_keywords: list[str], biz_keywords: list[str]) -> str:
    source = f"{category_name} {text}"
    if any(word in source for word in ("报价得分", "费率得分", "价格评分", "价格分", "低价优先")):
        return "价格"
    if any(keyword in source for keyword in tech_keywords):
        return "技术"
    if "价格" in source or "报价" in source:
        return "价格"
    if any(keyword in source for keyword in biz_keywords):
        return "商务"
    return "综合"


def _extract_score(line: str) -> float | None:
    # Reject minutes, clock times and amounts; retain legitimate "30分钟内到达得3分".
    matches = []
    for m in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*分(?!钟|鐘|秒)", line):
        before = line[max(0, m.start()-18):m.start()]
        if re.search(r"(?:\d\s*[时時点:]\s*|元|角)\s*$", before):
            continue
        matches.append(float(m.group(1)))
    return matches[-1] if matches else None


def _format_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        score = f"（{item['score']}分）" if item.get("score") is not None else ""
        lines.append(f"- [{item.get('category', '综合')}] {item.get('title')}{score}：{item.get('text')}")
    return "\n".join(lines)


def _format_section_excerpt(section: str) -> str:
    lines = [line.strip() for line in section.split("\n") if line.strip()]
    return "\n".join(f"- {line}" for line in lines[:30])


def _first_match(text: str, patterns: list[str], default: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ：:，。；;、")[:300]
    return default


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)
