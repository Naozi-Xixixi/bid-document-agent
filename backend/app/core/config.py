from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "WordAgent Frontend API Bridge"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    host: str = Field(default="127.0.0.1", alias="WORDAGENT_BRIDGE_HOST")
    port: int = Field(default=3896, alias="WORDAGENT_BRIDGE_PORT")
    data_dir: Path = Field(default=Path("data"), alias="WORDAGENT_BRIDGE_DATA_DIR")
    database_path: Path | None = Field(default=None, alias="WORDAGENT_BRIDGE_DATABASE_PATH")
    db_backend: str = Field(default="sqlite", alias="DB_BACKEND")
    # MiB; supports large image-bearing Word files without buffering them in RAM.
    bid_max_upload_size_mb: int = Field(default=1024, ge=1, le=10240, alias="BID_MAX_UPLOAD_SIZE_MB")
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="wordagent", alias="MYSQL_USER")
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")
    mysql_auth_database: str = Field(default="wordagent_auth", alias="MYSQL_AUTH_DATABASE")
    mysql_database_prefix: str = Field(default="wordagent_company", alias="MYSQL_DATABASE_PREFIX")
    mysql_connect_timeout_seconds: int = Field(default=10, alias="MYSQL_CONNECT_TIMEOUT_SECONDS")
    mysql_read_timeout_seconds: int = Field(default=120, alias="MYSQL_READ_TIMEOUT_SECONDS")
    mysql_write_timeout_seconds: int = Field(default=120, alias="MYSQL_WRITE_TIMEOUT_SECONDS")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5181", "http://127.0.0.1:5181"],
        alias="WORDAGENT_BRIDGE_CORS_ORIGINS",
    )
    cors_origin_regex: str = Field(
        default=r"^https?://(?:localhost|127\.0\.0\.1|\[::1\]|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):(?:5173|5181|4173)$",
        alias="WORDAGENT_BRIDGE_CORS_ORIGIN_REGEX",
    )
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    qwen_api_key: str = Field(default="", alias="QWEN_API_KEY")
    qwen_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="QWEN_BASE_URL")
    qwen_generation_model: str = Field(default="qwen3.7-flash", alias="QWEN_GENERATION_MODEL")
    qwen_ocr_enabled: bool = Field(default=False, alias="QWEN_OCR_ENABLED")
    qwen_ocr_model: str = Field(default="qwen3.5-ocr", alias="QWEN_OCR_MODEL")
    qwen_embedding_model: str = Field(default="text-embedding-v4", alias="QWEN_EMBEDDING_MODEL")
    qwen_embedding_dimensions: int = Field(default=1024, alias="QWEN_EMBEDDING_DIMENSIONS")
    qwen_embedding_batch_size: int = Field(default=10, alias="QWEN_EMBEDDING_BATCH_SIZE")
    qwen_timeout_seconds: float = Field(default=60.0, alias="QWEN_TIMEOUT_SECONDS")
    qwen_document_analysis_timeout_seconds: float = Field(default=180.0, alias="QWEN_DOCUMENT_ANALYSIS_TIMEOUT_SECONDS")
    qwen_ocr_timeout_seconds: float = Field(default=120.0, alias="QWEN_OCR_TIMEOUT_SECONDS")
    qwen_embedding_timeout_seconds: float = Field(default=60.0, alias="QWEN_EMBEDDING_TIMEOUT_SECONDS")
    qwen_ocr_max_assets_per_item: int = Field(default=1000, alias="QWEN_OCR_MAX_ASSETS_PER_ITEM")
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_embedding_model: str = Field(default="bge-m3", alias="OLLAMA_EMBEDDING_MODEL")
    ollama_embedding_dimensions: int = Field(default=1024, alias="OLLAMA_EMBEDDING_DIMENSIONS")
    ollama_embedding_batch_size: int = Field(default=16, alias="OLLAMA_EMBEDDING_BATCH_SIZE")
    ollama_timeout_seconds: float = Field(default=180.0, alias="OLLAMA_TIMEOUT_SECONDS")
    qwen_enable_thinking: bool = Field(default=False, alias="QWEN_ENABLE_THINKING")
    qwen_image_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        alias="QWEN_IMAGE_BASE_URL",
    )
    qwen_image_model: str = Field(default="qwen-image-3.0", alias="QWEN_IMAGE_MODEL")
    qwen_image_timeout_seconds: float = Field(default=90.0, alias="QWEN_IMAGE_TIMEOUT_SECONDS")
    mermaid_dist_dir: Path | None = Field(default=None, alias="MERMAID_DIST_DIR")
    vector_backend: str = Field(default="local", alias="VECTOR_BACKEND")
    vector_embedding_provider: str = Field(default="local", alias="VECTOR_EMBEDDING_PROVIDER")
    vector_embedding_dimension: int = Field(default=128, alias="VECTOR_EMBEDDING_DIMENSION")
    milvus_uri: str = Field(default="http://127.0.0.1:19530", alias="MILVUS_URI")
    milvus_token: str = Field(default="", alias="MILVUS_TOKEN")
    milvus_db_name: str = Field(default="", alias="MILVUS_DB_NAME")
    milvus_collection_knowledge: str = Field(default="new_biaoshuai_knowledge_chunks", alias="MILVUS_COLLECTION_KNOWLEDGE")
    milvus_collection_rag_cases: str = Field(default="new_biaoshuai_rag_case_chunks", alias="MILVUS_COLLECTION_RAG_CASES")
    milvus_timeout_seconds: float = Field(default=10.0, alias="MILVUS_TIMEOUT_SECONDS")
    milvus_search_multiplier: int = Field(default=30, alias="MILVUS_SEARCH_MULTIPLIER")
    rag_search_overfetch: int = Field(default=10, alias="RAG_SEARCH_OVERFETCH")
    rag_rrf_constant: int = Field(default=60, alias="RAG_RRF_CONSTANT")
    rag_vector_min_score: float = Field(default=0.30, alias="RAG_VECTOR_MIN_SCORE")
    rag_vector_task_lease_seconds: int = Field(default=21600, alias="RAG_VECTOR_TASK_LEASE_SECONDS")
    rag_vector_task_max_attempts: int = Field(default=3, alias="RAG_VECTOR_TASK_MAX_ATTEMPTS")
    rag_vector_task_retry_base_seconds: float = Field(default=2.0, alias="RAG_VECTOR_TASK_RETRY_BASE_SECONDS")
    qdrant_url: str = Field(default="http://127.0.0.1:6333", alias="QDRANT_URL")
    qdrant_path: Path | None = Field(default=None, alias="QDRANT_PATH")
    qdrant_api_key: str = Field(default="", alias="QDRANT_API_KEY")
    qdrant_timeout_seconds: float = Field(default=10.0, alias="QDRANT_TIMEOUT_SECONDS")
    qdrant_write_retries: int = Field(default=3, alias="QDRANT_WRITE_RETRIES")
    qdrant_collection_knowledge: str = Field(default="wordagent_knowledge_chunks", alias="QDRANT_COLLECTION_KNOWLEDGE")
    qdrant_collection_rag_cases: str = Field(default="wordagent_rag_case_chunks", alias="QDRANT_COLLECTION_RAG_CASES")
    amap_web_service_key: str = Field(default="", alias="AMAP_WEB_SERVICE_KEY")
    enable_llm_parse_optimize: bool = Field(default=True, alias="ENABLE_LLM_PARSE_OPTIMIZE")
    enable_officecli_docx_formatting: bool = Field(default=True, alias="ENABLE_OFFICECLI_DOCX_FORMATTING")
    officecli_binary: str = Field(default="officecli", alias="OFFICECLI_BINARY")
    officecli_timeout_seconds: float = Field(default=20.0, alias="OFFICECLI_TIMEOUT_SECONDS")
    admin_initial_username: str = Field(default="admin", alias="ADMIN_INITIAL_USERNAME")
    admin_initial_password: str = Field(default="", alias="ADMIN_INITIAL_PASSWORD")
    auth_session_days: int = Field(default=7, alias="AUTH_SESSION_DAYS")

    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def backend_root(self) -> Path:
        return BACKEND_ROOT

    def resolve_local_path(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.backend_root / path).resolve()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ["*"]
            if stripped.startswith("["):
                return [str(item) for item in json.loads(stripped)]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return ["*"]

    @field_validator("qdrant_path", mode="before")
    @classmethod
    def empty_qdrant_path_is_remote_mode(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @property
    def project_dir(self) -> Path:
        from app.core.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        path = (tenant.database_file.parent if tenant else self.resolve_local_path(self.data_dir)) / "projects"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def upload_dir(self) -> Path:
        from app.core.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        if tenant is not None:
            tenant.upload_dir.mkdir(parents=True, exist_ok=True)
            return tenant.upload_dir
        path = self.resolve_local_path(self.data_dir) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def settings_file(self) -> Path:
        from app.core.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        data_dir = tenant.database_file.parent if tenant else self.resolve_local_path(self.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "settings.json"

    @property
    def database_file(self) -> Path:
        from app.core.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        if tenant is not None:
            tenant.database_file.parent.mkdir(parents=True, exist_ok=True)
            return tenant.database_file
        configured = self.database_path or (self.data_dir / "wordagent.sqlite3")
        path = self.resolve_local_path(configured)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
