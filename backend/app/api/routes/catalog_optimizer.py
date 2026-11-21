from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.services import catalog_optimize_service


router = APIRouter(prefix="/catalog-optimizer")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: catalog_optimize_service.CatalogOptimizeServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": exc.message, "data": None})


@router.post("/optimize")
async def optimize_catalog(
    tender_file: UploadFile = File(...),
    catalog_file: UploadFile | None = File(default=None),
    catalog_text: str = Form(default=""),
):
    try:
        result = await catalog_optimize_service.optimize_catalog_from_uploads(tender_file, catalog_file, catalog_text)
        return _ok(result, "目录优化完成")
    except catalog_optimize_service.CatalogOptimizeServiceError as exc:
        return _error(exc)
