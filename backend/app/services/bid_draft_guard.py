"""Conservative checks for facts that a historical proposal cannot establish."""

from __future__ import annotations

import re
from datetime import date


_DATE = re.compile(r"(?P<year>20\d{2})\s*[-/.年]\s*(?P<month>\d{1,2})\s*[-/.月]\s*(?P<day>\d{1,2})\s*日?")
_PRICE = re.compile(r"(?P<label>投标(?:综合)?(?:费率)?报价|投标折扣率|报价费率)\s*[：:为是]?\s*(?P<value>\d+(?:\.\d+)?\s*%)")
_CAPABILITY = re.compile(r"(?:我司|我方|本公司|公司|投标人).{0,22}(?:拥有|具备|自有|配备|建有|已建立|建立了|已获得|已与|签订|持有|取得|生产线|分拣线)")
_RELATION = re.compile(r"(?:已获得|已与|已取得|签订|签署).{0,50}(?:授权|协议|合同|合作|证书|资质)")
_ATTACHMENT = re.compile(r"(?:详见|见).{0,16}(?:附件|扫描件)")
_EVIDENCE_CLAIM = re.compile(r"(?:已附|已备齐|已提交|已提供|已出具|联合.{0,15}出具).{0,35}(?:授权|证明|扫描件|证书|承诺函|合同|原件)")
_PLACEHOLDER = "【待核实】"
_SIGNED_CLAIM = re.compile(r"(?:均已|现已|已经|已)(?:完成|加盖|加签)?(?:供应商|投标人)?(?:电子签章|电子签名|公章)")


def render_opening_form(tender_text: str, project_overview: str, chapter_title: str) -> str | None:
    """Render a tender's fixed opening table without guessing a price or bidder."""
    if chapter_title.strip() == "报价表":
        matches = list(re.finditer(r"(?:^|\n)\s*1\s*-\s*1\s+报价表\s*\n", tender_text))
        if not matches:
            return None
        source = tender_text[matches[-1].end():matches[-1].end() + 1200].split("---", 1)[0]
        if not all(field in source for field in ("项目名称", "项目编号", "投标人名称", "投标范围", "投标报价", "费率", "备注", "投标人电子签章")):
            return None
        name = re.search(r"项目名称\s*[：:]\s*([^\n]+)", source)
        number = re.search(r"项目编号\s*[：:]\s*([^\n]+)", source)
        if not name or not number:
            return None
        return "\n".join([
            "# 1-1 报价表", "", f"项目名称：{name.group(1).strip()}",
            f"项目编号：{number.group(1).strip()}", "",
            "| 投标人名称 |  |", "| --- | --- |",
            "| 投标范围 | 第____标段（不分标段项目填写“全部”） |",
            "| 投标报价 | 费率：________ |", "| 备注 |  |", "",
            "投标人电子签章：", "",
        ])
    if "开标一览表" not in chapter_title:
        return None
    sections = list(re.finditer(r"(?:^|\n)\s*[三3][．.、]\s*开标一览表", tender_text))
    if not sections:
        return None
    source = tender_text[sections[-1].start():sections[-1].start() + 1800]
    fields = ("项目名称", "投标人全称", "投标范围", "投标综合费率报价")
    if not all(field in source for field in fields):
        return None
    title_match = re.search(r"项目名称[：:]\s*([^\n]+)", project_overview)
    project_name = title_match.group(1).strip() if title_match else "【待填写：以招标文件项目名称为准】"
    scope = "全部" if re.search(r"投标范围\s*全部", source) else "【待填写：投标范围】"
    return "\n".join([
        "# 开标一览表", "",
        "| 项目 | 内容 |", "| --- | --- |",
        f"| 项目名称 | {project_name} |",
        "| 投标人全称 | 【待填写：营业执照全称】 |",
        f"| 投标范围 | {scope} |",
        "| 投标综合费率报价 | 【待填写：确认后的综合费率，保留两位小数】 |",
        "| 备注 | 1. 投标综合费率报价不高于100%；2. 最终投标报价含税。 |", "",
        "投标人（公章）：【待盖章】", "",
        "填写前核对招标文件原表；报价须满足原文的费率范围及小数位要求。",
    ])


def _expiry_mentions(line: str, today: date) -> list[str]:
    if not re.search(r"有效期|到期|截止日期", line):
        return []
    matches = list(_DATE.finditer(line))
    if not matches:
        return []
    if len(matches) == 1 and not re.search(r"(?:至|到|截止|到期).{0,12}" + re.escape(matches[0].group()), line):
        return []
    # In a date range, only the final date is the expiry date.
    match = matches[-1]
    try:
        value = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return []
    return [match.group()] if value < today else []


