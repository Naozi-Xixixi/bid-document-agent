from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services import ppt_service

router = APIRouter(prefix="/bid")


class PptGenerateRequest(BaseModel):
    version_id: int | None = None
    chapter_ids: list[int] = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=160)
    subtitle: str = Field(default="", max_length=200)
    theme: str = Field(default="green", pattern="^(green|blue)$")
    max_slides_per_chapter: int = Field(default=3, ge=1, le=4)
    include_images: bool = True


@router.get("/{bid_id}/ppt/source")
async def get_ppt_source(bid_id: int, version_id: int | None = Query(default=None)):
    try:
        return {"code": 0, "message": "success", "data": await asyncio.to_thread(ppt_service.get_source, bid_id, version_id)}
    except ppt_service.PptServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{bid_id}/ppt/generate")
async def generate_ppt(bid_id: int, payload: PptGenerateRequest):
    try:
        content, filename = await asyncio.to_thread(
            ppt_service.generate_pptx,
            bid_id,
            version_id=payload.version_id,
            chapter_ids=payload.chapter_ids,
            title=payload.title,
            subtitle=payload.subtitle,
            theme_name=payload.theme,
            max_slides_per_chapter=payload.max_slides_per_chapter,
            include_images=payload.include_images,
        )
    except ppt_service.PptServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "private, no-store"},
    )
