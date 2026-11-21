"""Keep a project alive for the complete ASGI request, including SSE/background work."""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from collections.abc import Iterator

from starlette.responses import JSONResponse
from app.core.config import settings
from app.core.tenant_context import get_current_tenant

_LOCK = threading.RLock()
_ACTIVE: dict[tuple[str, str, str], int] = {}
_DELETING: set[tuple[str, str, str]] = set()


class ActivityConflict(Exception):
    pass


def _key(kind: str, value: str | int) -> tuple[str, str, str]:
    tenant = get_current_tenant()
    return (tenant.company_id if tenant else "company_default", kind, str(value))


@contextmanager
def begin_deletion(project_id: str, bid_id: int | None) -> Iterator[None]:
    keys = {_key("project", project_id)}
    if bid_id is not None:
        keys.add(_key("bid", bid_id))
    with _LOCK:
        if any(key in _DELETING or _ACTIVE.get(key, 0) for key in keys):
            raise ActivityConflict("项目正在处理请求、解析或生成，请等待完成或停止任务后再删除")
        _DELETING.update(keys)
    try:
        yield
    finally:
        with _LOCK:
            _DELETING.difference_update(keys)


def _acquire_request(keys: set[tuple[str, str, str]]) -> None:
    with _LOCK:
        if any(key in _DELETING for key in keys):
            raise ActivityConflict("项目正在删除，请稍后刷新")
        for key in keys:
            _ACTIVE[key] = _ACTIVE.get(key, 0) + 1


def _release_request(keys: set[tuple[str, str, str]]) -> None:
    with _LOCK:
        for key in keys:
            remaining = _ACTIVE.get(key, 1) - 1
            if remaining:
                _ACTIVE[key] = remaining
            else:
                _ACTIVE.pop(key, None)


@contextmanager
def project_request(project_id: str) -> Iterator[None]:
    """Lease a body-addressed project for all awaited work or stream iteration."""
    keys = {_key("project", project_id)}
    _acquire_request(keys)
    try:
        yield
    finally:
        _release_request(keys)


class ProjectActivityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        match = re.fullmatch(re.escape(settings.api_prefix.rstrip("/")) + r"/(bid|projects)/([^/]+)(/.*)?", path)
        if not match or (match[1] == "projects" and scope["method"] == "DELETE" and not match[3]):
            return await self.app(scope, receive, send)
        kind = "bid" if match[1] == "bid" else "project"
        value = match[2]
        if kind == "bid":
            try:
                value = str(int(value))
            except ValueError:
                return await self.app(scope, receive, send)
        keys = {_key(kind, value)}
        if kind == "bid":
            # Merging retains the source upload path. Protect both projects so
            # the source file cannot disappear before its new reference commits.
            source = re.fullmatch(r"/merge-parse/([^/]+)/?", match[3] or "")
            if source:
                try:
                    keys.add(_key("bid", int(source[1])))
                except ValueError:
                    pass  # The route will reject a malformed integer.
        try:
            _acquire_request(keys)
        except ActivityConflict as exc:
            return await JSONResponse({"detail": str(exc)}, status_code=409)(scope, receive, send)
        try:
            # Unlike BaseHTTPMiddleware.call_next, this includes the stream and
            # response background tasks; a disconnect also releases the lease.
            return await self.app(scope, receive, send)
        finally:
            _release_request(keys)
