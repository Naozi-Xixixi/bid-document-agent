from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    parent_id: int | None = None
    sort_order: int = 0


class KnowledgeCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    parent_id: int | None = None
    sort_order: int | None = None


class KnowledgeItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    category_id: int | None = None
    tags: list[str] | None = None
    valid_until: str | None = None
    status: str | None = None



class KnowledgeEvidenceReview(BaseModel):
    verified: bool
    subject: str = Field(default="", max_length=200)
    identifier: str = Field(default="", max_length=180)
    valid_until: date | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    category_id: int | None = None
    project_id: int | None = Field(default=None, ge=1)
    item_id: int | None = Field(default=None, ge=1)
    doc_role: str | None = Field(default=None, pattern="^(tender|bid|knowledge)$")
    chunk_kind: list[str] = Field(default_factory=list, max_length=8)
    image_category: str | None = None
    asset_sub_type: list[str] = Field(default_factory=list, max_length=24)
    priority_expire: bool = False
    exclude_ids: list[int] = Field(default_factory=list, max_length=2000)
    chapter_theme: str | None = Field(default=None, max_length=800)
    bid_type: str | None = Field(default=None, max_length=20)
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)
    include_images: bool = False
    include_expired: bool = False
    limit: int = Field(default=8, ge=1, le=50)


class KnowledgeBatchRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1)
    action: str = Field(pattern="^(delete|move|expire|restore)$")
    category_id: int | None = None


class KnowledgeLocalImportRequest(BaseModel):
    dir_path: str | None = None


class KnowledgeAssetAnnotationUpdate(BaseModel):
    annotation: str = Field(default="", max_length=2400)


class KnowledgeAssetMetadataUpdate(BaseModel):
    bid_type: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)
    extend_tags: list[str] | None = None


class KnowledgeAssetDimensionsUpdate(BaseModel):
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)
    dimension_tags: dict[str, Any] | None = None


class KnowledgeAssetBatchTagRequest(BaseModel):
    category: str | None = Field(default=None, max_length=80)
    asset_sub_type: str | None = Field(default=None, max_length=80)
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)
    source: str | None = Field(default=None, max_length=200)
    source_from: str | None = Field(default=None, max_length=40)
    source_to: str | None = Field(default=None, max_length=40)
    item_ids: list[int] | None = None
    asset_ids: list[int] | None = None
    overwrite_manual: bool = False
    only_missing: bool = True
    limit: int = Field(default=2000, ge=1, le=10000)


class KnowledgeAssetAnnotationRegenerateRequest(BaseModel):
    asset_ids: list[int] | None = None
    only_missing: bool = True
    limit: int = Field(default=120, ge=1, le=1000)


class KnowledgeAssetReextractBatchRequest(BaseModel):
    item_ids: list[int] | None = None
    asset_ids: list[int] | None = None
    asset_sub_type: str | None = Field(default=None, max_length=80)
    only_missing: bool = True
    limit: int = Field(default=200, ge=1, le=2000)


class KnowledgeDeepExtractBatchRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=500)


class KnowledgeProjectRenameRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=160)


class KnowledgeProjectMatchRequest(BaseModel):
    min_score: float = Field(default=0.08, ge=0, le=1)
    limit: int = Field(default=500, ge=1, le=5000)


class KnowledgeProjectReuseRequest(BaseModel):
    target_bid_id: int | None = None
    target_project_id: int | None = None
    map_ids: list[int] = Field(default_factory=list, max_length=500)
