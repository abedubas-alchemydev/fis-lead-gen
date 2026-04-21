# BrokerCheck Extractor

Automated pipeline that, given a list of CRD numbers already stored in your **Neon** Postgres database, downloads each firm's **FINRA BrokerCheck Detailed Report** and its latest **SEC Form X-17A-5** filings, parses both into structured data, derives the client's required fields (Self-Clearing vs Fully Disclosed, YoY net-capital and total-asset growth), and writes the results back to Neon.

Built for the 3,000-firm batch described in the requirements brief. Polite, resumable, delta-aware, with a dead-letter queue for the inevitable 2–3% of parse failures.

---

## What this produces

Per firm, two domains of data land in Neon:

**Domain 1 — Firm Profile (FINRA)**
CRD, SEC#, legal name, officers & directors (name + full multi-line position + ownership), types of business (total + list + "other"), clearing arrangements (statement + classification + raw text), introducing arrangements, formation date, SEC/FINRA registration date, termination date.

**Domain 2 — Financials & Contact (SEC X-17A-5)**
SEC file #, period beginning/ending, primary contact (name, title, email, phone), total assets, total liabilities, members' or stockholders' equity, net capital, auditor name and PCAOB ID — for the two most recent filings, plus computed YoY growth.

---

## Repository layout

```
brokercheck_extractor/
├── acquisition/
│   ├── finra_client.py       # Async httpx client, CRD → PDF URL, search API fallback
│   └── sec_edgar_client.py   # CIK resolution, X-17A-5 filing enumeration, PDF download
├── parsers/
│   ├── base.py               # pdfplumber → PyMuPDF → OCR layered extraction
│   ├── finra_parser.py       # Section-anchored extraction for BrokerCheck
│   └── focus_parser.py       # X-17A-5 facing page + SoFC + net capital
├── derivation/
│   ├── clearing_classifier.py  # Self-Clearing / Fully Disclosed with raw-text override
│   └── yoy_calculator.py       # Current vs prior growth
├── schema/models.py          # Pydantic models (the wire / DB contract)
├── storage/db.py             # SQLAlchemy async + Neon-compatible UPSERTs + DLQ
├── orchestrator.py           # Batch driver: asyncio.Semaphore + tenacity + delta skip
├── cli.py                    # init-db / run / parse-one / fetch-crd
└── tests/test_parsers.py     # Regression tests against real PDFs
fixtures/
├── firm_5393_schwab.pdf
└── xfocus_andpartners.pdf
```

---

## Setup

### 1. Install system dependencies

OCR fallback needs Tesseract and Poppler. The pipeline still works without these on born-digital PDFs, but the X-17A-5 facing pages are almost always scanned, so you want them.

```bash
# Ubuntu / Debian
sudo apt-get install -y tesseract-ocr poppler-utils

# macOS
brew install tesseract poppler
```

### 2. Install Python dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Configure Neon

Copy `.env.example` to `.env` and fill in your Neon connection string. Use the **pooled** endpoint (the one ending in `-pooler` in the hostname) for batch workloads — direct connections can't handle concurrent sessions well.

```bash
cp .env.example .env
# edit .env — set DATABASE_URL and HTTP_USER_AGENT
```

The SEC EDGAR API rejects anonymous traffic; you must put a real contact email in `HTTP_USER_AGENT` or you'll get 403s.

### 4. Bootstrap schema

```bash
python -m brokercheck_extractor.cli init-db
```

This creates four tables:

| Table | Role |
| --- | --- |
| `firms_input` | Your existing 3K-firm table (pipeline only **reads** from it) |
| `firm_profile` | Domain 1 output, keyed by CRD |
| `focus_report` | Domain 2 output, keyed by (CRD, period_ending) — one row per filing |
| `firm_record` | Merged per-firm row with YoY derivations |
| `parse_errors` | Dead-letter queue |

**If your existing `firms_input` table lives in the same Neon database under a different name, either point the ORM at it by editing the `__tablename__` on `FirmInput` in `storage/db.py`, or create a view named `firms_input` pointing at your real table.** The only columns we need from it are `firm_name` and `crd_number`.

---

## Running the pipeline

```bash
# Smoke test against 5 firms (don't write raw PDFs to disk)
python -m brokercheck_extractor.cli run --limit 5

# Full 3K-firm run, saving raw PDFs for audit
python -m brokercheck_extractor.cli run --save-pdfs

# Reprocess only firms your upstream has tagged a specific status
python -m brokercheck_extractor.cli run --where-status=active
```

### Delta detection

Each FINRA PDF has its SHA-256 hash stored in `firm_profile.raw_pdf_hash`. On subsequent runs, if the hash hasn't changed, the FINRA parse is skipped and only SEC filings are refreshed. This keeps a full re-run cheap once the initial pass is done.

### Concurrency and politeness

`MAX_CONCURRENCY` (default 5) caps the number of simultaneously in-flight firms. Each firm does up to 3 HTTP calls (FINRA PDF + SEC CIK search + SEC filing download), so effective request rate at default settings is ≈10–15 req/s across both hosts. FINRA tolerates this; SEC explicitly caps anonymous clients at 10 req/s — if you need to go wider, get an EDGAR API key.

