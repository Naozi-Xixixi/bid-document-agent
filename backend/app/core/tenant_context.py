from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TenantContext:
    user_id: int
    username: str
    role: str
    company_id: str
    company_name: str
    database_file: Path
    upload_dir: Path


_CURRENT_TENANT: ContextVar[TenantContext | None] = ContextVar("current_tenant", default=None)


def get_current_tenant() -> TenantContext | None:
    return _CURRENT_TENANT.get()


def set_current_tenant(context: TenantContext) -> Token[TenantContext | None]:
    return _CURRENT_TENANT.set(context)


def reset_current_tenant(token: Token[TenantContext | None]) -> None:
    _CURRENT_TENANT.reset(token)
