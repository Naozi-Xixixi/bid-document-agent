from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


ParseMode = Literal["auto_upload", "paste_text"]
BidCategory = Literal["service", "goods", "engineering", "other"]
ParseStatus = Literal["pending", "processing", "completed", "failed"]
MaterialType = Literal["template", "writing_data"]


class BidParseInfoUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    parse_mode: ParseMode | None = Field(default=None, validation_alias=AliasChoices("parse_mode", "parseMode"))
    paste_content: str | None = Field(default=None, validation_alias=AliasChoices("paste_content", "pasteContent"))
    bid_category: BidCategory = Field(default="service", validation_alias=AliasChoices("bid_category", "bidCategory"))


class PasteParseContentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_overview: str | None = Field(default=None, validation_alias=AliasChoices("project_overview", "projectOverview"))
    score_requirements: str | None = Field(default=None, validation_alias=AliasChoices("score_requirements", "scoreRequirements"))


class ParseResultEditRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_overview: str | None = Field(default=None, validation_alias=AliasChoices("project_overview", "projectOverview"))
    score_requirements: str | None = Field(default=None, validation_alias=AliasChoices("score_requirements", "scoreRequirements"))


class ApiEnvelope(BaseModel):
    code: int = 0
    message: str = "success"
    data: object | None = None