def _append_review_note(line: str, note: str) -> str:
    # Keep the terminal pipe: otherwise the reviewer note turns a table row
    # into an ordinary paragraph in both the preview and Word exporter.
    if line.strip().startswith("|") and line.rstrip().endswith("|"):
        end = line.rfind("|")
        return line[:end].rstrip() + " " + note + " " + line[end:]
    return line + " " + note


def mark_unverified_facts(markdown: str, chapter_path: str, *, today: date | None = None) -> str:
    """Label obvious stale claims in newly generated text; never silently change numbers."""
    today = today or date.today()
    rows = []
    for line in markdown.splitlines():
        if not line.strip() or line.lstrip().startswith("[[KNOWLEDGE_IMAGE:"):
            rows.append(line)
            continue
        line = _SIGNED_CLAIM.sub("待按采购文件要求签章", line)
        if _PRICE.search(line) and _PLACEHOLDER not in line:
            line = _append_review_note(line, "【待核实：报价须以本项目确认金额为准】")
        expired = _expiry_mentions(line, today)
        if expired and "已过期" not in line:
            line = _append_review_note(line, "【已过期：须提供现行有效证明】")
        if (_CAPABILITY.search(line) or _RELATION.search(line)) and "【待核实：请核对当前企业证明材料】" not in line:
            line = _append_review_note(line, "【待核实：请核对当前企业证明材料】")
        if _ATTACHMENT.search(line) and "待核实" not in line:
            line = _append_review_note(line, "【待核实：确认附件已实际装订】")
        if _EVIDENCE_CLAIM.search(line) and "【待核实：附件是否存在】" not in line:
            line = _append_review_note(line, "【待核实：附件是否存在】")
        rows.append(line)
    return "\n".join(rows) + ("\n" if markdown.endswith("\n") else "")


def audit_chapter_text(chapter_id: int, chapter_title: str, markdown: str, *, today: date | None = None) -> list[dict]:
    """Return review items for price, expired evidence and unverified claims."""
    today = today or date.today()
    risks: list[dict] = []
    seen: set[tuple[str, str]] = set()
    per_rule: dict[str, int] = {}

    def add(rule: str, severity: str, issue: str, excerpt: str) -> None:
        key = (rule, excerpt[:90])
        if key in seen or per_rule.get(rule, 0) >= 3:
            return
        seen.add(key)
        per_rule[rule] = per_rule.get(rule, 0) + 1
        risks.append({
            "risk_id": f"text-{chapter_id}-{rule}-{len(risks)}",
            "severity": severity,
            "issue": issue,
            "rule": rule,
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "asset_id": 0,
            "excerpt": excerpt[:180],
        })

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("[[KNOWLEDGE_IMAGE:"):
            continue
        if _PRICE.search(line):
            add("unconfirmed_quote", "high", "正文含具体投标报价，须与本项目已确认的报价单核对。", line)
        expired = _expiry_mentions(line, today)
        if expired and not re.search(r"(?:不能|不应|不可|不得|不作为).{0,12}(?:当前|现行|本项目).{0,8}(?:有效|证明|资质)", line):
            add("expired_certificate", "high", f"正文引用的证件有效期 {expired[0]} 已过，须更换现行证明。", line)
        if _CAPABILITY.search(line) or _RELATION.search(line):
            add("company_capability", "medium", "企业能力陈述须与现行证照、设备或合同证明逐条核对。", line)
        if _ATTACHMENT.search(line):
            add("missing_attachment", "medium", "正文指向附件，须确认附件实际存在并与叙述一致。", line)
        if _EVIDENCE_CLAIM.search(line):
            add("unverified_attachment_claim", "high", "正文声称证明文件已提交，须核对附件确实存在且适用于本项目。", line)
        if "待核实" in line or "待填写" in line or "须提供现行有效证明" in line:
            add("unverified_placeholder", "medium", "正文有待核实内容，提交前须补充证明并更新。", line)
        empty_price = bool(re.search(r"^\|\s*(?:投标综合费率报价|投标报价)\s*\|\s*\|$", line))
        if ("开标一览表" in chapter_title or "报价表" in chapter_title) and "报价" in line and ("待填写" in line or "________" in line or empty_price):
            add("missing_bid_price", "high", "报价表缺少已确认的投标报价，不能作为正式投标文件。", line)
    return risks
