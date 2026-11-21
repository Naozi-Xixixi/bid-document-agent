from __future__ import annotations

from pydantic import BaseModel, Field


class AuditStartRequest(BaseModel):
    audit_id: int
    checks: list[str] = Field(default_factory=lambda: ["rejection", "scoring", "qualification", "format", "business"])


class AuditUploadResponse(BaseModel):
    audit_id: int
    file_name: str
    file_size: int
    role: str
    status: str

