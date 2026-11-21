from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class KnowledgeItemUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    item_type: str | None = Field(default=None, validation_alias=AliasChoices("item_type", "type"))
    industry: str | None = None
    region: str | None = None
    year: int | None = None
    project_type: str | None = Field(default=None, validation_alias=AliasChoices("project_type", "projectType"))
    tags: list[str] | None = None
    valid_until: str | None = Field(default=None, validation_alias=AliasChoices("valid_until", "validUntil"))
    status: str | None = None


class KnowledgeReuseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bid_id: int = Field(validation_alias=AliasChoices("bid_id", "bidId"))
    chapter_id: int = Field(validation_alias=AliasChoices("chapter_id", "chapterId"))
    insert_mode: str = Field(default="append", validation_alias=AliasChoices("insert_mode", "insertMode"))

