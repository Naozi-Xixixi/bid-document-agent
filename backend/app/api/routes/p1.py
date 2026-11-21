from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from app.services import p1_service


router = APIRouter(prefix="/v1")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: p1_service.P1ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


@router.get("/health")
async def health():
    return _ok({"status": "ok", "module": "p1", "version": "0.1.0"})


@router.get("/projects")
async def list_projects():
    try:
        return _ok(p1_service.list_projects())
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects")
async def create_project(payload: dict):
    try:
        return JSONResponse(status_code=201, content=_ok(p1_service.create_project(payload), "项目已创建"))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        return _ok(p1_service.get_project(project_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/tender-documents")
async def upload_tender_document(project_id: str, file: UploadFile = File(...)):
    try:
        return JSONResponse(status_code=202, content=_ok(await p1_service.upload_tender_document(project_id, file), "解读任务已完成"))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/tender-documents/{doc_id}/parse-result")
async def get_parse_result(doc_id: str):
    try:
        return _ok(p1_service.get_parse_result(doc_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/tender-documents/{doc_id}/qualification-match")
async def qualification_match(doc_id: str):
    try:
        return _ok(p1_service.qualification_match(doc_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/outline")
async def generate_outline(project_id: str):
    try:
        return _ok(await p1_service.generate_outline_with_ai(project_id), "大纲已生成")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.put("/projects/{project_id}/outline")
async def save_outline(project_id: str, payload: dict):
    try:
        return _ok(p1_service.save_outline(project_id, payload.get("nodes") or []), "大纲已保存")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/knowledge-items")
async def search_knowledge_items(
    q: str = Query(default=""),
    industry: str = Query(default=""),
    year: int | None = Query(default=None),
    type: str = Query(default=""),
    project_type: str = Query(default=""),
    include_expired: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        return _ok(
            p1_service.search_knowledge(
                q=q,
                industry=industry,
                year=year,
                item_type=type,
                project_type=project_type,
                include_expired=include_expired,
                limit=limit,
            )
        )
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/knowledge-items")
async def ingest_knowledge_items(
    files: list[UploadFile] = File(...),
    type: str = Form(default="proposal"),
    industry: str = Form(default=""),
    region: str = Form(default=""),
    year: int | None = Form(default=None),
    project_type: str = Form(default=""),
    tags: str = Form(default=""),
    valid_until: str | None = Form(default=None),
):
    try:
        payload = {"type": type, "industry": industry, "region": region, "year": year, "project_type": project_type, "tags": tags, "valid_until": valid_until}
        result = await p1_service.ingest_knowledge(files, payload)
        status = 207 if result["failure_count"] else 201
        return JSONResponse(status_code=status, content=_ok(result, "知识库入库完成"))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/knowledge-items/{item_id}")
async def get_knowledge_item(item_id: str):
    try:
        return _ok(p1_service.get_knowledge_item(item_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/knowledge-items/{item_id}/reuse")
async def reuse_knowledge_item(item_id: str, payload: dict):
    try:
        return _ok(p1_service.reuse_knowledge(item_id, payload), "已复用到指定大纲节点")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/proposal/generate")
async def generate_proposal(project_id: str):
    try:
        return JSONResponse(status_code=202, content=_ok(p1_service.generate_proposal(project_id), "标书正文已生成"))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/projects/{project_id}/proposal/sections")
async def list_sections(project_id: str):
    try:
        return _ok(p1_service.list_sections(project_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/sections/{section_id}")
async def get_section(section_id: str):
    try:
        return _ok(p1_service.get_section(section_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.put("/sections/{section_id}")
async def save_section(section_id: str, payload: dict):
    try:
        return _ok(p1_service.save_section(section_id, payload), "章节已保存")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/sections/{section_id}/regenerate")
async def regenerate_section(section_id: str):
    try:
        return JSONResponse(status_code=202, content=_ok(p1_service.regenerate_section(section_id), "章节重生成完成"))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.put("/sections/{section_id}/annotations")
async def save_annotations(section_id: str, payload: dict):
    try:
        section = p1_service.get_section(section_id)
        return _ok({"section": section, "annotations": payload.get("annotations") or []}, "图片标注已记录")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/proposal/export")
async def export_proposal(project_id: str, format: str = Query(default="docx")):
    try:
        if format != "docx":
            raise p1_service.P1ServiceError(40024, "当前 P1 薄版本先支持 DOCX 导出，PDF 将接入 LibreOffice 后开放")
        filename, content = p1_service.export_docx(project_id)
        encoded = quote(filename)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
        )
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/review")
async def review_project(project_id: str):
    try:
        return _ok(p1_service.review_project(project_id), "审核完成")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/projects/{project_id}/review")
async def list_findings(project_id: str):
    try:
        return _ok(p1_service.list_findings(project_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.patch("/audit-findings/{finding_id}")
async def update_finding(finding_id: str, payload: dict):
    try:
        return _ok(p1_service.update_finding(finding_id, payload), "问题状态已更新")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    try:
        return _ok(p1_service.get_job(job_id))
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    try:
        job = p1_service.get_job(job_id)
        return _ok({**job, "status": "completed"}, "当前 P1 薄版本任务为同步完成，无需暂停")
    except p1_service.P1ServiceError as exc:
        return _error(exc)


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    try:
        job = p1_service.get_job(job_id)
        return _ok({**job, "status": "completed"}, "当前 P1 薄版本任务已完成")
    except p1_service.P1ServiceError as exc:
        return _error(exc)
