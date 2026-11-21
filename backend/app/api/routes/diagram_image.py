from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import diagram_image_service


router = APIRouter(prefix="/image")


class DiagramImageGenerateRequest(BaseModel):
    paragraph: str = Field(..., min_length=20, max_length=5000)
    diagram_type: Literal["framework", "flow", "architecture", "timeline", "organization", "smartart", "smartart_layer", "cycle", "matrix", "swimlane", "platform_layer"] = "framework"
    template_id: str = Field(default="basic_hierarchy", max_length=40)
    style_hint: str = Field(default="商务科技风，温和蓝绿色点缀，圆角卡片，轻微渐变", max_length=120)
    size: Literal["wide", "classic43", "a4", "ultrawide", "square", "portrait"] = "wide"
    font_family: Literal["default_heiti", "source_han_sans", "microsoft_yahei", "simsun", "fangsong"] = "default_heiti"
    style_preset: Literal["tech", "mono", "business", "office", "cartoon", "poster_business", "sticker_cute", "bid_process"] = "tech"
    auto_match: bool = False
    auto_mode: bool = True


@router.post("/diagram/generate")
async def generate_diagram_image(payload: DiagramImageGenerateRequest):
    try:
        data = await diagram_image_service.generate_diagram_image(
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
        # raw provider payload can be large/noisy; keep API response compact.
        data.pop("raw", None)
        return {"code": 0, "message": "success", "data": data}
    except diagram_image_service.DiagramImageServiceError as exc:
        return JSONResponse(status_code=exc.status_code, content={"code": 50001, "message": exc.message, "data": None})
