from __future__ import annotations

import re
from datetime import date
import tempfile
from pathlib import Path

import pdfplumber
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.modules.knowledge import service as knowledge_service


router = APIRouter(prefix="/invoice")

OFFICIAL_PLATFORM_URL = "https://inv-veri.chinatax.gov.cn/fpcygzfw/national-invoice-check"


def _is_non_tax_receipt(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "非税收入" in compact and "票据" in compact


def _extract_non_tax_receipt_fields(text: str) -> dict[str, str]:
    def match(pattern: str) -> str:
        found = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return found.group(1).strip() if found else ""

    # These are receipt labels. Do not map the receipt total to a VAT
    # invoice's tax-exclusive amount or its payer to an invoice title.
    return {
        "receipt_code": match(r"票据代码\s*[：:]\s*([0-9]{6,30})"),
        "receipt_number": match(r"票据号码\s*[：:]\s*([0-9]{6,30})"),
        "verification_code": match(r"校验码[ \t]*[：:][ \t]*([0-9A-Z]{6,16})(?![0-9A-Z])"),
        "payer": match(r"交款人\s*[：:]\s*([^\r\n]+?)(?=\s+开票日期\s*[：:]|\s*$)"),
        "payer_tax_no": match(r"交款人统一社会信用代码[ \t]*[：:][ \t]*([0-9A-Z]{18})(?![0-9A-Z])"),
        "issue_date": match(r"开票日期\s*[：:]\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})"),
        "total_amount": match(r"[（(]小写[）)]\s*[¥￥]?\s*([0-9][0-9,]*\.\d{2})"),
    }


def _ok(data: object, message: str = "success") -> dict[str, object]:
    return {"code": 0, "message": message, "data": data}


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": "INVOICE_OCR_ERROR", "message": message, "data": None})


def _validate_invoice(fields: dict[str, str]) -> dict[str, object]:
    checks: list[dict[str, str]] = []
    required = {
        "invoice_number": "发票号码",
        "issue_date": "开票日期",
        "amount_excluding_tax": "不含税金额",
        "title": "发票抬头",
        "tax_no": "税号",
    }
    # Never use the legacy generic amount field for official verification:
    # older OCR output may represent the tax-inclusive total instead.
    amount_excluding_tax = str(fields.get("amount_excluding_tax") or "").strip()
    field_values = {**fields, "amount_excluding_tax": amount_excluding_tax}
    missing = [label for key, label in required.items() if not str(field_values.get(key) or "").strip()]
    if missing:
        checks.append({"level": "high" if len(missing) >= 2 else "medium", "message": f"缺少：{'、'.join(missing)}"})
    issue_date = str(fields.get("issue_date") or "").strip()
    if issue_date:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date):
                raise ValueError("格式错误")
            date.fromisoformat(issue_date)
        except ValueError:
            checks.append({"level": "medium", "message": "开票日期格式需要人工确认"})
    amount = amount_excluding_tax.replace(",", "").replace("，", "").strip()
    if amount and not re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?", amount):
        checks.append({"level": "medium", "message": "不含税金额格式需要人工确认"})
    invoice_code = str(fields.get("invoice_code") or "").strip()
    if invoice_code and not re.fullmatch(r"[0-9A-Za-z-]{8,30}", invoice_code):
        checks.append({"level": "medium", "message": "发票代码格式需要人工确认"})

    official_required = {
        "invoice_number": "发票号码",
        "issue_date": "开票日期",
        "amount_excluding_tax": "开具金额（不含税）",
    }
    official_missing = [label for key, label in official_required.items() if not str(field_values.get(key) or "").strip()]
    official_ready = not official_missing and not any(
        message in {"开票日期格式需要人工确认", "不含税金额格式需要人工确认"}
        for message in (item["message"] for item in checks)
    )
    status = "high_risk" if any(item["level"] == "high" for item in checks) else "review" if checks else "passed"
    return {
        "status": status,
        "checks": checks,
        "missing_fields": missing,
        "official_ready": official_ready,
        "official_missing_fields": official_missing,
    }


