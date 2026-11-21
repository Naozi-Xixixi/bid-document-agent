from __future__ import annotations

from pydantic import BaseModel, Field


class MermaidGenerateRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=6000)
    chart_type: str = Field(default="flowchart", max_length=40)
    chart_title: str | None = Field(default=None, max_length=120)


class MermaidRenderRequest(BaseModel):
    chart_title: str = Field(default="标书图表", max_length=120)
    chart_type: str = Field(default="flowchart", max_length=40)
    mermaid_code: str = Field(min_length=1, max_length=12000)
    source_text: str | None = Field(default=None, max_length=6000)
    chart_image_data: str | None = None
    save_to_rag: bool = False


class MermaidSaveToRagRequest(BaseModel):
    asset_id: int


class GraphicSectionGenerateRequest(BaseModel):
    bidder_name: str = Field(default="", max_length=200)
    chapter_id: int
    chapter_ids: list[int] = Field(default_factory=list, max_length=500)
    word_limit: int = Field(default=1200, ge=200, le=8000)
    length_target: int | None = Field(default=None, ge=1, le=500000)
    length_mode: str = Field(default="default", pattern="^(shrink|expand|default)$")
    rag_enable: bool = True
    image_strategy: str = Field(default="rag_auto", pattern="^(none|rag_auto|project_only|valid_only|category_only)$")
    asset_sub_type: list[str] = Field(default_factory=list, max_length=24)
    priority_expire: bool = False
    exclude_ids: list[int] = Field(default_factory=list, max_length=2000)
    chapter_theme: str | None = Field(default=None, max_length=800)
    chart_strategy: str | None = Field(default=None, pattern="^(none|rag_auto|project_first)$")
    format_template_id: str | None = Field(default=None, max_length=60)
    selected_asset_ids: list[int] = Field(default_factory=list, max_length=2000)
    selected_chunk_ids: list[int] = Field(default_factory=list, max_length=2000)
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)


class ChapterMaterialSelectionRequest(BaseModel):
    selected_asset_ids: list[int] = Field(default_factory=list, max_length=2000)
    selected_chunk_ids: list[int] = Field(default_factory=list, max_length=2000)
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)


class ChapterMaterialMatchAllRequest(BaseModel):
    chapter_ids: list[int] = Field(default_factory=list, max_length=500)
    bid_scenario: str | None = Field(default=None, max_length=20)
    length_level: str | None = Field(default=None, max_length=20)


class GraphicSectionSaveRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)
    chapter_id: int
    markdown: str = Field(default="", max_length=80000)
    ref_chart_ids: list[int] = Field(default_factory=list)
    ref_image_asset_ids: list[int] = Field(default_factory=list)
    format_template_id: str | None = Field(default=None, max_length=60)


class TableFillRequest(BaseModel):
    table_type: str = Field(pattern="^(qualification|personnel|vehicle|equipment|performance|financial)$")
    embed_images: bool = False
    asset_ids: list[int] = Field(default_factory=list, max_length=2000)


class AssetVerifyRequest(BaseModel):
    asset_ids: list[int] = Field(default_factory=list, max_length=2000)
