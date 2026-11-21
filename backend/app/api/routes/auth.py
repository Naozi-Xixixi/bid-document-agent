from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import auth_service


router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class CreateCompanyRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)


class UpdateCompanyRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=200)
    display_name: str = Field(default="", max_length=80)
    company_id: str = Field(min_length=1, max_length=120)
    role: str = Field(default="company_user", pattern="^(company_user|admin)$")


class UpdateUserRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    password: str | None = Field(default=None, min_length=6, max_length=200)
    display_name: str | None = Field(default=None, max_length=80)
    company_id: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, pattern="^(company_user|admin)$")


def _envelope(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def _current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "auth_user", None)
    if not isinstance(user, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def _require_admin(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if user.get("role") != "admin" or user.get("company_id") != "company_default":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有系统管理员可以执行此操作")
    return user


def _operation_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    result = auth_service.login(payload.username, payload.password)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误，或账号已停用")
    secure = request.url.scheme == "https"
    response.set_cookie(
        key="wordagent_session",
        value=result["access_token"],
        max_age=max(1, int(settings.auth_session_days or 7)) * 86400,
        httponly=True,
        secure=secure,
        samesite="none" if secure else "lax",
        path="/",
    )
    return _envelope({
        "access_token": result["access_token"],
        "expires_at": result["expires_at"],
        "user": result["user"],
        "company": result["company"],
    })


@router.get("/me")
async def me(request: Request):
    user = _current_user(request)
    company = getattr(request.state, "auth_company", None) or {}
    return _envelope(auth_service.current_identity(user, company))


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = getattr(request.state, "auth_token", None)
    auth_service.logout(token)
    secure = request.url.scheme == "https"
    response.delete_cookie("wordagent_session", path="/", secure=secure, httponly=True, samesite="none" if secure else "lax")
    return _envelope(None, "已退出登录")


@router.get("/admin/companies")
async def admin_companies(request: Request):
    _require_admin(request)
    return _envelope(auth_service.list_companies())


@router.post("/admin/companies")
async def admin_create_company(request: Request, payload: CreateCompanyRequest):
    _require_admin(request)
    try:
        company = auth_service.create_company(payload.company_name)
    except ValueError as exc:
        raise _operation_error(exc) from exc
    return _envelope(company, "公司已创建")


@router.patch("/admin/companies/{company_id}")
async def admin_update_company(request: Request, company_id: str, payload: UpdateCompanyRequest):
    _require_admin(request)
    try:
        company = auth_service.set_company_status(company_id, payload.status)
    except ValueError as exc:
        raise _operation_error(exc) from exc
    return _envelope(company, "公司状态已更新")


@router.get("/admin/users")
async def admin_users(request: Request):
    _require_admin(request)
    return _envelope(auth_service.list_users())


@router.post("/admin/users")
async def admin_create_user(request: Request, payload: CreateUserRequest):
    _require_admin(request)
    try:
        user = auth_service.create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            company_id=payload.company_id,
            role=payload.role,
        )
    except ValueError as exc:
        raise _operation_error(exc) from exc
    return _envelope(user, "账号已创建")


@router.patch("/admin/users/{user_id}")
async def admin_update_user(request: Request, user_id: int, payload: UpdateUserRequest):
    current = _require_admin(request)
    if user_id == int(current["user_id"]):
        if payload.status == "disabled" or payload.role == "company_user" or payload.company_id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能停用、降级或转移当前管理员账号")
    try:
        user = auth_service.update_user(
            user_id,
            status=payload.status,
            password=payload.password,
            display_name=payload.display_name,
            company_id=payload.company_id,
            role=payload.role,
        )
    except ValueError as exc:
        raise _operation_error(exc) from exc
    return _envelope(user, "账号信息已更新")
