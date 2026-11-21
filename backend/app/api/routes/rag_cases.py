from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.services import rag_case_service


router = APIRouter(prefix="/rag")
bid_router = APIRouter(prefix="/bid")


class RagIngestRequest(BaseModel):
    folder_path: str | None = None
    # Incremental indexing is the safe default; pass true only after source
    # PDFs have been replaced and a full rebuild is explicitly required.
    force: bool = False


class RagScoringContextRequest(BaseModel):
    query: str = ""
    bid_type: str = "open"
    limit: int = 6


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: rag_case_service.RagCaseServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": exc.message, "data": None})


@router.get("/cases/scan")
def scan_rag_cases(folder_path: str | None = Query(default=None)):
    try:
        return _ok(rag_case_service.scan_case_folder(folder_path))
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@router.post("/cases/ingest")
def ingest_rag_cases(payload: RagIngestRequest | None = None):
    body = payload or RagIngestRequest()
    try:
        return _ok(rag_case_service.ingest_case_folder(body.folder_path, force=body.force), "历史案例库已入库")
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@router.get("/cases")
def list_rag_cases():
    return _ok(rag_case_service.list_cases())


@router.get("/cases/{case_id}")
def get_rag_case_detail(case_id: int):
    try:
        return _ok(rag_case_service.get_case_detail(case_id))
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@router.get("/cases/{case_id}/documents/{document_id}")
def download_rag_case_document(case_id: int, document_id: int, proposal_only: bool = Query(default=False)):
    try:
        data = rag_case_service.get_case_document(case_id, document_id, proposal_only=proposal_only)
        path = data["absolute_path"]
        filename = str(data.get("file_name") or "case.pdf")
        return FileResponse(path, filename=filename, media_type="application/pdf")
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@router.get("/cases/{case_id}/documents/{document_id}/pages/{page_number}.png")
def get_rag_case_document_page_image(
    case_id: int,
    document_id: int,
    page_number: int,
    terms: str = Query(default=""),
):
    try:
        data = rag_case_service.render_case_document_page(case_id, document_id, page_number, terms=terms)
        headers = {"Cache-Control": "private, max-age=300"}
        return StreamingResponse(data["stream"], media_type="image/png", headers=headers)
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@bid_router.get("/{bid_id}/rag/similar-cases")
def get_bid_similar_cases(bid_id: int, limit: int = Query(default=5, ge=1, le=10)):
    try:
        return _ok(rag_case_service.retrieve_similar_cases_for_bid(bid_id, limit=limit))
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@bid_router.get("/{bid_id}/rag/cases/{case_id}/comparison-report")
async def get_bid_case_comparison_report(bid_id: int, case_id: int, limit: int = Query(default=8, ge=1, le=12)):
    try:
        return _ok(await rag_case_service.generate_case_comparison_report(bid_id, case_id, limit=limit))
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)


@bid_router.post("/{bid_id}/rag/scoring-context")
def get_bid_scoring_context(bid_id: int, payload: RagScoringContextRequest | None = None):
    body = payload or RagScoringContextRequest()
    try:
        data: dict[str, Any] = rag_case_service.retrieve_scoring_context(
            bid_id,
            body.query,
            body.bid_type,
            limit=max(1, min(10, body.limit)),
        )
        return _ok(data)
    except rag_case_service.RagCaseServiceError as exc:
        return _error(exc)
