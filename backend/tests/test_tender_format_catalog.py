"""Tender-prescribed file order takes precedence over generated catalog suggestions."""
import unittest

from app.services.bid_catalog_service import _align_catalog_to_tender_format, _build_rule_catalog, _required_format_sections


class TenderFormatCatalogTests(unittest.TestCase):
    def test_response_checklist_with_split_numbers(self):
        text = "第七章响应文件格式\n响应文件资料清单\n序号资料名称备注\n一\n报价表\n二\n磋商保证金\n三\n服务方案\n备注：以原格式为准\n---"
        required = _required_format_sections(text)
        self.assertEqual(required, ["报价表", "磋商保证金", "服务方案"])
        result = _align_catalog_to_tender_format([{"title": "报价表", "children": [{"title": "虚构明细", "children": []}]}], required)
        self.assertEqual(result[0]["children"], [])
        self.assertTrue(result[2]["children"])

    def test_extracts_actual_checklist_after_table_of_contents_mention(self):
        text = (
            "目录\n第七章 投标文件格式 36\n其他章节\n"
            "---\n第七章投标文件格式\n投标文件资料清单\n序号资料名称备注\n"
            "一投标函\n二投标人情况综合简介\n三开标一览表\n"
            "第 36 页共 53 页\n---\n一．投标函\n"
        )
        self.assertEqual(_required_format_sections(text), ["投标函", "投标人情况综合简介", "开标一览表"])

    def test_missing_tender_sections_are_kept_in_required_order(self):
        required = ["投标函", "投标人情况综合简介", "开标一览表"]
        generated = [{"title": "开标一览表", "children": [{"title": "报价", "children": []}]}]
        result = _align_catalog_to_tender_format(generated, required)
        self.assertEqual([item["title"] for item in result], required)
        self.assertEqual(result[0]["children"], [])
        self.assertEqual(result[2]["children"], [])  # A prescribed form is generated intact.

    def test_no_mandatory_checklist_preserves_generated_catalog(self):
        generated = [{"title": "技术方案", "children": []}]
        self.assertIs(_align_catalog_to_tender_format(generated, []), generated)

    def test_mandatory_plan_gets_subsections_when_model_uses_different_heading(self):
        required = ["投标函", "供货安装（调试）方案", "售后服务体系与维保方案"]
        generated = [{"title": "供货与配送实施方案", "children": [{"title": "交付流程", "children": []}]}]
        result = _align_catalog_to_tender_format(generated, required, {"project_overview": "食材配送服务"})
        self.assertEqual([item["title"] for item in result], required)
        self.assertEqual(result[0]["children"], [])
        self.assertGreaterEqual(len(result[1]["children"]), 3)
        self.assertTrue(result[1]["children"][0]["children"])
        self.assertGreaterEqual(len(result[2]["children"]), 2)

    def test_preserves_model_subsections_under_mandatory_title(self):
        required = ["投标函", "供货安装（调试）方案"]
        generated = [{"title": "供货安装调试方案", "children": [{"title": "项目专用配送方案", "children": []}]}]
        result = _align_catalog_to_tender_format(generated, required)
        self.assertEqual(result[1]["children"][0]["title"], "项目专用配送方案")

    def test_rule_fallback_follows_mandatory_checklist(self):
        required = ["投标函", "供货安装（调试）方案"]
        fallback = _build_rule_catalog({"required_format_sections": required}, "标准均衡型")
        self.assertEqual([item["title"] for item in fallback], required)
        self.assertEqual(fallback[0]["children"], [])
        self.assertTrue(fallback[1]["children"])

    def test_food_specific_subsections_stay_in_food_projects(self):
        required = ["供货安装（调试）方案"]
        generic = _build_rule_catalog({"required_format_sections": required}, "标准均衡型")
        food = _build_rule_catalog(
            {"required_format_sections": required, "project_overview": "食材配送和食品安全保障"}, "标准均衡型"
        )
        self.assertNotIn("食品安全", str(generic))
        self.assertIn("食品安全", str(food))

    def test_numbered_response_format_skips_quotation_instructions_and_conditional_forms(self):
        text = (
            "第六章响应文件格式\n一、报价表格式\n2.填写报价原表。\n"
            "二、最后承诺报价表\n三、磋商响应函\n四、商务响应表\n"
            "五、联合协议（本项目不采用）\n六、中小企业声明函\n第七章其他事项\n"
        )
        self.assertEqual(_required_format_sections(text), ["报价表", "磋商响应函", "商务响应表", "中小企业声明函"])

    def test_conflicting_price_weights_survive_as_review_notes_only(self):
        from app.services import tender_form_service
        source = "综合评分满分为100分，价格分值占总分值的权重为15%。价格分（16分）。磋商报价得分＝（基准价/最终报价）×16%×100。"
        conflict = tender_form_service.price_score_conflict(source)
        self.assertIn("15%", conflict)
        self.assertIn("16%", conflict)
        checked = tender_form_service.review_price_score(
            "报价费率不得超过100%。磋商报价得分＝（基准价/最终报价）×16%×100。", conflict
        )
        self.assertIn("报价费率不得超过100%", checked)
        self.assertNotIn("×16%×100", checked)
        self.assertIn(conflict, checked)
