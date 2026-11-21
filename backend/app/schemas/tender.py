from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .wordagent import DocumentOutput


class ApiResponse(BaseModel):
    success: bool = True
    data: object | None = None
    error: str | None = None


class ProviderModel(BaseModel):
    id: str
    name: str
    provider: str = "local"
    enabled: bool = True


class ProviderConfig(BaseModel):
    name: str
    baseUrl: str = ""
    apiKey: str = ""
    apiType: Literal["openai", "anthropic"] = "openai"
    models: list[ProviderModel] = Field(default_factory=list)
    enabled: bool = True


class BridgeSettings(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)
    defaultModel: str = "qwen3.7-flash"
    defaultProvider: str = "qwen"
    temperature: float = 0.3
    customPrompt: str = ""


class BidSettings(BaseModel):
    bidTitle: str = Field(default="", max_length=200)
    pageCount: int = Field(default=200, ge=0, le=4000)
    wordsPerPage: int = Field(default=800, ge=100, le=2000)
    estimatedWordCount: int = Field(default=160000, ge=0, le=8000000)
    bidType: Literal["open", "blind"] = "open"
    selectedModel: str = "qwen3.7-flash"
    selectedProvider: str = "qwen"


class BidTypeInfo(BaseModel):
    id: Literal["open", "blind"]
    label: str
    description: str
    rules: list[str] = Field(default_factory=list)


class ProjectSettingsUpdateRequest(BaseModel):
    bidTitle: str | None = Field(default=None, max_length=200)
    pageCount: int | None = Field(default=None, ge=0, le=4000)
    wordsPerPage: int | None = Field(default=None, ge=100, le=2000)
    estimatedWordCount: int | None = Field(default=None, ge=0, le=8000000)
    bidType: Literal["open", "blind"] | None = None
    selectedModel: str | None = None
    selectedProvider: str | None = None


class UploadedFileInfo(BaseModel):
    fileId: str
    filename: str
    size: int
    contentType: str
    projectPath: str


class ProjectCreateRequest(BaseModel):
    settings: BidSettings = Field(default_factory=BidSettings)


class ProjectState(BaseModel):
    id: str
    settings: BidSettings
    files: list[UploadedFileInfo] = Field(default_factory=list)
    tenderAnalysis: dict | None = None
    directory: list[dict] = Field(default_factory=list)
    sections: dict[str, dict] = Field(default_factory=dict)
    createdAt: datetime
    updatedAt: datetime


class TenderParseRequest(BaseModel):
    method: Literal["auto", "manual"] = "auto"
    pastedText: str = ""


class DirectoryGenerateRequest(BaseModel):
    strategy: Literal["score_points", "template", "manual"] = "score_points"
    manualDirectory: str = ""


class DirectoryActionRequest(BaseModel):
    instruction: str = ""
    style: str = "通用投标技术标"


class SectionGenerateRequest(BaseModel):
    sectionId: str
    title: str
    subItems: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    wordTarget: int = 800


class SectionGenerateByIdRequest(BaseModel):
    title: str | None = None
    subItems: list[str] | None = None
    requirements: list[str] = Field(default_factory=list)
    wordTarget: int | None = None


class SectionActionRequest(BaseModel):
    instruction: str = ""
    wordTarget: int | None = None


class AgentChatRequest(BaseModel):
    message: str
    mode: Literal["agent", "ask", "plan"] = "agent"
    projectId: str | None = None
    model: str | None = None
    provider: str | None = None
    context: dict = Field(default_factory=dict)


class AgentChatResponse(BaseModel):
    message: str
    document: DocumentOutput | None = None
    events: list[dict] = Field(default_factory=list)
