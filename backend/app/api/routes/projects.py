from contextlib import closing

from fastapi import APIRouter, HTTPException

from app.schemas.tender import BidTypeInfo, ProjectCreateRequest, ProjectSettingsUpdateRequest
from app.services import storage
from app.services.project_deletion_service import ProjectDeletionError, delete_project

router = APIRouter(prefix="/projects")


BID_TYPES = [
    BidTypeInfo(
        id="open",
        label="明标",
        description="可以注明投标企业所在公司的资料及其他细节，评审专家知道投标人对应的技术标文件。",
        rules=["允许出现公司名称、资质、业绩、人员等企业识别信息", "适合突出企业实力、经验和综合服务能力"],
    ),
    BidTypeInfo(
        id="blind",
        label="暗标",
        description="技术标不能明示或暗示投标单位，通常采用统一格式、封面和装订方式。",
        rules=["正文应避免公司名称、人员姓名、Logo、专属案例等可识别信息", "后续生成与检查环节应优先执行匿名化规则"],
    ),
]


def _load_project_or_404(project_id: str):
    try:
        return storage.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bid-types")
async def get_bid_types():
    return {"success": True, "bidTypes": [item.model_dump() for item in BID_TYPES]}


@router.get("")
async def get_projects():
    projects = storage.list_projects()
    with closing(storage.connect()) as conn:
        bid_ids = {row["project_id"]: int(row["bid_id"]) for row in conn.execute("SELECT project_id, bid_id FROM bids")}
    return {"success": True, "projects": [{**item.model_dump(), "bidId": bid_ids.get(item.id)} for item in projects]}


@router.post("")
async def create_project(payload: ProjectCreateRequest):
    project = storage.create_project(payload.settings)
    bid_id = storage.get_bid_id(project.id)
    # 新项目直接进入“招标文件解读”步骤，省略原系统的“标书设置”。
    storage.update_bid_current_step(bid_id, 2)
    return {"success": True, "project": project.model_dump(), "settings": project.settings.model_dump(), "bidId": bid_id}


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = _load_project_or_404(project_id)
    bid_id = storage.get_bid_id(project.id)
    return {"success": True, "project": project.model_dump(), "bidId": bid_id}


@router.get("/{project_id}/settings")
async def get_project_settings(project_id: str):
    project = _load_project_or_404(project_id)
    bid_id = storage.get_bid_id(project.id)
    return {"success": True, "projectId": project.id, "bidId": bid_id, "settings": project.settings.model_dump()}


@router.patch("/{project_id}/settings")
async def update_project_settings(project_id: str, payload: ProjectSettingsUpdateRequest):
    try:
        project = storage.update_project_settings(project_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    bid_id = storage.get_bid_id(project.id)
    return {"success": True, "project": project.model_dump(), "settings": project.settings.model_dump(), "bidId": bid_id}


@router.delete("/{project_id}")
def delete_history_project(project_id: str):
    """Run disk/database work in FastAPI's worker pool with tenant context."""
    try:
        return delete_project(project_id)
    except ProjectDeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
