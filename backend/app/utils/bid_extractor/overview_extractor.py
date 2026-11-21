from __future__ import annotations

import re


def normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"[\r\n]+", text or ""):
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def extract_project_overview(full_text: str) -> str:
    """Build a readable project overview from announcement/front-matter lines."""

    lines = normalize_lines(full_text)
    head = lines[:80]
    source = "\n".join(head)

    fields: list[str] = []
    project_name = _first_match(
        source,
        [
            r"(?:工程|项目|采购项目)?名称[：:]\s*([^\n]{6,120}(?:\n(?!采购项目|招标人|采购人|招标代理)[^\n]{2,120})?)",
            r"([^\n]{6,120}?(?:工程|项目|采购|服务|改造|建设))\s*(?:施工)?招标",
            r"([^\n]{6,120}?)\s*(?:资格预审公告|招标公告|采购公告)",
        ],
    )
    if project_name:
        fields.append(f"项目名称：{project_name}")

    buyer = _first_match(source, [r"招标人(?:为|：|:)\s*([^，。；\n]{3,80})", r"采购人(?:为|：|:)\s*([^，。；\n]{3,80})"])
    if buyer:
        fields.append(f"招标/采购单位：{buyer}")

    location = _first_match(source, [r"建设地点[：:]\s*([^\n]{3,100})", r"项目地点[：:]\s*([^\n]{3,100})"])
    if location:
        fields.append(f"项目地点：{location}")

    scale = _first_match(source, [r"建设规模[：:]\s*([^\n]{3,140})", r"采购规模[：:]\s*([^\n]{3,140})"])
    if scale:
        fields.append(f"项目规模：{scale}")

    budget = _first_match(source, [r"(?:合同估算价|预算金额|最高限价|采购预算)[：:]?\s*([\d,.]+\s*(?:万元|元|万))"])
    if budget:
        fields.append(f"预算/估算价：{budget}")

    duration = _first_match(source, [r"(?:计划工期|服务期限|交付期限)[：:]?\s*([^\n。；]{2,80})"])
    if duration:
        fields.append(f"工期/服务期：{duration}")

    scope = _extract_scope(lines)
    if scope:
        fields.append(f"招标范围：{scope}")

    project_section = _extract_project_section(lines)
    if project_section and project_section not in "\n".join(fields):
        fields.append(f"项目概况：{project_section}")

    if fields:
        return "\n".join(fields)

    return "\n".join(head[:8])[:1600]


def _extract_scope(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if any(keyword in line for keyword in ("招标范围", "采购范围", "服务范围", "建设内容")):
            block = [line]
            for next_line in lines[idx + 1 : idx + 5]:
                if re.match(r"^\d+(?:\.\d+)*\s+", next_line) and block:
                    break
                block.append(next_line)
            return " ".join(block)[:600]
    return ""


def _extract_project_section(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line).lstrip("#")
        if compact not in {"项目概况", "一、项目概况", "项目背景", "一、项目背景"}:
            continue
        content: list[str] = []
        for next_line in lines[idx + 1 : idx + 10]:
            if content and re.match(r"^(?:第[一二三四五六七八九十\d]+章|[一二三四五六七八九十]+、|\d+\.\d+)", next_line):
                break
            content.append(next_line.lstrip("# "))
        return " ".join(content)[:1200]
    return ""


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ：:，。；;、")[:300]
    return ""
