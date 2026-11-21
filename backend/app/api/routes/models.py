import asyncio
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services import model_preferences
from app.services.qwen_client import qwen_api_key, deepseek_api_key
from app.services.storage import read_bridge_settings

router = APIRouter()


def enabled_models():
    result = []
    for provider in read_bridge_settings().providers:
        if not provider.enabled:
            continue
        for model in provider.models:
            if model.enabled:
                result.append({"id": model.id, "name": model.name, "provider": provider.name})
    return result or [{"id": "mock-bid-agent", "name": "Mock Bid Agent", "provider": "local"}]


@router.get("/models")
@router.get("/chat/models")
async def get_models():
    return {"success": True, "models": enabled_models()}


class SceneModelSelection(BaseModel):
    model: str | None = None


@router.get("/models/scenes")
def get_model_scenes():
    return {"success": True, "scenes": model_preferences.list_selections()}


@router.put("/models/scenes/{scene}")
async def set_model_scene(scene: str, payload: SceneModelSelection, request: Request):
    user = getattr(request.state, "auth_user", {})
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有公司管理员可以修改模型配置")
    try:
        model = model_preferences.save_selection(scene, payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "scene": scene, "model": model}


@router.get("/models/qwen-catalog")
async def get_qwen_catalog():
    """Proxy the official DashScope model catalog without exposing the API key."""
    key = qwen_api_key()
    if not key:
        raise HTTPException(status_code=503, detail="尚未配置千问 API Key")
    parsed = urlsplit(settings.qwen_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=503, detail="千问服务地址需使用 HTTPS")
    url = f"{parsed.scheme}://{parsed.netloc}/api/v1/models"
    found: dict[str, dict] = {}
    try:
        async with asyncio.timeout(18):
            async with httpx.AsyncClient(timeout=15) as client:
                for provider in ("qwen", "qwen-domain-model"):
                    for page in range(1, 21):
                        response = await client.get(
                            url,
                            headers={"Authorization": f"Bearer {key}"},
                            params={"providers": provider, "page_no": page, "page_size": 100, "language": "zh-CN"},
                        )
                        response.raise_for_status()
                        output = response.json().get("output") or {}
                        rows = output.get("models") or []
                        for item in rows:
                            model_id = str(item.get("model") or "").strip()
                            if model_id:
                                found[model_id] = {
                                    "id": model_id,
                                    "name": str(item.get("name") or model_id),
                                    "capabilities": item.get("capabilities") or [],
                                }
                        if not rows or page * 100 >= int(output.get("total") or 0):
                            break
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="千问模型列表获取超时，请检查网络或稍后重试") from exc
    except (httpx.HTTPError, ValueError) as exc:
        # Do not return provider response bodies; some gateways echo credentials.
        raise HTTPException(status_code=502, detail=f"千问模型列表获取失败：{type(exc).__name__}") from exc
    return {"success": True, "models": sorted(found.values(), key=lambda item: item["id"])}



@router.get("/models/deepseek-catalog")
async def get_deepseek_catalog():
    key = deepseek_api_key()
    if not key:
        raise HTTPException(status_code=503, detail="尚未配置 DEEPSEEK_API_KEY，请在后端配置文件中添加")
    base = settings.deepseek_base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(status_code=503, detail="DeepSeek 服务地址需使用 HTTPS")
    try:
        async with asyncio.timeout(18):
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(base + "/models", headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                rows = response.json().get("data")
                if not isinstance(rows, list):
                    raise ValueError("invalid model list")
                models = []
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("id") or "").strip()
                    if model_id and model_preferences._MODEL_ID.fullmatch("deepseek/" + model_id):
                        models.append({"id": "deepseek/" + model_id, "name": str(item.get("name") or model_id), "capabilities": ["TG"]})
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="DeepSeek 模型列表获取超时，请检查网络或稍后重试") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            detail = "DeepSeek 密钥无效或账号无权访问，请检查 API Key 和账号状态"
        elif code == 429:
            detail = "DeepSeek 请求过于频繁，请稍后重试"
        else:
            detail = f"DeepSeek 服务返回 HTTP {code}，请稍后重试"
        raise HTTPException(status_code=502, detail=detail) from exc
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail=f"DeepSeek 模型列表获取失败：{type(exc).__name__}") from exc
    return {"success": True, "models": sorted(models, key=lambda item: item["id"])}
