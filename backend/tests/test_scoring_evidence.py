from __future__ import annotations

import base64
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docx import Document

from app.services import bid_scoring_evidence, tender_form_service


def verified_asset(asset: dict, kind: str, identifier: str, subject: str = "投标企业") -> dict:
    return {**asset, "evidence_kind": kind, "item_status": "completed",
            "evidence_review": {"status": "verified", "identifier": identifier, "subject": subject}}


class ScoringEvidenceTests(unittest.TestCase):
    def test_docx_preserves_named_response_table_and_leaves_quote_blank(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tender.docx"
            doc = Document()
            doc.add_paragraph("报价表")
            wrong = doc.add_table(rows=2, cols=2)
            wrong.cell(0, 0).text = "合同附表"
            doc.add_paragraph("第六章 响应文件格式")
            doc.add_paragraph("一、报价表格式")
            doc.add_paragraph("1-1 报价表")
            table = doc.add_table(rows=3, cols=2)
            table.cell(0, 0).paragraphs[0].add_run("报价").bold = True
            table.cell(1, 0).merge(table.cell(2, 0)).text = "备注"
            doc.add_paragraph("供应商电子签章：")
            doc.add_paragraph("二、最后承诺报价表")
            doc.add_table(rows=2, cols=2).cell(0, 0).text = "另一张表"
            doc.save(path)
            markdown = tender_form_service.extract_form(path, "报价费率表（不超过100%）")
        self.assertIn("| **报价** |  |", markdown)
        self.assertNotIn("合同附表", markdown)
        self.assertNotIn("另一张表", markdown)
        self.assertIn("供应商电子签章：", markdown)
        encoded = re.search(r"<!--TENDER_TABLE:([^>]+)-->", markdown)[1]
        layout = json.loads(base64.b64decode(encoded))
        self.assertEqual(len(layout["heights"]), 3)
        self.assertEqual(len(layout["widths"]), 2)
        self.assertTrue(any(cell["rowspan"] == 2 for cell in layout["cells"]))

    def test_links_only_matching_current_attachment_and_lists_other_slots(self):
        criteria = {"biz_scoring_items": json.dumps([{
            "name": "供应商业绩", "criteria": "食材供货及配送业绩须提供业绩合同扫描件。",
            "source_location": "评分表第9页",
        }])}
        assets = [
            {"asset_id": 1, "item_id": 10, "asset_sub_type": "业绩合同", "source_name": "当前企业合同", "resource_path": "contract.png", "page_num": 3},
            {"asset_id": 2, "item_id": 11, "asset_sub_type": "业绩合同", "source_name": "历史投标文件", "resource_path": "history.png"},
            {"asset_id": 3, "item_id": 12, "asset_sub_type": "车辆照片", "source_name": "企业实物照", "resource_path": "truck.png"},
            {"asset_id": 4, "item_id": 13, "asset_sub_type": "业绩合同", "source_name": "企业合同", "resource_path": "missing.png"},
        ]
        assets = [verified_asset(asset, asset["asset_sub_type"], "合同编号") for asset in assets]
        assets.append({**assets[0], "asset_id": 5, "evidence_review": None})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("contract.png", "history.png", "truck.png"):
                (root / name).write_bytes(b"fixture")
            with patch.object(bid_scoring_evidence.knowledge_service, "_resolve_stored_path", side_effect=lambda value: root / value):
                plan = bid_scoring_evidence.scoring_plan("供应商业绩 / 业绩合同", None, criteria, assets, {11})
        markdown = bid_scoring_evidence.append_evidence("已附合同和证明材料。", plan)
        self.assertIn("评分表第9页", markdown)
        self.assertIn("[[KNOWLEDGE_IMAGE:1]]", markdown)
        self.assertIn("原件第 3 页", markdown)
        self.assertIn("已关联，待复核", markdown)
        self.assertNotIn("已附合同", markdown)
        for id in (2, 3, 4, 5):
            self.assertNotIn(f"[[KNOWLEDGE_IMAGE:{id}]]", markdown)
        self.assertEqual(markdown, bid_scoring_evidence.append_evidence(markdown, plan))

    def test_exact_performance_item_precedes_feedback_mention(self):
        parsed = {
            "tech_scoring_items": json.dumps([{"title": "履约反馈", "criteria": "已认可的供应商业绩另行提供履约反馈。"}]),
            "biz_scoring_items": json.dumps([{"title": "供应商业绩", "criteria": "提供业绩合同关键页扫描件。"}]),
        }
        plan = bid_scoring_evidence.scoring_plan("业绩合同及证明材料", None, parsed, [], set())
        self.assertEqual(plan["criteria"], "提供业绩合同关键页扫描件。")

    def test_acceptance_returns_response_topics_without_inserting_unrelated_scans(self):
        criteria = {"tech_scoring_items": json.dumps([{
            "title": "食材检验验收方案", "text": "食材检验标准、检验流程、现场验收。"
        }])}
        plan = bid_scoring_evidence.scoring_plan("食材验收方案 / 采购人现场验收配合流程", None, criteria, [], set())
        result = bid_scoring_evidence.append_evidence("冷藏保持0℃-4℃，冷冻低于-18℃。误差控制在±1%。2-4小时内调拨。", plan)
        for unconfirmed in ("0℃", "±1%", "2-4小时"):
            self.assertNotIn(unconfirmed, result)
        self.assertIn("现场验收", result)
        self.assertNotIn("KNOWLEDGE_IMAGE", result)

    def test_vehicle_matching_requires_same_identity_and_deduplicates_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in range(3):
                path = root / f'{index}.png'
                path.write_bytes(f'image {index}'.encode())
                paths.append(str(path))
            subtypes = ['车辆租赁合同', '车辆行驶证', '车辆照片']
            assets = [
                verified_asset({"asset_id": index + 1, "asset_sub_type": subtype, "resource_path": path}, subtype, "皖A12345")
                for index, (subtype, path) in enumerate(zip(subtypes, paths))
            ]
            plan = {"kind": "vehicle", "label": "冷链冷藏车辆", "criteria": "同车材料", "source": "评分表"}
            duplicate = dict(assets[1], asset_id=4)
            exact = bid_scoring_evidence.link_attachments(plan, [*assets, duplicate], set())
            self.assertEqual([[asset["asset_id"] for asset in found] for _, found in exact["slots"]], [[1], [2], [3]])
            other_car = verified_asset(assets[2], "车辆照片", "皖A99999")
            mismatched = bid_scoring_evidence.link_attachments(plan, [*assets[:2], other_car], set())
            self.assertEqual([[asset["asset_id"] for asset in found] for _, found in mismatched["slots"]], [[1], [2], []])
            self.assertIn("待提供", bid_scoring_evidence.proof_section(mismatched))

    def test_certificate_requires_matching_type_role_and_number(self):
        with TemporaryDirectory() as directory:
            assets = []
            for i, (kind, subtype, number) in enumerate([
                ("食品安全管理体系", "食品安全管理体系认证证书", "ABC12345"),
                ("食品安全管理体系", "证书查询截图", "ABC12345"),
                ("食品安全管理体系", "证书查询截图", "ABC99999"),
                ("供应链安全管理体系", "证书查询截图", "ABC12345"),
            ], 1):
                path = Path(directory) / f'{i}.png'
                path.write_bytes(f'certificate page {i}'.encode())
                evidence_kind = kind + "认证证书" if i == 1 else "认证查询截图"
                assets.append(verified_asset({"asset_id": i, "asset_sub_type": subtype, "resource_path": str(path)},
                                             evidence_kind, number, "另一主体" if i == 4 else "投标企业"))
            plan = {"kind": "certificate", "certificate_kind": "食品安全管理体系"}
            linked = bid_scoring_evidence.link_attachments(plan, assets, set())
            self.assertEqual([[asset["asset_id"] for asset in found] for _, found in linked["slots"]], [[1], [2]])
            mismatched = bid_scoring_evidence.link_attachments(plan, [assets[0], *assets[2:]], set())
            self.assertFalse(mismatched["slots"][1][1])

    def test_acceptance_drops_exclusive_claims_and_unconfirmed_thresholds(self):
        plan = {"kind": "acceptance", "slots": [], "source": "评分表", "criteria": "现场验收"}
        text = "冷藏货物温度应保持在 0℃-4℃，冷冻货物温度应保持在 -18℃以下。保存期限不少于两年。验收单是唯一结算依据。"
        result = bid_scoring_evidence.append_evidence(text, plan)
        for invented in ("0℃", "-18℃", "两年", "唯一结算依据"):
            self.assertNotIn(invented, result)

    def test_preserves_underlined_blank_in_two_cell_paragraphs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.docx"
            doc = Document()
            doc.add_paragraph("第六章 响应文件格式")
            doc.add_paragraph("1-1 报价表")
            table = doc.add_table(rows=2, cols=2)
            cell = table.cell(0, 1)
            cell.paragraphs[0].add_run("费率大写：")
            cell.paragraphs[0].add_run("   ").underline = True
            paragraph = cell.add_paragraph("费率小写：")
            paragraph.add_run("   ").underline = True
            doc.save(path)
            markdown = tender_form_service.extract_form(path, "报价表")
        layout = json.loads(base64.b64decode(re.search(r"<!--TENDER_TABLE:([^>]+)-->", markdown)[1]))
        spec = next(cell for cell in layout["cells"] if cell["row"] == 0 and cell["col"] == 1)
        self.assertEqual(len(spec["paragraphs"]), 2)
        self.assertTrue(all(p["runs"][-1] == {"text": "   ", "underline": True} for p in spec["paragraphs"]))
