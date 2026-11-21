from __future__ import annotations

import json
import re
from typing import Any


class RequirementsExtractor:
    """BiaoShu-SKILL requirements extractor adapted without CLI side effects."""

    QUALIFICATION_KEYWORDS = ["资质", "认证", "证书", "资格", "许可", "ISO", "CMMI", "ITSS", "PMP", "信息系统集成"]
    TECHNICAL_KEYWORDS = ["技术参数", "性能指标", "功能要求", "系统要求", "并发", "响应时间", "可用性", "兼容性"]
    COMMERCIAL_KEYWORDS = ["工期", "交付时间", "付款方式", "质保", "售后服务", "培训", "维护", "响应时间"]
    DISQUALIFICATION_KEYWORDS = ["废标", "无效投标", "不予受理", "取消资格", "资格性审查", "符合性审查", "实质性响应"]

    def __init__(self, text: str):
        self.text = _normalize_text(text)
        self.requirements: dict[str, Any] = {
            "qualification_requirements": {"enterprise": [], "certifications": [], "personnel": [], "performance": []},
            "technical_requirements": {"parameters": [], "functions": [], "performance": []},
            "commercial_requirements": {"delivery": {}, "payment": {}, "warranty": {}, "training": {}},
            "disqualification_clauses": [],
        }

    def extract_all(self) -> dict[str, Any]:
        self._extract_qualifications()
        self._extract_technical_requirements()
        self._extract_commercial_requirements()
        self._extract_disqualification_clauses()
        result = dict(self.requirements)
        result.update(_flatten_requirements(result, self.text))
        return result

    def _extract_qualifications(self) -> None:
        lines = self.text.split("\n")
        in_section = False
        for line in lines:
            if any(keyword in line for keyword in ["资质要求", "资格要求", "投标人资格"]):
                in_section = True

            if in_section and re.match(r"第[一二三四五六七八九十\d]+章", line) and not any(keyword in line for keyword in ["资质", "资格"]):
                in_section = False
                continue

            if not in_section and not any(keyword in line for keyword in self.QUALIFICATION_KEYWORDS):
                continue

            if any(keyword in line for keyword in ["企业资质", "公司资质", "营业执照", "许可证", "资质"]):
                self.requirements["qualification_requirements"]["enterprise"].append({"type": "企业资质", "requirement": line, "mandatory": True})
            if any(keyword in line for keyword in ["ISO", "CMMI", "ITSS", "认证", "证书"]):
                self.requirements["qualification_requirements"]["certifications"].append({"type": "认证证书", "requirement": line, "mandatory": True})
            if any(keyword in line for keyword in ["项目经理", "PMP", "高级工程师", "人员资格", "负责人"]):
                self.requirements["qualification_requirements"]["personnel"].append({"type": "人员资格", "requirement": line, "mandatory": True})
            if any(keyword in line for keyword in ["类似项目", "业绩", "合同金额"]):
                self.requirements["qualification_requirements"]["performance"].append({"type": "业绩要求", "requirement": line, "mandatory": True})

    def _extract_technical_requirements(self) -> None:
        lines = self.text.split("\n")
        in_section = False
        for line in lines:
            if any(keyword in line for keyword in ["技术要求", "技术参数", "技术规格", "功能需求"]):
                in_section = True

            if in_section and re.match(r"第[一二三四五六七八九十\d]+章", line) and not any(keyword in line for keyword in ["技术", "功能"]):
                in_section = False
                continue

            if not in_section and not any(keyword in line for keyword in self.TECHNICAL_KEYWORDS):
                continue

            if any(keyword in line for keyword in ["并发", "响应时间", "可用性", "性能"]):
                self.requirements["technical_requirements"]["performance"].append({"category": "性能指标", "item": line, "mandatory": True})
            if any(keyword in line for keyword in ["功能", "模块", "子系统"]):
                self.requirements["technical_requirements"]["functions"].append({"module": line[:80], "requirement": line, "mandatory": True})
            if any(keyword in line for keyword in ["技术参数", "技术规格", "参数"]):
                self.requirements["technical_requirements"]["parameters"].append({"category": "技术参数", "item": line, "mandatory": True})

    def _extract_commercial_requirements(self) -> None:
        duration_patterns = [r"工期[：:]\s*(\d+)[日天月年]", r"服务期[限]?[：:]\s*([^\n。；]{2,80})", r"交付[时间期限]*[：:]\s*([^\n。；]{2,80})"]
        duration = _first_match(self.text, duration_patterns)
        if duration:
            self.requirements["commercial_requirements"]["delivery"] = {"total_duration": duration}

        warranty = _first_match(self.text, [r"质保[期务]?[：:]\s*(\d+)[年月]", r"售后服务[期]?[：:]\s*([^\n。；]{2,80})"])
        if warranty:
            self.requirements["commercial_requirements"]["warranty"] = {"period": warranty}

        for keyword in ["付款方式", "付款条件", "支付方式"]:
            if keyword not in self.text:
                continue
            match = re.search(f"{keyword}[：:]?([^。\n]+。?)", self.text)
            if match:
                self.requirements["commercial_requirements"]["payment"] = {"terms": match.group(1).strip()}
                break

        training = _first_match(self.text, [r"培训[服务要求]*[：:]\s*([^\n。；]{2,120})"])
        if training:
            self.requirements["commercial_requirements"]["training"] = {"requirement": training}

    def _extract_disqualification_clauses(self) -> None:
        lines = self.text.split("\n")
        in_section = False
        for line in lines:
            if any(keyword in line for keyword in ["废标条款", "无效投标", "不予受理", "否决投标"]):
                in_section = True

            if in_section and re.match(r"第[一二三四五六七八九十\d]+章", line) and not any(keyword in line for keyword in self.DISQUALIFICATION_KEYWORDS):
                in_section = False
                continue

            if in_section or any(keyword in line for keyword in self.DISQUALIFICATION_KEYWORDS):
                if any(keyword in line for keyword in ["未提供", "超过", "不符合", "未响应", "废标", "无效", "否决"]):
                    self.requirements["disqualification_clauses"].append({"type": "资格/符合性审查", "clause": line, "consequence": "废标或否决投标"})