def _read_invoice_text(data: bytes, suffix: str) -> tuple[str, str]:
    extension = suffix.lower().lstrip(".") or "png"
    if extension == "pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(data)
        try:
            native_pages: list[str] = []
            try:
                with pdfplumber.open(str(temp_path)) as pdf:
                    page_count = min(len(pdf.pages), 10)
                    for page in pdf.pages[:page_count]:
                        page_text = (page.extract_text() or "").strip()
                        if page_text:
                            native_pages.append(page_text)
            except Exception:
                page_count = 10

            native_text = "\n".join(native_pages)
            if _is_non_tax_receipt(native_text):
                receipt = _extract_non_tax_receipt_fields(native_text)
                if receipt["receipt_number"] and receipt["issue_date"] and receipt["total_amount"]:
                    return native_text, "pdf"
            # Text PDFs can skip PaddleOCR entirely. If the embedded text is
            # incomplete, OCR is used as a supplement instead of replacing it.
            native_fields = knowledge_service._extract_invoice_fields(native_text) if native_text.strip() else {}  # type: ignore[attr-defined]
            native_ready = bool(
                native_fields.get("invoice_number")
                and native_fields.get("issue_date")
                and (
                    native_fields.get("amount_excluding_tax")
                    or (native_fields.get("total_amount") and native_fields.get("tax_amount"))
                )
            )
            if native_ready:
                return native_text, "pdf"

            ocr_pages: list[str] = []
            for page_index in range(page_count):
                text = knowledge_service._ocr_pdf_page(temp_path, page_index)  # type: ignore[attr-defined]
                if text:
                    ocr_pages.append(text)
            combined = "\n".join(item for item in (native_text, "\n".join(ocr_pages)) if item.strip())
            return combined, "pdf"
        finally:
            temp_path.unlink(missing_ok=True)
    return knowledge_service._ocr_image_bytes(data, extension), "image"


@router.post("/ocr")
async def ocr_invoice(file: UploadFile = File(...)):
    filename = file.filename or "invoice"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf"}:
        return _error("仅支持 JPG、PNG、WEBP、BMP 或 PDF 发票文件")
    data = await file.read()
    if not data:
        return _error("上传文件为空")
    if len(data) > 20 * 1024 * 1024:
        return _error("文件不能超过 20MB")

    try:
        text, source_type = _read_invoice_text(data, suffix)
        if _is_non_tax_receipt(text):
            receipt_fields = _extract_non_tax_receipt_fields(text)
            ocr_available = bool(text.strip())
            return _ok(
                {
                    "file_name": filename,
                    "source_type": source_type,
                    "ocr_text": text,
                    "document_type": "non_tax_receipt",
                    "receipt_fields": receipt_fields,
                    "fields": {},
                    "local_verification": {
                        "status": "review",
                        "checks": [{"level": "medium", "message": "该文件是非税收入票据，需按票据所属渠道核验"}],
                        "missing_fields": [],
                        "official_ready": False,
                        "official_missing_fields": [],
                    },
                    "status": "ocr_completed" if ocr_available else "ocr_unavailable",
                    "external_verification": {
                        "status": "manual_pending",
                        "message": "请在财政部全国财政电子票据查验平台输入票据字段并完成查验。",
                        "platform": "财政部全国财政电子票据查验平台",
                        "platform_url": "https://pjcy.mof.gov.cn/",
                        "official_ready": False,
                        "official_missing_fields": [],
                    },
                    "official_verification_payload": {
                        "invoice_code": "", "invoice_number": "", "issue_date": "", "amount_excluding_tax": "",
                    },
                },
                "非税收入票据已识别；请使用票据所属渠道核验" if ocr_available else "票据内容无法识别",
            )
        fields = knowledge_service._extract_invoice_fields(text)  # type: ignore[attr-defined]
        checks = _validate_invoice(fields)
        ocr_available = bool(text.strip())
        issue_date = str(fields.get("issue_date") or "")
        amount_excluding_tax = str(fields.get("amount_excluding_tax") or "")
        official_ready = bool(checks.get("official_ready"))
        official_missing = list(checks.get("official_missing_fields") or [])
        official_message = (
            "已准备好官方查验所需字段，请打开国家税务总局平台并手动输入验证码。"
            if official_ready
            else f"还缺少官方查验字段：{'、'.join(official_missing)}。补充后再打开官方平台。"
        )
        return _ok(
            {
                "file_name": filename,
                "source_type": source_type,
                "ocr_text": text,
                "document_type": "vat_invoice",
                "fields": fields,
                "local_verification": checks,
                "status": "ocr_completed" if ocr_available else "ocr_unavailable",
                "external_verification": {
                    "status": "manual_pending",
                    "message": official_message,
                    "platform": "国家税务总局全国增值税发票查验平台",
                    "platform_url": OFFICIAL_PLATFORM_URL,
                    "official_ready": official_ready,
                    "official_missing_fields": official_missing,
                },
                "official_verification_payload": {
                    "invoice_code": str(fields.get("invoice_code") or ""),
                    "invoice_number": str(fields.get("invoice_number") or ""),
                    "issue_date": issue_date.replace("-", ""),
                    "amount_excluding_tax": amount_excluding_tax,
                },
            },
            "发票 OCR 识别完成" if ocr_available else "OCR 当前不可用，请检查 PaddleOCR 环境",
        )
    except Exception as exc:  # pragma: no cover - defensive upload boundary
        return _error(f"发票识别失败：{exc}", 500)
