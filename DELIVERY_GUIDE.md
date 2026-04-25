# Client Delivery Guide — Production Data Population

## Quick Start (Copy-Paste Commands)

Open a terminal in the project root:
```
D:\Deshorn Compliance Lead Generation Project\Web Application Deshorn
```

### Step 0: Verify Prerequisites

```bash
# Verify database has broker-dealers loaded
python -m scripts.check_db_state
```

You should see **3,000+ broker-dealers**. If you see 0, run Step 1 first.

---

### Step 1: Initial Load (ONLY if broker-dealers table is empty)

> Skip this if you already have 3,000+ BDs in the database.

```bash
python -m scripts.initial_load
```

**What it does:** Downloads all active U.S. broker-dealers from FINRA BrokerCheck + SEC EDGAR, merges them, and inserts into the database.

**Runtime:** 15–30 minutes  
**Result:** ~3,000 broker-dealers with CIK, CRD, name, state, registration data

---

### Step 2: Populate Financial Data + Clearing Data + Alerts

This is the main command. It runs all pipelines in sequence:

```bash
python -m scripts.populate_all_data
```

**What it does (in order):**
1. Seeds competitor providers (Pershing, Apex, Hilltop, RBC, Axos, Vision)
2. Extracts financial metrics from X-17A-5 PDFs via Gemini AI
3. Runs the clearing pipeline (extracts clearing partners from PDFs via Gemini)
4. Runs the filing monitor (creates Form BD + 17a-11 deficiency alerts)
5. Refreshes competitor flags and recalculates all lead scores

**Runtime:** 30–90 minutes (depends on batch size)  
**Requires:** `GEMINI_API_KEY` set in `backend/.env`

---

### Step 3 (Optional): Control Batch Sizes

If you want to process more/fewer BDs per run, edit `backend/.env`:

```env
# Financial extraction batch (default: process all BDs)
FINANCIAL_PIPELINE_OFFSET=0
FINANCIAL_PIPELINE_LIMIT=200

# Clearing extraction batch (default: process all BDs)
CLEARING_PIPELINE_OFFSET=0
CLEARING_PIPELINE_LIMIT=200

# Filing monitor batch
FILING_MONITOR_OFFSET=0
FILING_MONITOR_LIMIT=500
```

Set `LIMIT=` (empty) to process ALL broker-dealers.

To process the **next batch**, increment the offset:
```env
FINANCIAL_PIPELINE_OFFSET=200
FINANCIAL_PIPELINE_LIMIT=200
```

Then re-run: `python -m scripts.populate_all_data`

---

### Step 4: Run Individual Pipelines (if needed)

```bash
# Only financial metrics
python -m scripts.load_financials

# Only clearing pipeline
python -m scripts.run_clearing_pipeline

# Only filing monitor
python -m scripts.run_filing_monitor
```

---

### Step 5: Verify Data Population

```bash
python -m scripts.check_db_state
```

**What "good" looks like for a demo:**

| Metric                   | Target       |
|--------------------------|--------------|
| Broker-dealers           | 3,000+       |
| With financial data      | 60+          |
| With clearing partner    | 20+          |
| With health status       | 60+          |
| Hot leads                | 1+           |
| Warm leads               | 20+          |
| Filing alerts            | 30+          |
| Competitor providers     | 6+           |

---

### Step 6: Create a Demo Admin User

If you need an admin user for the demo:

1. Open the app at http://localhost:3000
2. Click "Create one" to sign up
3. Enter name, email, password
4. To promote to admin, run this SQL:

```sql
UPDATE "user" SET role = 'admin' WHERE email = 'your-demo-email@example.com';
```

Or via psql / your database tool connected to the Neon DB.

---

### Step 7: Start the Application

```bash
# Terminal 1: Start backend
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2: Start frontend
cd frontend
npm run dev
```

Open http://localhost:3000 in the browser.

---

## What the Client Will See

### Dashboard Home
- **4 KPI cards**: Total Active BDs, New BDs (30d), Deficiency Alerts, High-Value Leads
- **Activity Feed**: Recent Form BD and 17a-11 filing alerts
- **Clearing Distribution Chart**: Donut chart showing clearing provider market share

