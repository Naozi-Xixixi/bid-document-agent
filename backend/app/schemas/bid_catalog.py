from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Any


class CatalogChapterUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_title: str = Field(min_length=1, max_length=200, validation_alias=AliasChoices("chapter_title", "chapterTitle"))


class CatalogChapterActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    instruction: str = ""


class CatalogChapterCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: int = Field(validation_alias=AliasChoices("version_id", "versionId"))
    parent_id: int = Field(default=0, validation_alias=AliasChoices("parent_id", "parentId"))
    chapter_title: str = Field(min_length=1, max_length=200, validation_alias=AliasChoices("chapter_title", "chapterTitle"))
    level: int = Field(ge=1, le=3)
    sort_order: int | None = Field(default=None, validation_alias=AliasChoices("sort_order", "sortOrder"))


class CatalogChapterMoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    move_type: str = Field(validation_alias=AliasChoices("move_type", "moveType"))


class CatalogSetFinalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: int = Field(validation_alias=AliasChoices("version_id", "versionId"))


class CatalogVersionTreeUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    catalog_content: list[dict[str, Any]] = Field(validation_alias=AliasChoices("catalog_content", "catalogContent"))


class CatalogRegenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    style: str | None = None


class CatalogWordCountUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_word_count: int = Field(ge=300, validation_alias=AliasChoices("target_word_count", "targetWordCount"))
