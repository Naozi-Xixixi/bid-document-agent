from __future__ import annotations

import unittest
from contextlib import ExitStack
from generation_fixtures import seed_generation_db
from unittest.mock import patch

from docx import Document

from app.services import bid_graphic_service as graphic
from app.schemas.bid_graphic import GraphicSectionGenerateRequest


class GraphicExportTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        seed_generation_db(stack)

    def test_total_word_target_redistributes_when_one_chapter_reaches_limit(self) -> None:
        rows = {
            1: {"target_word_count": 10000, "graphic_markdown": ""},
            2: {"target_word_count": 1, "graphic_markdown": ""},
        }
        with patch.object(graphic, "_chapter_row", side_effect=lambda _bid_id, chapter_id: rows[chapter_id]):
            targets = graphic._allocate_chapter_targets(17, [1, 2], 12000)
        self.assertEqual(targets, {1: 8000, 2: 4000})
        self.assertEqual(sum(targets.values()), 12000)

    def test_total_word_target_uses_exact_sum_and_stable_rounding(self) -> None:
        rows = {index: {"target_word_count": 1, "graphic_markdown": ""} for index in (1, 2, 3)}
        with patch.object(graphic, "_chapter_row", side_effect=lambda _bid_id, chapter_id: rows[chapter_id]):
            targets = graphic._allocate_chapter_targets(17, [1, 2, 3], 10000)
        self.assertEqual(targets, {1: 3334, 2: 3333, 3: 3333})

    def test_catalog_order_keeps_parent_headings_and_skips_empty_branches(self) -> None:
        rows = [
            {"chapter_id": 1, "parent_id": 0, "sort_order": 1, "graphic_markdown": ""},
            {"chapter_id": 2, "parent_id": 1, "sort_order": 2, "graphic_markdown": "second"},
            {"chapter_id": 3, "parent_id": 1, "sort_order": 1, "graphic_markdown": "first"},
            {"chapter_id": 4, "parent_id": 0, "sort_order": 2, "graphic_markdown": ""},
        ]
        self.assertEqual(
            [row["chapter_id"] for row in graphic._ordered_graphic_export_rows(rows)],
            [1, 3, 2],
        )

    def test_table_and_image_marker_render_as_word_objects(self) -> None:
        doc = Document()
        markdown = (
            "## 企业实力 / 仓储设施\n\n"
            "| 项目 | 证明 |\n| --- | --- |\n| 仓库 | 现场照片 |\n\n"
            "[[KNOWLEDGE_IMAGE:7]]\n图注：实际仓储现场"
        )
        with patch.object(graphic.knowledge_service, "get_asset", return_value={"asset_id": 7}), \
                patch.object(graphic, "_add_knowledge_image", return_value=True) as add_image:
            graphic._append_markdown_to_doc(doc, markdown, 17, "仓储设施")
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual(doc.tables[0].cell(1, 0).text, "仓库")
        self.assertEqual(add_image.call_count, 1)
        self.assertEqual([p.text for p in doc.paragraphs if p.text], ["图注：实际仓储现场"])  # table spacer is intentional


    def test_bold_caption_does_not_duplicate_and_subheading_keeps_level(self) -> None:
        doc = Document()
        markdown = "## 资质说明\n\n[[KNOWLEDGE_IMAGE:7]]\n\n**图注：营业执照原件**"
        with patch.object(graphic.knowledge_service, "get_asset", return_value={"asset_id": 7}), \
                patch.object(graphic, "_add_knowledge_image", return_value=True) as add_image:
            graphic._append_markdown_to_doc(doc, markdown, 17, "营业执照")
        self.assertEqual(add_image.call_count, 1)
        self.assertEqual([p.text for p in doc.paragraphs], ["资质说明", "图注：营业执照原件"])
        self.assertEqual(doc.paragraphs[0].style.name, "Heading 3")

    def test_short_image_caption_and_explicit_figure_label_do_not_export_ocr_dump(self) -> None:
        doc = Document()
        asset = {"asset_id": 7, "asset_sub_type": "其他附件", "category": "现场场景类", "annotation": "文档主题：历史文件\nOCR摘要：" + "不相关文字" * 100}
        markdown = "[[KNOWLEDGE_IMAGE:7]]\n\n图：入库验收现场"
        with patch.object(graphic.knowledge_service, "get_asset", return_value=asset), \
                patch.object(graphic, "_add_knowledge_image", return_value=True):
            graphic._append_markdown_to_doc(doc, markdown, 17, "入库验收")
        self.assertEqual(graphic._knowledge_image_caption(asset), "现场场景类")
        self.assertEqual([p.text for p in doc.paragraphs], ["图：入库验收现场"])
        self.assertNotIn("OCR摘要", "\n".join(p.text for p in doc.paragraphs))


    def test_unapproved_image_and_its_caption_are_removed(self) -> None:
        raw = "正文。\n\n[[KNOWLEDGE_IMAGE:1928]]\n\n*图注：不相关的人员证书*\n\n结尾。"
        cleaned = graphic._normalize_graphic_markdown(raw, [], "供货合同")
        self.assertEqual(cleaned, "正文。\n\n结尾。")
        allowed = graphic._normalize_graphic_markdown(raw, [{"asset_id": 1928}], "供货合同")
        self.assertIn("[[KNOWLEDGE_IMAGE:1928]]", allowed)

    def test_auto_images_exclude_personal_documents_and_form_chapters(self) -> None:
        id_card = {"asset_id": 1320, "category": "人员资质类", "asset_sub_type": "居民身份证"}
        self.assertEqual(graphic._select_ranked_images([id_card], "授权", set(), set(), used_ids=set(), priority_expire=False), [])
        historical_license = {"asset_id": 1580, "category": "资质证书类", "asset_sub_type": "营业执照", "item_title": "示例文旅博览采购 制作文件"}
        self.assertEqual(graphic._select_ranked_images([historical_license], "营业执照", {"营业执照"}, {"qualification"}, used_ids=set(), priority_expire=False), [])
        other_project_invoice = {"asset_id": 3946, "item_id": 6, "item_title": "某集团有限公司", "category": "票据凭证类"}
        with patch.object(graphic.bid_case_reference_service, "historical_case_item_ids", return_value={6}):
            self.assertEqual(graphic._select_ranked_images([other_project_invoice], "发票", set(), {"document"}, used_ids=set(), priority_expire=False), [])
        with patch.object(graphic, "_bid_context", return_value={}):
            risks = graphic._audit_chapter_assets(17, 1, "营业执照", [historical_license])
        self.assertEqual([risk["rule"] for risk in risks], ["historical_image_source"])
        self.assertTrue(graphic._requires_manual_image_selection("生产厂商授权书及相关承诺"))
        self.assertTrue(graphic._requires_manual_image_selection("开标一览表"))
        self.assertFalse(graphic._requires_manual_image_selection("冷链配送现场"))
        self.assertFalse(graphic._chapter_allows_personnel_documents("生产厂商授权书及相关承诺"))
        self.assertTrue(graphic._chapter_allows_personnel_documents("配送人员健康证"))

    def test_export_rechecks_history_subject_and_revoked_evidence(self) -> None:
        assets = [
            {"asset_id": 1, "item_id": 15, "source_name": "历史投标文件"},
            {"asset_id": 2, "evidence_kind": "营业执照", "item_status": "completed",
             "evidence_review": {"status": "verified", "subject": "甲公司", "identifier": "A1"}},
            {"asset_id": 3, "evidence_kind": "营业执照", "item_status": "completed",
             "evidence_review": {"status": "verified", "subject": "乙公司", "identifier": "B1"}},
            {"asset_id": 4, "evidence_kind": "业绩合同", "item_status": "completed",
             "evidence_review": {"status": "withdrawn", "subject": "甲公司", "identifier": "C1"}},
        ]
        markdown = "\n\n".join(f"[[KNOWLEDGE_IMAGE:{i}]]\n\n图注：附件 {i}" for i in range(1, 5))
        with patch.object(graphic.bid_case_reference_service, "historical_case_item_ids", return_value={15}):
            result, kept = graphic._review_export_images(markdown, assets, "甲公司")
        self.assertEqual([asset["asset_id"] for asset in kept], [2])
        self.assertEqual(graphic._image_ids_from_markdown(result), [2])
        self.assertIn("已排除历史来源", result)

    def test_clear_ocr_certificate_fields_can_be_used_when_structured_fields_are_missing(self) -> None:
        license_asset = {"category": "资质证书类", "asset_sub_type": "营业执照", "structured_info": {"fields": {}},
                         "ocr_text": "统一社会信用代码\n910000000875794481\n名称\n示例农产品加工配送有限公司"}
        certificate_asset = {"category": "资质证书类", "asset_sub_type": "资质认定证书", "ocr_text": "证书编号：211200170172"}
        self.assertEqual(graphic._structured_field(license_asset, "cert_no", "证件编号"), "910000000875794481")
        self.assertEqual(graphic._structured_field(license_asset, "holder_name", "企业名称"), "示例农产品加工配送有限公司")
        self.assertEqual(graphic._structured_field(certificate_asset, "cert_no", "证书编号"), "211200170172")

    def test_automatic_image_selection_never_falls_back_to_expired_evidence(self) -> None:
        asset = {"asset_id": 3, "item_title": "当前企业资质", "asset_sub_type": "食品经营许可证"}
        with patch.object(graphic, "_rank_image_candidates", return_value=[]) as rank:
            self.assertEqual(graphic._select_ranked_images([asset], "食品经营许可证", {"食品经营许可证"}, {"qualification"}, used_ids=set(), priority_expire=False), [])
        self.assertTrue(rank.call_args_list)
        self.assertTrue(all(call.kwargs["allow_expired"] is False for call in rank.call_args_list))

    def test_food_receiving_chapter_only_autoselects_scene_images(self) -> None:
        exact, groups = graphic._resolve_image_scope("食材采购与验收制度 入库验收标准与流程", [])
        self.assertFalse(exact)
        self.assertEqual(groups, {"scene"})


class FixedOpeningFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_form_stream_saves_without_calling_model(self) -> None:
        form = "# 开标一览表\n| 投标综合费率报价 | 【待填写】 |"
        generation = {"fixed_markdown": form, "target_words": 100, "chapter": {"chapter_title": "开标一览表"}}
        saved = {"chapter_id": 1, "chapter_title": "开标一览表", "markdown": form, "images": [], "risks": []}
        with patch.object(graphic.chapter_collaboration, "start_revision", return_value=0), \
                patch.object(graphic, "_build_graphic_generation", return_value=generation), \
                patch.object(graphic, "save_graphic_section", return_value=saved) as save, \
                patch.object(graphic, "stream_chat_completion") as model:
            events = [event async for event in graphic.stream_graphic_section(7, GraphicSectionGenerateRequest(chapter_id=1))]
        self.assertEqual([event["type"] for event in events], ["chunk", "replace", "done"])
        self.assertEqual(events[-1]["result"]["markdown"], form)
        save.assert_called_once()
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