Backoff on 429/503 is automatic (exponential via `tenacity`, honoring `Retry-After`).

### Monitoring a run

```bash
# Progress is logged every 25 firms. To tail while running:
python -m brokercheck_extractor.cli run --log-level=INFO 2>&1 | tee run.log

# Check the DLQ after the run
psql $DATABASE_URL -c "SELECT source, stage, error_type, COUNT(*) FROM parse_errors GROUP BY 1,2,3;"
```

---

## One-off utilities

```bash
# Parse a local BrokerCheck PDF and dump JSON (useful for debugging)
python -m brokercheck_extractor.cli parse-one --finra-pdf ./firm_5393.pdf

# Parse a local X-17A-5 PDF
python -m brokercheck_extractor.cli parse-one --focus-pdf ./xfocus_andpartners.pdf

# Download a BrokerCheck PDF by CRD to a local file
python -m brokercheck_extractor.cli fetch-crd 5393 --out firm_5393.pdf
```

---

## Architecture notes

### Why no Playwright / browser automation?

FINRA has an undocumented-but-stable JSON search API (`api.brokercheck.finra.org/search/firm`) and the PDFs sit at a deterministic URL: `https://files.brokercheck.finra.org/firm/firm_{CRD}.pdf`. Since you already have the CRDs in Neon, the search call isn't even needed — we hit the PDF URL directly. No browser, no Selenium, no Playwright.

SEC EDGAR exposes `data.sec.gov/submissions/CIK{10-digit-zero-padded}.json` with the full filing history as structured data. Same deal — no scraping.

### Layered PDF extraction

Inside `parsers/base.py`:

1. `pdfplumber.extract_text()` first — handles 95% of modern BrokerCheck reports cleanly.
2. `PyMuPDF (fitz)` fallback when pdfplumber returns very little text (common on multi-column layouts).
3. OCR (`pdf2image` → `pytesseract`) when both return less than 50 characters on a visually populated page. X-17A-5 facing pages nearly always need this; FINRA PDFs essentially never.

### Section anchoring

The FINRA parser doesn't regex over the full document. It splits the text into named sections (`Firm Profile`, `Types of Business`, `Clearing Arrangements`, etc.) using stable FINRA headers, then runs a section-specific extractor on each chunk. When FINRA changes the format of one section, only that extractor breaks.

### Clearing classification with raw-text override

The client spec requires that when we can't confidently classify Self-Clearing vs Fully Disclosed, we fall back to the raw parsed paragraph instead of guessing. `derivation/clearing_classifier.py` does exactly this: three passes (self-clearing phrase → named clearing-firm introducer → explicit non-self-clearing language), and emits `ClearingType.UNKNOWN` plus the raw text when none match. The raw text is **always** preserved in `firm_profile.clearing_raw_text`.

### Idempotency

Every write path uses Postgres `ON CONFLICT DO UPDATE` keyed on the natural key (CRD for firm_profile / firm_record, CRD+period_ending for focus_report). Safe to re-run at will — no manual cleanup between runs.

---

## Test coverage

```bash
pytest -v
```

13 regression tests run against real FINRA (Charles Schwab CRD 5393) and SEC (&Partners LLC 2025 Part III) PDFs validating:

- CRD, SEC#, firm name, registration status
- Formation date (1971-04-01), SEC registration date (1971-06-13)
- Types of business total and list (no preamble leakage)
- All 8+ officers with multi-line positions preserved
- Clearing statement and `SELF_CLEARING` classification for Schwab
- SEC file number, period, contact name/title/email/phone
- Auditor and PCAOB ID
- Total assets / total liabilities / members' equity (with balance-sheet identity check)
- YoY growth happy path, missing prior, zero prior

Fixtures are shipped in `fixtures/` — tests don't hit the network.

---

## Known edge cases handled

- **Terminated/legacy firms** (e.g., CRD 10997 R H Securities): most sections emit "Information not available — see Summary Page". Parser returns `None` or empty collections rather than raising.
- **X-17A-5 Part III filings that lack a Net Capital computation**: `net_capital` is still extracted if the figure appears elsewhere in the notes; otherwise stays `None` and YoY reports `insufficient_data`.
- **Multi-page officer blocks**: FINRA wraps officer descriptions across page boundaries. The normalizer strips page footers and continuation headers (`Direct Owners and Executive Officers (continued)`) before block splitting.
- **OCR artifacts on notarized X-17A-5 pages**: the title extractor uses a keyword-anchored phrase expander that cleanly pulls `"FinOp and Controller"` out of OCR-merged lines like `"St. Louis County FinOp and Controller"`.

---

## Next steps the client may want

- **Migrate `firm_profile.officers` and `introducing_arrangements` to normalized tables** if you need to query them relationally. They're JSON columns now for flexibility.
- **Add EDGAR API key** if you need to go above 10 req/s.
- **Wire up to your manager agent / FastAPI layer** — the DAL in `storage/db.py` is the obvious integration point.
