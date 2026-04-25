from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import computed_field
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
ROOT_ENV_PATH = PROJECT_ROOT / ".env"
BACKEND_ENV_PATH = BACKEND_ROOT / ".env"

# Load root env first, then backend env so backend-specific values consistently win
# no matter whether the process is started from the repo root or backend directory.
load_dotenv(ROOT_ENV_PATH, override=False)
load_dotenv(BACKEND_ENV_PATH, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "Client Clearing Lead Gen Engine API"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/deshorn"
    redis_url: str = "redis://localhost:6379/0"
    backend_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    auth_session_cookie_name: str = "better-auth.session_token"
    auth_secret: str = Field(default="deshorn-local-dev-secret-2026-strong-key", alias="BETTER_AUTH_SECRET")
    data_source_mode: str = "live"
    sec_user_agent: str = "Alchemy Dev compliance@alchemy.dev"
    edgar_rate_limit_per_second: int = 10
    finra_rate_limit_per_second: int = 2
    pdf_cache_dir: str = ".tmp/pdf-cache"
    llm_provider: str = "gemini"
    clearing_extraction_min_confidence: float = 0.7
    sec_request_timeout_seconds: float = 30.0
    sec_request_max_retries: int = 3
    sec_submissions_base_url: str = "https://data.sec.gov/submissions"
    sec_archives_base_url: str = "https://www.sec.gov/Archives/edgar/data"
    sec_bulk_submissions_url: str = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    sec_bulk_submissions_zip_path: str = ".tmp/sec/submissions.zip"
    edgar_target_sic_codes: str = "6211"
    finra_search_base_url: str = "https://api.brokercheck.finra.org/search/firm"
    finra_request_timeout_seconds: float = 20.0
    finra_request_delay_seconds: float = 0.5
    finra_request_max_retries: int = 4
    finra_harvest_queries: str = (
        "llc,inc,lp,corp,company,securities,capital,markets,financial,broker,dealer,investment"
    )
    focus_reports_csv_path: str | None = None
    initial_load_limit: int | None = None
    minimum_initial_load_records: int = 500
    run_focus_import_on_initial_load: bool = True
    run_filing_monitor_on_initial_load: bool = True
    run_clearing_pipeline_on_initial_load: bool = False
    financial_extraction_min_confidence: float = 0.65
    financial_pipeline_offset: int = 0
    financial_pipeline_limit: int | None = None
    filing_monitor_offset: int = 0
    filing_monitor_limit: int | None = None
    clearing_pipeline_offset: int = 0
    clearing_pipeline_limit: int | None = None
    contact_enrichment_provider: str = "disabled"
    apollo_api_key: str | None = None
    zoominfo_api_key: str | None = None
    # Multi-provider contact discovery chain used by the "Generate More Details"
    # button on the firm detail page. The orchestrator walks providers in the
    # comma-separated order below; the first result with ``confidence >=
    # contact_discovery_min_confidence`` wins. Keys (``hunter_api_key``,
    # ``snov_client_id``, ``snov_client_secret``) are declared further down
    # because the existing email-extractor module already depends on them.
    contact_discovery_chain: str = "apollo_match,hunter,snov"
    contact_discovery_min_confidence: float = 60.0
    contact_discovery_timeout: float = 10.0
    gemini_api_key: str | None = None
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_pdf_model: str = "gemini-2.5-pro"
    gemini_request_timeout_seconds: float = 120.0
    gemini_request_max_retries: int = 2
    gemini_inline_pdf_max_size_mb: int = 45
    # Files API kicks in for PDFs above this size to keep working-set memory
    # flat. Smaller PDFs stay on the inline base64 path (fewer round-trips,
    # lower latency). Set above gemini_inline_pdf_max_size_mb to disable.
    gemini_files_api_threshold_mb: int = 20
    openai_api_key: str | None = None
    openai_api_base: str = "https://api.openai.com/v1"
    openai_pdf_model: str = "gpt-4o"
    openai_request_timeout_seconds: float = 120.0
    openai_request_max_retries: int = 2
    openai_max_pdf_size_mb: int = 45
    anthropic_api_key: str | None = None

    # Email extractor — discovery providers (Hunter, Snov, theHarvester, site crawler).
    # Apollo provider is intentionally absent: upstream module ships without it.
    # All keys are optional; missing credentials short-circuit to a provider-level
    # "credentials not configured" error without blocking the rest of the fan-out.
    hunter_api_key: str | None = None
    hunter_limit: int = Field(default=100, ge=1, le=100)
    theharvester_sources: str = "crtsh,rapiddns,otx,duckduckgo"
    theharvester_timeout_seconds: int = Field(default=90, ge=10, le=300)
    snov_client_id: str | None = None
    snov_client_secret: str | None = None
    snov_limit: int = Field(default=100, ge=1, le=1000)
    # SMTP verification (POST /verify) — RCPT TO probe knobs.
    smtp_verify_timeout_seconds: int = Field(default=15, ge=5, le=60)
    smtp_verify_max_batch: int = Field(default=25, ge=1, le=100)
    smtp_verify_concurrency: int = Field(default=1, ge=1, le=10)
    smtp_verify_from_address: str = "verify@email-extractor.local"
    smtp_verify_helo_host: str = "email-extractor.local"

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
