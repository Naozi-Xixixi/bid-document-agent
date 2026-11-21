"""Build auditable scoring responses from the tender and tenant-owned attachments."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import re
from functools import lru_cache
from pathlib import Path

from app.modules.knowledge import service as knowledge_service
from app.services import bid_case_reference_service

EVIDENCE_HEADING = "### 评分响应与附件"
_RULES = {
    "performance": {
        "chapter": r"供应商业绩|投标人?业绩|业绩合同|(?:同类|类似).*业绩|(?:供货|配送)业绩",
        "label": "供应商业绩",
        "source": r"供应商业绩|项目业绩|投标人(?:供货)?业绩|食材供货及配送业绩",
        "slots": (
            ("业绩合同扫描件", ("业绩合同", "食材供货合同", "供货合同", "配送合同", "食材配送合同")),
            ("关键评审因素缺失时的业主证明", ("业绩证明", "业主证明", "履约证明", "履约反馈", "验收报告")),
        ),
    },
    "vehicle": {
        "chapter": r"冷链冷藏车辆|冷链车辆|车辆配置|配送车辆|运输车辆|车辆配备|车辆清单及证明|行驶证|车辆租赁合同",
        "label": "冷链冷藏车辆",
        "source": r"冷链冷藏箱式货车|冷链冷藏车辆|运输车辆|车辆配备情况|配送能力.*配送车辆",
        "slots": (
            ("车辆购买凭证或租赁协议", ("车辆购买合同", "购车合同", "车辆购置发票", "车辆租赁合同", "车辆租赁协议")),
            ("同车行驶证", ("车辆行驶证",)),
            ("同车实物照片", ("冷链配送车", "冷藏车照片", "车辆照片")),
        ),
    },
    "certificate": {
        "chapter": r"食品安全管理体系|质量管理体系|诚信管理体系|供应链安全管理体系|食品可追溯管理体系",
        "label": "体系认证证书",
        "source": r"食品安全管理体系|质量管理体系|诚信管理体系|供应链安全管理体系|食品可追溯管理体系",
        "slots": (
            ("证书扫描件", ("体系认证证书", "食品安全管理体系认证证书", "供应链安全管理体系认证证书", "食品可追溯管理体系认证证书", "质量管理体系认证证书", "诚信管理体系认证证书")),
            ("同证书查询截图", ("证书查询截图", "认证查询截图", "认证证书查询截图", "体系认证查询截图")),
        ),
    },
    "qualification": {
        "chapter": r"营业执照|食品经营许可证|食品经营相关资质",
        "label": "企业资质",
        "source": r"营业执照|食品经营许可证",
        "slots": (("营业执照", ("营业执照",)), ("食品经营许可证", ("食品经营许可证",))),
    },
    "acceptance": {
        "chapter": r"食材检验验收|食材验收|入库验收|现场交接.*验收|现场验收",
        "label": "食材检验验收",
        "source": r"食材检验验收方案|食材检验标准",
        "slots": (),
    },
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


@lru_cache(maxsize=32)
def _docx_scoring_rows(path: str, modified: int) -> tuple[tuple[str, str, str], ...]:
    from docx import Document

    document = Document(path)
    rows = []
    for index, table in enumerate(document.tables, 1):
        for row in table._tbl.xpath("./w:tr"):
            cells = ["".join(cell.xpath(".//w:t/text()")).strip() for cell in row.xpath("./w:tc")]
            if len(cells) == 4 and "分" in cells[-1] and len(cells[-2]) >= 24:
                rows.append((cells[1], cells[-2], f"招标文件第 {index} 张评分表"))
            elif len(cells) >= 4 and re.fullmatch(r"\d{1,3}", cells[0]) and len(cells[-1]) >= 24:
                if re.fullmatch(r"[\d.～~至-]+(?:分)?", cells[-2]):
                    rows.append((cells[-3], cells[-1], f"招标文件第 {index} 张评分表"))
    return tuple(rows)


def _current_requirement(rule: dict, tender_path: Path | None, parse_row: dict) -> tuple[str, str]:
    candidates = []
    if tender_path and tender_path.suffix.lower() == ".docx" and tender_path.is_file():
        try:
            rows = _docx_scoring_rows(str(tender_path), tender_path.stat().st_mtime_ns)
        except (OSError, ValueError):
            rows = ()
        for title, criteria, location in rows:
            if re.search(rule["source"], _compact(title + criteria)):
                candidates.append((bool(re.fullmatch(rule["source"], _compact(title))), criteria, f"{location} · {title.strip() or rule['label']}"))
    for key in ("tech_scoring_items", "biz_scoring_items", "qualification_requirements"):
        try:
            rows = json.loads(parse_row.get(key) or "[]")
        except (TypeError, ValueError):
            continue
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            title = str(row.get("评审项目") or row.get("评分项名称") or row.get("指标") or row.get("title") or row.get("name") or "")
            criteria = str(row.get("评审标准") or row.get("评分细则") or row.get("criteria") or row.get("text") or row.get("content") or row.get("review_standard") or "")
            if criteria and re.search(rule["source"], _compact(title + criteria)):
                location = str(row.get("source_location") or "")
                if not location or "None" in location:
                    location = str(row.get("source_title") or "招标评分要求")
                candidates.append((bool(re.fullmatch(rule["source"], _compact(title))), criteria, location))
    if candidates:
        _, criteria, location = max(candidates, key=lambda row: row[0])
        return criteria, location
    if rule["label"] == "企业资质":
        for line in str(parse_row.get("raw_full_text") or "").splitlines():
            if len(line) <= 800 and re.search(rule["source"], _compact(line)) and re.search(r"提供|具备|持有|有效|复印件|扫描件", line):
                return line.strip(), "招标文件资格要求"
    return "", ""


def scoring_plan(chapter_path: str, tender_path: Path | None, parse_row: dict, assets: list[dict],
                 historical_ids: set[int]) -> dict | None:
    current = chapter_path.split(" / ")[-1]
    if re.search(r"调度|运力冗余|维修|保养|车辆应急|履约反馈", current) and not re.search(r"证明|合同|行驶证|照片", current):
        return None
    for title in reversed(chapter_path.split(" / ")[-2:]):
        matches = [key for key, rule in _RULES.items() if re.search(rule["chapter"], _compact(title))]
        if len(matches) == 1:
            kind = matches[0]
            break
    else:
        return None

    rule = _RULES[kind]
    criteria, source = _current_requirement(rule, tender_path, parse_row)
    if not criteria:
        return None
    plan = {"kind": kind, "label": rule["label"], "criteria": criteria, "source": source}
    if kind == "certificate":
        names = re.findall(rule["chapter"], _compact(current))
        if len(set(names)) != 1:
            return None
        plan["certificate_kind"] = names[0]
        plan["label"] = names[0] + "认证证书"
    if kind == "qualification":
        required = set(re.findall(rule["source"], _compact(criteria)))
        relevant = set(re.findall(rule["source"], _compact(current)))
        plan["qualification_types"] = sorted(required & relevant if relevant else required)
        if not plan["qualification_types"]:
            return None
    if kind == "acceptance":
        original = str(parse_row.get("raw_full_text") or "")
        acceptance_terms = []
        for line in original.splitlines():
            if 15 <= len(line) <= 650 and re.search(r"验收签单|验收不合格|检验检疫证|验收合格.*数量", line):
                acceptance_terms.append(line.strip())
        plan["tender_terms"] = "\n".join(dict.fromkeys(acceptance_terms))[:1600]
    return link_attachments(plan, assets, historical_ids)


def attachment_types(plan: dict) -> set[str]:
    return {subtype for _, subtypes in _proof_slots(plan) for subtype in subtypes}


def _proof_slots(plan: dict) -> tuple:
    slots = _RULES[plan["kind"]]["slots"]
    if plan["kind"] == "qualification":
        return tuple(slot for slot in slots if slot[0] in plan["qualification_types"])
    return slots


_VIN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")


def _identity(asset: dict, kind: str) -> dict[str, str]:
    number = _compact(str(asset["evidence_review"]["identifier"])).upper()
    if kind == "certificate":
        return {"证书编号": number}
    if kind == "performance":
        return {"业绩编号": number}
    return {"车辆识别代号" if _VIN.fullmatch(number) else "车牌": number}


def _slot_index(asset: dict, plan: dict, rules: tuple) -> int | None:
    subtype = asset["evidence_kind"]
    if plan["kind"] == "certificate":
        if subtype == "认证查询截图":
            return 1
        return 0 if subtype == plan["certificate_kind"] + "认证证书" else None
    return next((i for i, (_, subtypes) in enumerate(rules) if subtype in subtypes), None)


def _proof_groups(plan: dict, rules: tuple, eligible: list[tuple]) -> list[dict]:
    documents: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}
    for slot, asset in eligible:
        subject = _compact(asset["evidence_review"]["subject"]).casefold()
        label, identifier = next(iter(_identity(asset, plan["kind"]).items()))
        documents.setdefault((subject, label, identifier), []).append((slot, asset))

    groups = []
    for (_, label, identifier), members in documents.items():
        slots = [
            (slot_label, sorted(
                [asset for slot, asset in members if slot == index],
                key=lambda asset: (asset.get("item_id") or 0, asset.get("page_num") or 0, asset["asset_id"]),
            ))
            for index, (slot_label, _) in enumerate(rules)
        ]
        # Supporting pages must belong to a group with its primary proof.
        if not slots[0][1]:
            continue
        groups.append({"identity": {label: identifier}, "slots": slots})
    return groups


def link_attachments(plan: dict, assets: list[dict], historical_ids: set[int]) -> dict:
    rules = _proof_slots(plan)
    eligible = []
    seen = set()
    for asset in assets if rules else ():
        asset_id = asset.get("asset_id")
        if (not asset_id or asset.get("reference_only")
                or not knowledge_service.is_verified_evidence_asset(asset)
                or not bid_case_reference_service.is_current_company_evidence(asset, historical_ids)):
            continue
        slot = _slot_index(asset, plan, rules)
        if slot is None:
            continue
        path = knowledge_service._resolve_stored_path(asset.get("resource_path"))
        try:
            if not path or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} or not path.is_file() or not path.stat().st_size:
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").digest()
        except OSError:
            continue
        if digest in seen:
            continue
        seen.add(digest)
        eligible.append((slot, asset))
    if plan["kind"] in {"vehicle", "certificate", "performance"}:
        groups = _proof_groups(plan, rules, eligible)
        slots = [(label, [asset for group in groups for asset in group["slots"][i][1]])
                 for i, (label, _) in enumerate(rules)]
        included = {asset["asset_id"] for _, linked in slots for asset in linked}
        return {**plan, "slots": slots, "proof_groups": groups,
                "unverified_assets": len({asset["asset_id"] for _, asset in eligible} - included)}
    slots = []
    for i, (label, _) in enumerate(rules):
        documents = {}
        for slot, asset in eligible:
            if slot == i:
                documents.setdefault(asset.get("item_id") or asset["asset_id"], []).append(asset)
        ordered = [asset for pages in documents.values()
                   for asset in sorted(pages, key=lambda page: (page.get("page_num") or 0, page["asset_id"]))]
        slots.append((label, ordered))
    return {**plan, "slots": slots}


def plan_prompt(plan: dict) -> str:
    slots = "；".join(
        f"{label}：{'已关联待复核' if assets else '待提供'}" for label, assets in plan["slots"]
    ) or "按方案正文回应，现场记录属于执行产物"
    return (
        f"本章招标评分原文（{plan['source']}）：{plan['criteria']}\n"
        f"附件实际状态：{slots}。系统将在正文之后生成附件对应表及插图。"
        "正文只描述已经确认的事实与拟采取措施，不得写已提供、已随附或已提交证明，"
        "不得推算车辆或业绩数量、得分、合同金额或资质有效性。"
        f"采购验收原文摘录：{plan.get('tender_terms') or '未提取额外条款'}。\n"
        "验收方案分别写清到货入库查验与采购人现场验收，包含岗位、票证核对、外观及数量、温控核查、异常隔离退换和留痕。\n"
        "逐条响应检验标准、检验流程和验收方案。仅在招标原文明确给出时写具体温度、时限、抽样比例或设备名称，"
        "其余写按相应品类招标要求、产品标签及采购人确认的标准执行。企业未提供的设备、人员和检测能力不得写成承诺。"
    )


def strip_generated_evidence(markdown: str) -> str:
    return markdown.split("\n" + EVIDENCE_HEADING, 1)[0].rstrip()


def proof_section(plan: dict) -> str:
    """Proof chapters show supported facts without inventing assets or projects."""
    if plan["kind"] == "performance":
        body = (
            "按评分要求整理食材供货及配送业绩。每项业绩应核对供应商名称、合同签订时间、项目内容及履约情况，"
            "并按项目去重。合同无法体现关键评审因素时，补充业主单位或合同甲方出具的证明。"
        )
    elif plan["kind"] == "qualification":
        body = "按招标资格要求提供已核验的企业证明，并核对持证主体及适用范围。"
    elif plan["kind"] == "certificate":
        body = "按证书编号核对证书扫描件和对应的认证查询截图。证书主体、有效期及采购要求由投标人复核。"
    else:
        body = (
            "按单辆车整理资料。自有车辆核对购买合同或购置发票、行驶证及车辆照片；租赁车辆核对租赁合同或协议、"
            "行驶证及车辆照片。按同一车牌或车辆识别代号交叉核对，并复核权属或租赁关系、冷藏车型及使用有效性。"
        )
    return append_evidence(body, plan)


def _guard_acceptance_thresholds(markdown: str, tender_terms: str) -> str:
    source = _compact(tender_terms)

    def guarded(pattern: str, replacement: str, value: str) -> str:
        return re.sub(pattern, lambda match: match[0] if _compact(match[0]) in source else replacement, value)

    value = guarded(
        r"冷藏(?:保持|控制在|温度为)\s*[-−]?\d+(?:\.\d+)?\s*(?:℃|°C)?\s*[-–~～至]\s*[-−]?\d+(?:\.\d+)?\s*(?:℃|°C)[，、,]\s*冷冻(?:低于|不高于|保持|控制在)?\s*[-−]?\d+(?:\.\d+)?\s*(?:℃|°C)(?:以下)?",
        "冷藏、冷冻温度按招标文件对应品类及产品标签要求核查",
        markdown,
    )
    value = guarded(
        r"(?:计量)?误差(?:应)?(?:控制在|不超过|为)\s*[±+\-]?\s*\d+(?:\.\d+)?\s*[%％](?:或规定范围内)?",
        "计量偏差按采购人确认的标准复核",
        value,
    )
    value = guarded(
        r"(?:在\s*)?\d+(?:\.\d+)?(?:\s*[-–~～至]\s*\d+(?:\.\d+)?)?\s*(?:小时|分钟)内(?=\s*调拨|\s*补货|\s*送达|\s*更换|\s*退换|\s*整改|\s*反馈|\s*完成)",
        "按采购人确认的时限",
        value,
    )
    value = guarded(
        r"(?:冷藏|冷冻|到货|货物中心)[^。；，、|\n]{0,32}?[-−+]?\d+(?:\.\d+)?\s*(?:[-–~～至]\s*[-−+]?\d+(?:\.\d+)?\s*)?(?:℃|°C)(?:以下|以上)?",
        "温度按相应品类采购要求及产品标签核查",
        value,
    )
    value = guarded(
        r"[（(](?:通常为|一般为)?\s*\d+(?:\.\d+)?\s*(?:小时|分钟)内[）)]",
        "",
        value,
    )
    value = guarded(
        r"(?:资料)?保存(?:期限)?(?:不少于|至少|不低于|为)\s*[一二两三四五六七八九十\d]+\s*(?:年|个月|月)",
        "保存期限按采购要求及双方确认的规定执行",
        value,
    )
    value = guarded(
        r"唯一(?:有效)?(?:的)?(?:结算)?(?:依据|凭证)",
        "核对依据之一",
        value,
    )
    value = re.sub(
        r"(?:所有|全部)(?:的)?(?:供货)?产品[，,]?[^。；\n]{0,15}保质期(?:不(?:得|少)?少于|不少于|至少|须达到)[^。；\n]{0,12}三分之二",
        "对采购文件明确规定的品类核查剩余保质期不少于三分之二",
        value,
    )
    return value


def append_evidence(markdown: str, plan: dict) -> str:
    markdown = strip_generated_evidence(markdown)
    if plan["kind"] == "acceptance":
        markdown = _guard_acceptance_thresholds(markdown, plan.get("tender_terms", ""))
    markdown = re.sub(r"(?:均已|现已|已经|已)(?:随附|附上|附有|附|提交|提供)(?=[^。；\n|]{0,30}(?:合同|证明|材料|扫描件|行驶证|照片|凭证))", "拟在核验后提供", markdown)
    def cell(text: str) -> str:
        return " ".join(text.replace("|", "｜").split())

    rows = [
        EVIDENCE_HEADING, "", f"招标依据：{plan['source']}", "",
        cell(plan["criteria"]), "",
    ]
    if not plan["slots"]:
        return "\n\n".join([markdown, "\n".join(rows).rstrip(), "响应核对项：食材检验标准、检验流程及现场验收。验收单和异常处置记录在履约过程留存。"])

    assets_by_id = {}
    proof_groups = plan.get("proof_groups") or [{"identity": {}, "slots": plan["slots"]}]
    for group in proof_groups:
        identity = group["identity"]
        if identity:
            rows.extend(["", " / ".join(f"{name}：{value}" for name, value in identity.items()), ""])
        rows.extend(["| 证明要求 | 关联附件 | 复核状态 |", "| --- | --- | --- |"])
        for label, assets in group["slots"]:
            for asset in assets:
                assets_by_id[asset["asset_id"]] = (label, asset)
            refs = "、".join(f"附件 {asset['asset_id']}" for asset in assets)
            missing = "按合同内容确认是否需要补充" if "关键评审因素" in label else "待提供"
            rows.append(f"| {label} | {refs or '—'} | {'已关联，待复核' if refs else missing} |")
    if plan.get("unverified_assets"):
        rows.extend(["", f"另有 {plan['unverified_assets']} 项候选附件无法配成同一材料组，待核对。"])
    if plan["kind"] == "vehicle":
        rows.extend(["", "复核要求：按同一车辆核对购买或租赁凭证、行驶证及照片。"])
    for asset_id, (label, asset) in assets_by_id.items():
        name = cell(str(asset.get("source_name") or asset.get("item_title") or asset.get("title") or "企业资料"))
        page = asset.get("page_num") or asset.get("page_number")
        section = cell(str(asset.get("source_chapter") or ""))
        if section and section not in name:
            name = f"{name} · {section}"
        origin = f"{name}，原件第 {page} 页" if page else name
        rows.extend(["", f"[[KNOWLEDGE_IMAGE:{asset_id}]]", "", f"图注：附件 {asset_id} · {label} · {origin}"])
    return markdown + "\n\n" + "\n".join(rows)
