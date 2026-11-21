from datetime import date

from app.services import bid_draft_guard as guard


def test_old_quote_expired_certificate_and_unproved_capability_are_visible():
    draft = (
        "投标综合费率报价：80.00%\n"
        "有机认证有效期：2023年09月13日至2024年09月12日\n"
        "我司配备智能分拣线。"
    )
    marked = guard.mark_unverified_facts(draft, "报价与资质", today=date(2026, 10, 26))
    assert "80.00% 【待核实" in marked
    assert "2024年09月12日 【已过期" in marked
    assert "智能分拣线。 【待核实" in marked
    risks = guard.audit_chapter_text(7, "报价与资质", draft, today=date(2026, 10, 26))
    assert {item["rule"] for item in risks} >= {"unconfirmed_quote", "expired_certificate", "company_capability"}


def test_current_certificate_is_not_marked_expired():
    draft = "食品经营许可证有效期为2023年01月01日至2027年12月31日。"
    assert "已过期" not in guard.mark_unverified_facts(draft, "资质", today=date(2026, 10, 26))
    assert not any(item["rule"] == "expired_certificate" for item in guard.audit_chapter_text(1, "资质", draft, today=date(2026, 10, 26)))


def test_expired_document_explicitly_excluded_from_current_evidence_is_not_high_risk():
    note = "历史投标文件中的证书有效期至2024年09月12日，不能作为当前有效证明。"
    assert not any(item["rule"] == "expired_certificate" for item in guard.audit_chapter_text(1, "历史说明", note, today=date(2026, 10, 26)))


def test_named_supplier_authorization_and_missing_attachment_are_flagged():
    draft = "* 肉类：已获得某生产企业授权。\n具体授权书原件扫描件详见商务附件。"
    marked = guard.mark_unverified_facts(draft, "授权", today=date(2026, 10, 26))
    assert "已获得某生产企业授权。 【待核实" in marked
    assert "详见商务附件。 【待核实" in marked
    assert {item["rule"] for item in guard.audit_chapter_text(1, "授权", draft)} >= {"company_capability", "missing_attachment"}


def test_validity_word_without_date_does_not_crash():
    draft = "授权有效期覆盖本项目服务周期。"
    assert guard.mark_unverified_facts(draft, "授权") == draft
    assert guard.audit_chapter_text(1, "授权", draft) == []


def test_claimed_attached_evidence_is_reviewed_even_if_name_is_pending():
    draft = "| 肉禽类原厂授权 | [待核实] | 已附原件扫描件 |"
    marked = guard.mark_unverified_facts(draft, "生产厂商授权")
    assert "【待核实：附件是否存在】" in marked
    assert any(item["rule"] == "unverified_attachment_claim" and item["severity"] == "high" for item in guard.audit_chapter_text(1, "生产厂商授权", draft))


def test_opening_form_uses_tender_fields_and_never_guesses_bid_price():
    tender = "\n三．开标一览表\n项目名称\n投标人全称\n投标范围全部\n投标综合费率报价\n备注 1.报价≤100% 2.含税\n四．投标响应表"
    form = guard.render_opening_form(tender, "项目名称：文旅食材采购\n采购单位：文旅集团", "开标一览表")
    assert form is not None
    assert "| 项目名称 | 文旅食材采购 |" in form
    assert "| 投标范围 | 全部 |" in form
    assert "80.00%" not in form
    assert "【待填写：确认后的综合费率" in form
    assert any(item["rule"] == "missing_bid_price" and item["severity"] == "high" for item in guard.audit_chapter_text(1, "开标一览表", form))
    assert guard.render_opening_form(tender, "", "配送方案") is None


def test_response_quote_uses_original_fields_without_guessing_price_or_extra_fee():
    tender = ("\n1-1 报价表\n项目名称：测试项目\n项目编号：TEST01\n"
              "投标人名称\n投标范围\n投标报价\n费率\n备注\n投标人电子签章：\n---")
    form = guard.render_opening_form(tender, "", "报价表")
    assert form is not None
    assert "项目名称：测试项目" in form and "项目编号：TEST01" in form
    assert "| 投标报价 | 费率：________ |" in form
    assert "7200" not in form and "服务费" not in form
    assert any(item["rule"] == "missing_bid_price" for item in guard.audit_chapter_text(1, "报价表", form))
    assert guard.render_opening_form("1-1 报价表\n内容不全", "", "报价表") is None


def test_later_pending_attachment_does_not_validate_earlier_company_claim():
    draft = "我方已建立完善的厂商授权机制。相关原件待核实后提供。"
    marked = guard.mark_unverified_facts(draft, "授权")
    assert "【待核实：请核对当前企业证明材料】" in marked


def test_review_notes_stay_inside_table_cells():
    row='| 资源 | 我司具备冷链车辆，已附合同扫描件 |'
    marked=guard.mark_unverified_facts(row,'资源配置')
    assert marked.endswith('|')
    assert marked.count('|')==row.count('|')
    assert '【待核实：请核对当前企业证明材料】' in marked
    assert '【待核实：附件是否存在】' in marked
    assert guard.mark_unverified_facts(marked,'资源配置')==marked
