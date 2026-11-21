from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.tender import ApiResponse
from app.services import storage

router = APIRouter()


@router.post("/projects/{project_id}/files")
async def upload_project_files(project_id: str, files: list[UploadFile] = File(...)):
    try:
        project = storage.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    saved = [await storage.save_upload(project, item) for item in files]
    return {"success": True, "files": [item.model_dump() for item in saved], "project": project.model_dump()}


@router.post("/upload")
@router.post("/chat/upload")
async def upload_without_project(files: list[UploadFile] = File(...)):
    project = storage.create_project()
    saved = [await storage.save_upload(project, item) for item in files]
    return {"success": True, "projectId": project.id, "files": [item.model_dump() for item in saved]}

