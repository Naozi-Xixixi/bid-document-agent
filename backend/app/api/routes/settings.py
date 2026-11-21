from fastapi import APIRouter

from app.schemas.tender import BridgeSettings
from app.services.storage import read_bridge_settings, write_bridge_settings

router = APIRouter()


@router.get("/settings", response_model=BridgeSettings)
async def get_settings():
    return read_bridge_settings()


@router.post("/settings", response_model=BridgeSettings)
async def save_settings(payload: BridgeSettings):
    return write_bridge_settings(payload)