### Master List
- **3,000+ broker-dealers** in a searchable, sortable, filterable table
- **Financial Health badges**: Green (Healthy), Amber (OK), Red (At Risk)
- **Clearing Partner** column with COMPETITOR badges
- **Lead Priority** stars: Hot (gold), Warm (blue), Cold (gray)
- **Primary/Alternative/All** list toggle

### Firm Detail Page (click any firm name)
- Header bar with all badges
- Financial Overview with net capital trend chart
- Clearing Arrangements with EDGAR links
- Executive Contacts (from Apollo)
- Filing History timeline
- Registration & Compliance snapshot
- Deficiency Status

### Alerts Page
- Full filterable alert table (Type, Firm, Date, Priority, Status)
- Mark as read / Mark all as read
- Click-through to firm detail

### Export Page
- Restricted CSV export (9 PRD-approved fields only)
- 100-row cap, 3 exports/day limit
- Watermark footer

### Settings Page (Admin only)
- Scoring weight sliders (must sum to 100)
- Competitor provider list (add/edit/disable)
- Refresh Data button (triggers all pipelines)
- Pipeline status with recent runs and failure log

---

## Troubleshooting

### "No broker-dealers in database"
Run: `python -m scripts.initial_load`

### "Gemini extraction returns 0 results"
Check `backend/.env` has `GEMINI_API_KEY` set and `DATA_SOURCE_MODE=live`

### "Contact enrichment unavailable"
Check `backend/.env` has `CONTACT_ENRICHMENT_PROVIDER=apollo` and `APOLLO_API_KEY` set

### "Login redirects back to login"
Check that `BETTER_AUTH_SECRET` matches between `frontend/.env.local` and `backend/.env`

### "API calls fail with CORS"
Check `BACKEND_CORS_ORIGINS` in `backend/.env` includes your frontend URL

---

## Environment Files Reference

### backend/.env (key settings)
```
DATA_SOURCE_MODE=live
GEMINI_API_KEY=your-key
APOLLO_API_KEY=your-key
CONTACT_ENRICHMENT_PROVIDER=apollo
DATABASE_URL=postgresql+psycopg://...
```

### frontend/.env.local (key settings)
```
NEXT_PUBLIC_APP_URL=http://localhost:3000
BETTER_AUTH_SECRET=must-match-backend
DATABASE_URL=postgresql://...
RESEND_API_KEY=your-key
```

---

## Running tests

The pytest suite is split into two tiers via the `integration` marker.

### Default (unit) suite

Pure unit tests using `respx` for HTTP and `monkeypatch` / `MagicMock` for state. They never touch a real Postgres or external API and are safe to run on any machine that has the dev requirements installed. CI runs this tier on every push.

```bash
cd backend
pip install -r requirements-dev.txt
pytest app/tests/
```

`backend/pytest.ini` sets `addopts = -m "not integration"` so the default invocation deselects the integration tier automatically.

### Integration suite

Integration-marked tests hit the real Neon database and external providers (Apollo, Hunter, Snov, etc.). They are **deselected by default** because the staging and production Cloud Run services share one Neon database (see `CLAUDE.md` → "Staging caveat"), so an unguarded run from a developer laptop can write to the same rows the demo environment is reading from.

To run them explicitly — only when you have an isolated Postgres / API set wired into your local env — opt in with the marker:

```bash
cd backend
pytest app/tests/ -m integration -v
```

To run both tiers in one pass:

```bash
pytest app/tests/ -m "integration or not integration" -v
```

Do **not** add `-m integration` to the GitHub Actions workflow against the shared Neon DB until staging and production have separate Neon instances. That database split is the prerequisite for re-enabling integration tests in CI.

### Adding a new test

- A new test is **integration** if any of these is true:
  - it opens a real `AsyncSession` against the configured `DATABASE_URL`
  - it calls a real upstream HTTP API (Apollo, Hunter, Snov, Gemini, SEC EDGAR, FINRA, etc.)
  - it relies on Alembic migrations having been applied to a live Postgres
- A new test is a **unit** test if it stays mocked end-to-end (`respx` for HTTP, `monkeypatch` / `MagicMock` for state).
- Mark integration tests with a module-level guard at the top of the file:

```python
import pytest

pytestmark = pytest.mark.integration
```

Use `@pytest.mark.integration` per-function only when the file mixes both tiers. Prefer module-level — fewer lines changed and harder to forget when adding the next test.
