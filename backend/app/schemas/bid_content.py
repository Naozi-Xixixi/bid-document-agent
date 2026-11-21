from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChapterContentSaveRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)
    model_config = ConfigDict(populate_by_name=True)

    content_html: str = Field(default="", validation_alias=AliasChoices("content_html", "contentHtml"))
    content_text: str = Field(default="", validation_alias=AliasChoices("content_text", "contentText"))


class ChapterGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: int | None = Field(default=None, validation_alias=AliasChoices("version_id", "versionId"))
    style: str | None = None


class GenerateAllRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_id: int | None = Field(default=None, validation_alias=AliasChoices("version_id", "versionId"))
    style: str | None = None
