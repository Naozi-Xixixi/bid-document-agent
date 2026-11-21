from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Run(BaseModel):
    """WordAgent-style text or image run."""

    text: str | None = None
    rStyle: str | None = None
    url: str | None = None
    width: float | None = None
    height: float | None = None
    left: float | None = None
    top: float | None = None
    wrapType: str | None = None
    altText: str | None = None


class Paragraph(BaseModel):
    """A paragraph in the ordered WordAgent document stream."""

    pStyle: str = Field(description="Paragraph style id, for example pS_1.")
    runs: list[Run] = Field(default_factory=list)
    paraIndex: int | None = None

    @model_validator(mode="after")
    def require_style(self):
        if not self.pStyle.strip():
            raise ValueError("pStyle cannot be empty")
        return self


class CellParagraph(BaseModel):
    text: str = ""
    pStyle: str
    rStyle: str | None = None
    runs: list[Run] = Field(default_factory=list)


class Cell(BaseModel):
    text: str = ""
    paragraphs: list[CellParagraph] | None = None
    rStyle: str | None = None
    cStyle: str


class Table(BaseModel):
    rows: int
    columns: int
    cells: list[list[Cell]]
    tStyle: str
    columnWidths: list[float] | None = None
    rowHeights: list[list[int | float]] | None = None


class TableBlock(BaseModel):
    tables: list[Table] = Field(min_length=1)


class DocumentOutput(BaseModel):
    """Copied contract shape from WordAgent: ordered paragraphs plus style map."""

    paragraphs: list[Paragraph | TableBlock]
    styles: dict[str, list]

    @model_validator(mode="before")
    @classmethod
    def normalize_blocks(cls, data):
        if isinstance(data, dict) and "tables" in data:
            raise ValueError('Top-level "tables" is not supported; use paragraph table blocks.')
        if isinstance(data, dict) and "paragraphs" in data:
            cleaned = []
            for block in data["paragraphs"]:
                if isinstance(block, (Paragraph, TableBlock)):
                    cleaned.append(block)
                elif isinstance(block, dict) and "tables" in block:
                    cleaned.append(TableBlock.model_validate(block))
                elif isinstance(block, dict) and "runs" in block:
                    cleaned.append(block)
            return {**data, "paragraphs": cleaned}
        return data

    @model_validator(mode="after")
    def validate_style_refs(self):
        style_keys = set(self.styles)
        missing: set[str] = set()
        for block in self.paragraphs:
            if isinstance(block, Paragraph):
                if block.pStyle not in style_keys:
                    missing.add(block.pStyle)
                for run in block.runs:
                    if run.rStyle and run.rStyle not in style_keys:
                        missing.add(run.rStyle)
            else:
                for table in block.tables:
                    if table.tStyle not in style_keys:
                        missing.add(table.tStyle)
                    for row in table.cells:
                        for cell in row:
                            if cell.cStyle not in style_keys:
                                missing.add(cell.cStyle)
        if missing:
            raise ValueError(f"Missing style definitions: {', '.join(sorted(missing))}")
        return self


class RangeFilter(BaseModel):
    gt: float | None = None
    lt: float | None = None
    gte: float | None = None
    lte: float | None = None


class QueryFilter(BaseModel):
    regex: str | None = None
    regexFlags: str | None = "i"
    fontName: str | None = None
    fontSize: float | RangeFilter | None = None
    bold: bool | None = None
    italic: bool | None = None
    alignment: str | None = None
    styleName: str | None = None


class DocumentQuery(BaseModel):
    type: Literal["run", "paragraph"] = "run"
    filters: QueryFilter


def plain_text_document(title: str, paragraphs: list[str]) -> DocumentOutput:
    """Build a minimal WordAgent-compatible document payload."""

    styles = {
        "pS_title": ["center", 18, 0, 0, 0, 12, 12, "Title", 0],
        "pS_body": ["justify", 18, 0, 0, 24, 0, 6, "Body", 0],
        "rS_title": ["Microsoft YaHei", 18, True, False, 0, "", "#111111", 0, False, False, False],
        "rS_body": ["SimSun", 12, False, False, 0, "", "#111111", 0, False, False, False],
    }
    blocks: list[Paragraph] = [Paragraph(pStyle="pS_title", runs=[Run(text=title, rStyle="rS_title")])]
    blocks.extend(Paragraph(pStyle="pS_body", runs=[Run(text=item, rStyle="rS_body")]) for item in paragraphs)
    return DocumentOutput(paragraphs=blocks, styles=styles)

