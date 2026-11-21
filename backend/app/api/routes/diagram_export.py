from __future__ import annotations

import json
import urllib.parse
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.services import diagram_image_service, office_smartart_service


router = APIRouter(prefix="/diagram/export")


class SmartArtWordExportRequest(BaseModel):
    paragraph: str = Field(..., min_length=20, max_length=5000)
    diagram_type: Literal["framework", "flow", "architecture", "timeline", "organization", "smartart", "smartart_layer", "cycle", "matrix"] = "smartart"
    template_id: str = Field(default="basic_hierarchy", max_length=40)
    style_hint: str = Field(default="商务科技风，温和蓝绿色点缀，圆角卡片，轻微渐变", max_length=120)
    size: Literal["wide", "classic43", "a4", "ultrawide", "square", "portrait"] = "wide"
    font_family: Literal["default_heiti", "source_han_sans", "microsoft_yahei", "simsun", "fangsong"] = "default_heiti"
    style_preset: Literal["tech", "mono", "business", "office", "cartoon", "poster_business", "sticker_cute"] = "tech"
    auto_match: bool = False
    auto_mode: bool = True


@router.get("/capabilities")
async def smartart_export_capabilities():
    env = office_smartart_service.check_office_env()
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "officecli_available": env["available"],
            "officecli": env.get("officecli"),
            "reason": env.get("reason") or "",
            "fallback": "editable_docx_table",
        },
    }


@router.post("/smartart-word")
async def export_smartart_word(payload: SmartArtWordExportRequest):
    try:
        content, filename, meta = await office_smartart_service.build_smartart_export(
            payload.paragraph,
            diagram_type=payload.diagram_type,
            template_id=payload.template_id,
            style_hint=payload.style_hint,
            size=payload.size,
            font_family=payload.font_family,
            style_preset=payload.style_preset,
            auto_match=payload.auto_match,
            auto_mode=payload.auto_mode,
        )
        quoted = urllib.parse.quote(filename)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
                "X-SmartArt-Export-Meta": urllib.parse.quote(json.dumps(meta, ensure_ascii=False)),
            },
        )
    except diagram_image_service.DiagramImageServiceError as exc:
        return JSONResponse(status_code=exc.status_code, content={"code": 50001, "message": exc.message, "data": None})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"code": 50002, "message": f"SmartArt Word 导出失败：{exc}", "data": None})
