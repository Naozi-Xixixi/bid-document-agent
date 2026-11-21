from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import bid_case_reference_service as service

router = APIRouter(prefix="/bid")


class CaseSelection(BaseModel):
    project_id: int | None = Field(default=None, ge=1)


@router.get("/{bid_id}/case-reference")
def get_case_reference(bid_id: int):
    try:
        return {"code": 0, "message": "success", "data": service.get_selection(bid_id)}
    except service.CaseReferenceError as exc:
        return JSONResponse(status_code=exc.status_code, content={"code": 40001, "message": exc.message, "data": None})


@router.put("/{bid_id}/case-reference")
def set_case_reference(bid_id: int, payload: CaseSelection):
    try:
        return {"code": 0, "message": "success", "data": service.set_selection(bid_id, payload.project_id)}
    except service.CaseReferenceError as exc:
        return JSONResponse(status_code=exc.status_code, content={"code": 40001, "message": exc.message, "data": None})
