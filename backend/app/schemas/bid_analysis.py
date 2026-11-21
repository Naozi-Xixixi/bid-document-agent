from __future__ import annotations

from pydantic import BaseModel, Field


class AnalysisStartRequest(BaseModel):
    check_items: list[str] = Field(default_factory=list)


class AnalysisResolveRequest(BaseModel):
    resolved: bool = True

