from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"success": True, "app": settings.app_name, "version": settings.app_version}


@router.get("/version")
async def version():
    return {"version": settings.app_version}