def extract_requirements(full_text: str) -> dict[str, Any]:
    return RequirementsExtractor(full_text).extract_all()


def requirements_json(items: list[dict[str, Any]]) -> str:
    return json.dumps(items or [], ensure_ascii=False, indent=2)


def _flatten_requirements(result: dict[str, Any], text: str) -> dict[str, Any]:
    qualification_items: list[dict[str, Any]] = []
    for category, items in (result.get("qualification_requirements") or {}).items():
        for item in items or []:
            qualification_items.append(
                {
                    "id": len(qualification_items) + 1,
                    "category": "资质/资格",
                    "type": item.get("type") or category,
                    "text": item.get("requirement") or item.get("item") or str(item),
                    "mandatory": item.get("mandatory", True),
                }
            )

    technical_items: list[dict[str, Any]] = []
    for category, items in (result.get("technical_requirements") or {}).items():
        for item in items or []:
            technical_items.append(
                {
                    "id": len(technical_items) + 1,
                    "category": "技术要求",
                    "type": item.get("category") or category,
                    "text": item.get("requirement") or item.get("item") or str(item),
                    "mandatory": item.get("mandatory", True),
                }
            )

    commercial_items: list[dict[str, Any]] = []
    commercial = result.get("commercial_requirements") or {}
    for key, value in commercial.items():
        if not value:
            continue
        text_value = "；".join(f"{k}：{v}" for k, v in value.items()) if isinstance(value, dict) else str(value)
        commercial_items.append({"id": len(commercial_items) + 1, "category": "商务要求", "type": key, "text": text_value})

    invalid_items: list[dict[str, Any]] = []
    for item in result.get("disqualification_clauses") or []:
        invalid_items.append(
            {
                "id": len(invalid_items) + 1,
                "category": "废标/否决条款",
                "type": item.get("type") or "废标条款",
                "text": item.get("clause") or str(item),
                "consequence": item.get("consequence") or "废标或否决投标",
            }
        )

    if not qualification_items:
        qualification_items = _keyword_items(text, RequirementsExtractor.QUALIFICATION_KEYWORDS, "资质/资格", 36)
    if not commercial_items:
        commercial_items = _keyword_items(text, RequirementsExtractor.COMMERCIAL_KEYWORDS, "商务要求", 36)
    if not invalid_items:
        invalid_items = _keyword_items(text, RequirementsExtractor.DISQUALIFICATION_KEYWORDS + ["否决", "未响应", "不符合"], "废标/否决条款", 36)

    biz_lines = [item["text"] for item in qualification_items + commercial_items]
    invalid_lines = [item["text"] for item in invalid_items]
    return {
        "qualification_items": qualification_items[:80],
        "technical_items": technical_items[:80],
        "commercial_items": commercial_items[:80],
        "invalid_items": invalid_items[:80],
        "biz_text": _format_lines(biz_lines),
        "invalid_text": _format_lines(invalid_lines),
    }


def _keyword_items(text: str, keywords: list[str], category: str, limit: int) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        if not any(keyword in line for keyword in keywords):
            continue
        block = [line]
        for next_line in lines[idx + 1 : idx + 3]:
            if len(next_line) < 160 or any(keyword in next_line for keyword in keywords):
                block.append(next_line)
        value = " ".join(block).strip()[:800]
        if not value or value in seen:
            continue
        seen.add(value)
        selected.append({"id": len(selected) + 1, "category": category, "text": value})
        if len(selected) >= limit:
            break
    return selected


def _format_lines(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines[:80] if line)


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ：:，。；;、")[:300]
    return ""


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)
