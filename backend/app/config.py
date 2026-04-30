from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = Field(default="gemini", alias="LLM_PROVIDER")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    ollama_api_key: str = Field(default="", alias="OLLAMA_API_KEY")
    ollama_base_url: str = Field(default="https://ollama.com/api", alias="OLLAMA_BASE_URL")
    ollama_plan_model: str = Field(default="qwen3-vl:235b-cloud", alias="OLLAMA_PLAN_MODEL")
    ollama_remediation_model: str = Field(
        default="qwen3-vl:235b-cloud", alias="OLLAMA_REMEDIATION_MODEL"
    )
    ollama_reasoning_level: str = Field(default="medium", alias="OLLAMA_REASONING_LEVEL")
    ollama_page_image_dpi: int = Field(default=144, alias="OLLAMA_PAGE_IMAGE_DPI")
    ollama_max_page_images: int = Field(default=6, alias="OLLAMA_MAX_PAGE_IMAGES")
    app_api_key: str = Field(default="", alias="APP_API_KEY")
    public_origin: str = Field(default="http://localhost:8000", alias="PUBLIC_ORIGIN")

    basic_auth_enabled: bool = Field(default=False, alias="BASIC_AUTH_ENABLED")
    basic_auth_user: str = Field(default="defsol", alias="BASIC_AUTH_USER")
    basic_auth_password: str = Field(default="defsol", alias="BASIC_AUTH_PASSWORD")

    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")
    rate_limit_enabled: bool = Field(default=False, alias="RATE_LIMIT_ENABLED")
    rate_limit_window_seconds: int = Field(
        default=60, ge=1, alias="RATE_LIMIT_WINDOW_SECONDS"
    )
    rate_limit_api_per_minute: int = Field(default=120, ge=0, alias="RATE_LIMIT_API_PER_MINUTE")
    rate_limit_ingest_per_minute: int = Field(
        default=12, ge=0, alias="RATE_LIMIT_INGEST_PER_MINUTE"
    )
    rate_limit_translate_per_minute: int = Field(
        default=20, ge=0, alias="RATE_LIMIT_TRANSLATE_PER_MINUTE"
    )
    rate_limit_ask_per_minute: int = Field(default=60, ge=0, alias="RATE_LIMIT_ASK_PER_MINUTE")

    livekit_url: str = Field(default="", alias="LIVEKIT_URL")
    livekit_api_key: str = Field(default="", alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="", alias="LIVEKIT_API_SECRET")
    gemini_live_model: str = Field(
        default="gemini-2.5-flash-native-audio-preview-12-2025",
        alias="GEMINI_LIVE_MODEL",
    )

    gemini_remediation_model: str = Field(
        default="gemini-2.5-flash-lite", alias="GEMINI_REMEDIATION_MODEL"
    )
    gemini_ask_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_ASK_MODEL")
    gemini_call_timeout: float = Field(default=120.0, alias="GEMINI_CALL_TIMEOUT")
    # Gemini 3.x supports thinking_level: "none" | "low" | "high". Empty disables.
    gemini_thinking_level: str = Field(default="", alias="GEMINI_THINKING_LEVEL")
    pdf_vector_images_enabled: bool = Field(
        default=True, alias="PDF_VECTOR_IMAGES_ENABLED"
    )
    pdf_vector_min_ops: int = Field(default=25, alias="PDF_VECTOR_MIN_OPS")
    pdf_vector_raster_dpi: int = Field(default=110, alias="PDF_VECTOR_RASTER_DPI")
    structured_render_v2_enabled: bool = Field(
        default=False, alias="STRUCTURED_RENDER_V2_ENABLED"
    )
    structured_render_v2_pdf_backend: Literal["native", "liteparse", "llamaparse"] = Field(
        default="native",
        alias="STRUCTURED_RENDER_V2_PDF_BACKEND",
        validation_alias=AliasChoices("STRUCTURED_RENDER_V2_PDF_BACKEND", "PDF_BACKEND"),
    )
    llama_cloud_api_key: str = Field(default="", alias="LLAMA_CLOUD_API_KEY")
    llamaparse_base_url: str = Field(default="", alias="LLAMAPARSE_BASE_URL")
    llamaparse_tier: Literal["fast", "cost_effective", "agentic", "agentic_plus"] = Field(
        default="cost_effective",
        alias="LLAMAPARSE_TIER",
    )
    llamaparse_version: str = Field(default="latest", alias="LLAMAPARSE_VERSION")
    llamaparse_max_pages: int | None = Field(default=None, ge=1, alias="LLAMAPARSE_MAX_PAGES")
    llamaparse_target_pages: str = Field(default="", alias="LLAMAPARSE_TARGET_PAGES")
    llamaparse_use_cost_optimizer: bool = Field(
        default=False,
        alias="LLAMAPARSE_COST_OPTIMIZER",
    )
    llamaparse_disable_cache: bool = Field(default=False, alias="LLAMAPARSE_DISABLE_CACHE")
    llamaparse_do_not_cache: bool = Field(default=False, alias="LLAMAPARSE_DO_NOT_CACHE")
    llamaparse_timeout_s: float = Field(default=300.0, gt=0, alias="LLAMAPARSE_TIMEOUT_S")
    llamaparse_ocr_languages: str = Field(default="en", alias="LLAMAPARSE_OCR_LANGUAGES")
    layout_agent_enabled: bool = Field(default=False, alias="LAYOUT_AGENT_ENABLED")
    layout_agent_model: str = Field(default="kimi-k2.6:cloud", alias="LAYOUT_AGENT_MODEL")
    layout_agent_max_revisions: int = Field(default=1, alias="LAYOUT_AGENT_MAX_REVISIONS")
    layout_agent_timeout_s: float = Field(default=45.0, alias="LAYOUT_AGENT_TIMEOUT_S")
    layout_agent_apply_to_formats: frozenset[str] = Field(
        default=frozenset({"pdf"}), alias="LAYOUT_AGENT_APPLY_TO_FORMATS"
    )

    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="DATA_DIR")
    max_pdf_bytes: int = Field(default=52_428_800, alias="MAX_PDF_BYTES")

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("layout_agent_apply_to_formats", mode="before")
    @classmethod
    def _parse_layout_agent_formats(cls, value):
        if isinstance(value, str):
            return frozenset(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator("llamaparse_max_pages", mode="before")
    @classmethod
    def _parse_optional_positive_int(cls, value):
        if value == "":
            return None
        return value

    @property
    def cache_db_path(self) -> Path:
        return self.data_dir / "cache.db"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def images_dir_for(self, sha256: str) -> Path:
        return self.images_dir / sha256

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def artifacts_dir_for(self, sha256: str) -> Path:
        return self.artifacts_dir / sha256

    @property
    def livekit_configured(self) -> bool:
        return all([self.livekit_url, self.livekit_api_key, self.livekit_api_secret])

    @property
    def ollama_requires_api_key(self) -> bool:
        host = (urlparse(self.ollama_base_url).hostname or "").lower()
        return host.endswith("ollama.com")

    @property
    def transcript_llm_configured(self) -> bool:
        if self.llm_provider == "ollama":
            return bool(self.ollama_api_key) or not self.ollama_requires_api_key
        return bool(self.google_api_key)

    def ensure_dirs(self) -> None:
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
