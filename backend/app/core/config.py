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

    app_name: str = "DOX API"
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
    # Deprecated as a persistent cache. Previously held every parsed FOCUS
    # PDF on disk, which grew to ~9 GB on the fis-backend container. Now an
    # optional *parent* directory for per-extraction tempdirs (see
    # ``app.services.pdf_downloader.pdf_tempdir``). Leave unset in production
    # — the system temp is used and each extraction wipes its own working
    # directory on exit. Local devs can point this at a fixed location to
    # inspect a download mid-flight.
    pdf_cache_dir: str | None = None
    llm_provider: str = "gemini"
    clearing_extraction_min_confidence: float = 0.7
    # Confidence floor for the unified LLM-based clearing classifier
    # (services/clearing_classifier.py). Below this, the broker-dealer's
    # clearing_classification is set to 'needs_review' instead of the
    # LLM-returned label, so low-signal rows surface for manual review
    # rather than silently flipping the canonical column.
    clearing_classification_min_confidence: float = 0.7
    sec_request_timeout_seconds: float = 30.0
    sec_request_max_retries: int = 3
    sec_submissions_base_url: str = "https://data.sec.gov/submissions"
    sec_archives_base_url: str = "https://www.sec.gov/Archives/edgar/data"
    sec_bulk_submissions_url: str = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    sec_bulk_submissions_zip_path: str = ".tmp/sec/submissions.zip"
    edgar_target_sic_codes: str = "6211"
    # ── IAPD Investment Adviser bulk download (PR 2 of advisor-list rollout) ──
    # Index page that lists monthly ``ia{MMDDYY}.zip`` snapshots of all
    # SEC-registered Investment Advisers. The IapdService scrapes this page
    # rather than constructing a URL by date because the SEC moved the file
    # path from ``/files/investment/data/.../`` to
    # ``/files/investment/data/other/.../`` mid-Jan 2026 and may move it again.
    # Both old and new paths still serve historical files; scraping the index
    # avoids hard-coding a fragile path.
    iapd_index_url: str = (
        "https://www.sec.gov/data-research/sec-markets-data/"
        "information-about-registered-investment-advisers-exempt-reporting-advisers"
    )
    # Local cache for the downloaded ZIP. ~5 MB / month. TTL'd to 7 days to
    # match the BD bulk submissions cache.
    iapd_zip_cache_path: str = ".tmp/iapd/registered_advisers.zip"
    iapd_zip_ttl_seconds: int = 7 * 24 * 60 * 60
    # Same SEC-wide 10 req/sec ceiling as EDGAR. The IAPD bulk path is one
    # GET per month, so this limit is effectively for the EFTS 13F pass.
    iapd_request_timeout_seconds: float = 60.0
    iapd_request_max_retries: int = 3
    # ── EDGAR full-text search (EFTS) for Form 13F-HR enumeration ──
    # Used by ThirteenFFilterService to flag IAPD records that have filed a
    # 13F holdings report in the recent window. EFTS pages are capped at
    # ``from + size <= 10000`` (Elasticsearch default), and 90 days of
    # 13F-HR easily exceeds that, so the service partitions by week and
    # dedupes by CIK.
    sec_efts_search_url: str = "https://efts.sec.gov/LATEST/search-index"
    # 90 days = ~one quarter, which is the 13F filing cadence. Tighter
    # windows yield fresher data (and miss fewer recent filers); wider
    # windows pick up more historical filers but inflate scan cost.
    thirteen_f_lookback_days: int = 120
    thirteen_f_partition_days: int = 7
    initial_load_advisors_limit: int | None = None
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
    # Tier 3 of the firm-website resolver chain
    # (Apollo -> Hunter -> serper.dev -> SerpAPI). Slotted ahead of SerpAPI
    # because serper.dev is ~50× cheaper per query and ships a 2,500-query
    # one-time free credit on signup, so it's the right primary search tier
    # for both the lazy on-demand resolver and the bulk backfill scripts.
    # Optional: when unset, the resolver skips this tier and falls straight
    # through to SerpAPI.
    serper_api_key: str | None = None
    # Tier 4 of the firm-website resolver chain — fallback when serper.dev
    # is unset or misses. Free-tier key gives 250 searches/month; bulk
    # backfills should run on a paid plan. Optional: when unset, the
    # resolver skips this tier the same way it skips Hunter when
    # hunter_api_key is unset.
    serpapi_api_key: str | None = None
    # Cooldown window between successive Apollo /enrich attempts for the same
    # broker-dealer. Stops the detail-page useEffect from re-firing /enrich
    # on every visit for empty-result firms (where the FE's existing guard
    # is insufficient because no ExecutiveContact rows exist to read off).
    # Stamped on Apollo-owned outcomes only (success + no-result). Transient
    # 5xx / network errors do not engage the cooldown. Set to 0 to disable.
    apollo_enrich_cooldown_hours: int = 24
    zoominfo_api_key: str | None = None
    # People Data Labs — the first provider in the discovery chain that
    # actually returns person phones via API (PR #419's audit showed Apollo
    # /people/match returns phone_numbers=[] for 0/105 historical rows on
    # the current plan). PDL also returns multiple emails (work + personal)
    # and multiple phones (mobile + work) per match, which the ``emails`` /
    # ``phones`` JSONB columns added in migration 20260521_0054 persist.
    pdl_api_key: str | None = None
    # PDL's match likelihood is 1..10; values >=6 map to >=60.0 confidence
    # which clears the default ``contact_discovery_min_confidence`` floor.
    # Setting this as a server-side filter means PDL only bills for matches
    # that already clear our threshold — a too-low value would return
    # billed-but-useless matches.
    pdl_min_likelihood: int = 6
    # Auto-resolve phones via PDL during the refresh-all auto-enrich pass.
    # Apollo's /people/match doesn't return phones on our plan (gated behind
    # a separate Phone Numbers add-on), so after Apollo writes per-officer
    # rows with email + LinkedIn, this step calls PDL.enrich_by_email for
    # any contact still missing a phone. Costs ~1 PDL credit per officer
    # per BD per cooldown window (24h default). Disable by setting
    # CONTACT_ENRICH_AUTO_PDL_PHONES=false in the backend .env if PDL
    # spend becomes a concern; the manual "Find Phone" button is unaffected.
    contact_enrich_auto_pdl_phones: bool = True
    # Multi-provider contact discovery chain used by the "Generate More Details"
    # button on the firm detail page. The orchestrator walks providers in the
    # comma-separated order below; the first result with ``confidence >=
    # contact_discovery_min_confidence`` wins. PDL leads because it's the
    # only provider that returns multi-value emails + phones; Apollo / Hunter
    # / Snov stay as fallbacks (cheap, pay-as-you-go) when PDL misses or has
    # no key. Keys (``hunter_api_key``, ``snov_client_id``,
    # ``snov_client_secret``) are declared further down because the existing
    # email-extractor module already depends on them.
    contact_discovery_chain: str = "pdl,apollo_match,hunter,snov"
    contact_discovery_min_confidence: float = 60.0
    contact_discovery_timeout: float = 10.0
    gemini_api_key: str | None = None
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    # Production default for the structured-PDF extraction path
    # (clearing pipeline, financial pipeline, classification fan-out). Tier 1
    # of the Gemini paid plan caps ``gemini-2.5-pro`` at 1,000 requests/day
    # but allows ``gemini-2.5-flash`` at 10,000 requests/day with the same
    # JSON-schema + multi-modal capabilities and ~5x lower per-call cost,
    # so Flash is the production default. Override with the
    # ``GEMINI_PDF_MODEL`` env var (e.g. on Cloud Run) to flip back to Pro
    # for ad-hoc reruns when Pro quota is healthy and the deeper reasoning
    # chain is wanted on rationale-heavy ambiguous cases.
    gemini_pdf_model: str = "gemini-2.5-flash"
    gemini_request_timeout_seconds: float = 120.0
    gemini_request_max_retries: int = 2
    gemini_inline_pdf_max_size_mb: int = 45
    # Files API kicks in for PDFs above this size to keep working-set memory
    # flat. Smaller PDFs stay on the inline base64 path (fewer round-trips,
    # lower latency). Set above gemini_inline_pdf_max_size_mb to disable.
    gemini_files_api_threshold_mb: int = 20
    # Free-form chat model for the in-app Doxie chatbot (separate from
    # ``gemini_pdf_model`` so chat tuning doesn't affect the PDF extraction
    # pipeline). Flash is the production default — sub-second TTFB and ~5×
    # cheaper than Pro per token, sufficient for general-purpose conversation.
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_chat_max_output_tokens: int = 1024
    gemini_chat_temperature: float = 0.7
    # When True, the LLM-bound clearing-extraction path uploads PDFs to the
    # provider Files API (Gemini / OpenAI) instead of inline base64. ADR-0001
    # phase 2. Default off so the rollout is reversible by flag flip; the
    # default-off codepath is byte-for-byte identical to today's behavior.
    # Per-process LRU keyed by ``(cik, accession_number)`` reuses uploaded
    # ``file_id`` references inside the 23h TTL window (1h margin under the
    # provider's 24h server-side TTL).
    llm_use_files_api: bool = Field(default=False, alias="LLM_USE_FILES_API")
    openai_api_key: str | None = None
    openai_api_base: str = "https://api.openai.com/v1"
    openai_pdf_model: str = "gpt-4o"
    openai_request_timeout_seconds: float = 120.0
    openai_request_max_retries: int = 2
    openai_max_pdf_size_mb: int = 45
    anthropic_api_key: str | None = None

    # Google OAuth — used by:
    #   1. Better Auth (frontend) for "Login with Google" via the same
    #      ``client_id`` + ``client_secret`` pair.
    #   2. Backend (services/google_oauth.py) for refreshing Gmail access
    #      tokens stored on the ``account`` table when the user clicks
    #      Send in the per-contact Outreach modal at /master-list/{id}.
    # Both can be unset in CI / tests where no Gmail call fires; the
    # send endpoint surfaces a clean 412 when missing.
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # Microsoft OAuth — used by:
    #   1. Better Auth (frontend) for "Continue with Outlook" via the
    #      ``microsoft`` social provider against tenant ``common`` so
    #      both work / school AD accounts and consumer outlook.com /
    #      hotmail.com / live.com accounts can sign in.
    #   2. Backend (services/email_providers/microsoft_oauth.py) for
    #      refreshing access tokens used by the Microsoft Graph
    #      ``users.sendMail`` call in the outreach send path.
    # Required scope at consent: ``openid email profile offline_access
    # Mail.Send``. Both can be unset until the Azure AD app
    # registration lands; the send endpoint surfaces a clean 503 then.
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None

    # Yahoo OAuth — used by:
    #   1. Better Auth (frontend) via the ``genericOAuth`` plugin pointed
    #      at Yahoo's OIDC discovery URL.
    #   2. Backend (services/email_providers/yahoo_oauth.py) for
    #      refreshing access tokens used by the Yahoo SMTP XOAUTH2 send
    #      path in services/email_providers/yahoo.py.
    # Required scope at consent: ``openid email profile mail-w``. Both
    # can be unset until the Yahoo Developer app registration lands.
    yahoo_client_id: str | None = None
    yahoo_client_secret: str | None = None

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

    # Vault file-upload + RAG (2026-05-05). The bucket is per-environment
    # (``fis-vault-staging`` vs ``fis-vault-prod``); see
    # ``plans/vault-rag-ops-2026-05-04.md`` for provisioning. Empty string
    # = feature disabled — services/vault_storage.py raises VaultStorageError
    # when an upload is attempted, which the endpoint maps to 503.
    vault_storage_bucket: str = ""

    # ── SEC Form 4 ingestion (Investors tab — 2026-05-13) ──────────────────
    # Form 4 (Statement of Changes in Beneficial Ownership) is filed by
    # corporate insiders within ~2 business days of an insider transaction.
    # The Investors tab surfaces reporting persons whose transactions clear
    # the value floor, partitioned into A (acquired/buy) and D (disposed/sell)
    # buckets. See plans/c-users-dswdsrv-caraga-downloads-potenti-robust-wombat.md
    # for the full design and product context.
    form4_min_transaction_value: float = 50000.0
    # Watcher fetch window: how many days back from `today` to query EFTS on
    # each run. The 7-day overlap (vs the 3-month visibility window) is
    # deliberate — it catches late-filed Form 4s and is idempotent via the
    # dedupe_key UNIQUE constraint, so re-running across the same window
    # is free.
    form4_lookback_days: int = 7
    # UI default visibility window: "last 3 months" per the product brief.
    # The API caps the param at 365 days; the FE filter exposes
    # 30 / 90 / 180 / 365 as the user-facing presets.
    form4_default_visibility_days: int = 90

    # Cloud Scheduler / OIDC dual-auth for Tier 2 pipeline trigger endpoints.
    # ``cloud_scheduler_sa_email`` is the only ``email`` claim accepted on the
    # OIDC path of ``_ensure_admin_or_scheduler_sa``. Defaults to the runtime
    # SA used by the production fis-backend Cloud Run service.
    cloud_scheduler_sa_email: str = "136029935063-compute@developer.gserviceaccount.com"
    # Required ``aud`` claim on incoming Google-signed OIDC tokens. Must match
    # the public Cloud Run URL the scheduler job targets — *not* the custom
    # domain — because Cloud Scheduler signs tokens against the *.run.app URL.
    backend_audience: str = "https://fis-backend-136029935063.us-central1.run.app"

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
