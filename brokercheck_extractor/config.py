"""Centralized runtime configuration. Reads from env vars with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # ---- Acquisition ----
    finra_search_url: str = "https://api.brokercheck.finra.org/search/firm"
    finra_pdf_url_template: str = "https://files.brokercheck.finra.org/firm/firm_{crd}.pdf"

    sec_edgar_search_url: str = "https://efts.sec.gov/LATEST/search-index"
    sec_submissions_url_template: str = "https://data.sec.gov/submissions/CIK{cik10}.json"
    sec_archive_url_template: str = (
        "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}"
    )

    # SEC requires a User-Agent with contact info; FINRA is more forgiving but we match it.
    user_agent: str = os.environ.get(
        "HTTP_USER_AGENT",
        "BrokerCheckExtractor/1.0 (contact: ops@example.com)",
    )

    # ---- Concurrency ----
    max_concurrency: int = int(os.environ.get("MAX_CONCURRENCY", "5"))
    per_request_timeout_s: float = float(os.environ.get("REQ_TIMEOUT", "30"))
    retries: int = int(os.environ.get("RETRIES", "4"))

    # ---- Storage ----
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/brokercheck",
    )
    raw_pdf_dir: str = os.environ.get("RAW_PDF_DIR", "./_raw_pdfs")

    # ---- Parsing ----
    ocr_text_threshold: int = 50   # page text length under this triggers OCR
    ocr_dpi: int = 300
    enable_ocr: bool = os.environ.get("ENABLE_OCR", "true").lower() == "true"


settings = Settings()
