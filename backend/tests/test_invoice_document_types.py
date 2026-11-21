from __future__ import annotations

import asyncio
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from fastapi import UploadFile

from app.api.routes.invoice import (
    _extract_non_tax_receipt_fields,
    _is_non_tax_receipt,
    _validate_invoice,
    ocr_invoice,
)
from app.modules.knowledge.service import _extract_invoice_fields


RECEIPT_TEXT = """中央非税收入统一票据（电子）
票据代码:00010126 票据号码：0009671007
交款人统一社会信用代码: 校验码:b45e2a
交款人:某某科技有限公司 开票日期:2026-08-31
金额合计（大写）伍佰陆拾元整 （小写）560.00
"""

DIGITAL_VAT_TEXT = """电子发票（增值税专用发票）
发票号码：26342000003323756566
开票日期：2026年10月20日
购 名称：某某科技有限公司 销 名称：某某代理事务所
信 统一社会信用代码/纳税人识别号：91000000MAK8FMRE9Y 信 统一社会信用代码/纳税人识别号：91000000MA2TBGFA81
项目名称 金 额 税率/征收率 税 额
服务费 1089.11 1% 10.89
合 计 ¥1089.11 ¥10.89
价税合计（大写）壹仟壹佰圆整 （小写）¥1100.00
"""


class InvoiceDocumentTypeTests(TestCase):
    def test_non_tax_receipt_uses_its_own_labels(self) -> None:
        self.assertTrue(_is_non_tax_receipt(RECEIPT_TEXT))
        self.assertEqual(
            _extract_non_tax_receipt_fields(RECEIPT_TEXT),
            {
                "receipt_code": "00010126",
                "receipt_number": "0009671007",
                "verification_code": "b45e2a",
                "payer": "某某科技有限公司",
                "payer_tax_no": "",
                "issue_date": "2026-08-31",
                "total_amount": "560.00",
            },
        )

    def test_receipt_cannot_enter_vat_verification(self) -> None:
        upload = UploadFile(file=BytesIO(b"receipt"), filename="receipt.pdf")
        with patch("app.api.routes.invoice._read_invoice_text", return_value=(RECEIPT_TEXT, "pdf")):
            response = asyncio.run(ocr_invoice(upload))
        data = response["data"]
        self.assertEqual(data["document_type"], "non_tax_receipt")
        self.assertEqual(data["fields"], {})
        self.assertFalse(data["local_verification"]["official_ready"])
        self.assertFalse(data["external_verification"]["official_ready"])
        self.assertEqual(data["official_verification_payload"]["amount_excluding_tax"], "")

    def test_vat_invoice_validation_is_unchanged(self) -> None:
        self.assertFalse(_is_non_tax_receipt("增值税电子普通发票"))
        checks = _validate_invoice(
            {
                "invoice_number": "12345678",
                "issue_date": "2026-08-31",
                "amount_excluding_tax": "500.00",
                "title": "某某公司",
                "tax_no": "123456789012345678",
            }
        )
        self.assertTrue(checks["official_ready"])

    def test_impossible_date_not_ready_for_official_check(self) -> None:
        checks = _validate_invoice({'invoice_number':'12345678','issue_date':'2026-02-30','amount_excluding_tax':'100','title':'测试企业','tax_no':'123456789012345678'})
        self.assertFalse(checks['official_ready'])
        self.assertTrue(any('开票日期' in item['message'] for item in checks['checks']))

    def test_digital_vat_invoice_uses_summary_tax_and_buyer(self) -> None:
        fields = _extract_invoice_fields(DIGITAL_VAT_TEXT)
        self.assertEqual(fields["invoice_code"], "")
        self.assertEqual(fields["invoice_number"], "26342000003323756566")
        self.assertEqual(fields["amount_excluding_tax"], "1089.11")
        self.assertEqual(fields["tax_amount"], "10.89")
        self.assertEqual(fields["total_amount"], "1100.00")
        self.assertEqual(fields["title"], "某某科技有限公司")
