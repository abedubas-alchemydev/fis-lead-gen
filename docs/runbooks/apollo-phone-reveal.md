# Runbook: Apollo phone-reveal end-to-end

How Apollo phone numbers get onto our contact rows, why they were "missing"
for so long, and exactly what's wired on staging.

## TL;DR

Apollo's `/v1/people/match` returns `phone_numbers: []` by default. Phones
only land if the request includes **both** `reveal_phone_number=true` AND
`webhook_url`. The phones then arrive asynchronously as a POST to that
webhook URL minutes later — not in the sync response.

PR #419 audited Apollo and concluded our plan didn't return phones. That
was wrong: our key was always capable; we just weren't asking. PR #586
(2026-05-28) wired the opt-in. Today (2026-05-29) the staging deploy
delivers phones for ~16 of 27 Vanguard officers per gap-fill run.

## What's deployed

| Layer | Where | What it does |
|---|---|---|
| Apollo provider | `backend/app/services/contact_discovery/apollo_match.py` | Adds `reveal_phone_number: true` + `webhook_url` to `/people/match` payload when both `apollo_webhook_secret` and `public_base_url` are configured. Extracts `person.id` from the sync response into `DiscoveryResult.apollo_person_id`. |
| Merge | `backend/app/services/contact_discovery/orchestrator.py:_merge_discovery_results` | Carries `apollo_person_id` through the chain merge (first non-null in chain order, same shape as `linkedin_url`). |
| Persistence | `backend/app/models/{advisor,executive,investor}_contact.py` | New `apollo_person_id String(64) nullable indexed` column on all three tables (migration `20260528_0065`). |
| Webhook | `backend/app/api/v1/endpoints/webhooks_apollo.py` | `POST /api/v1/webhooks/apollo/{secret}/phone-reveal`. Walks all three contact tables by `apollo_person_id`, appends to `phones` JSONB (dedupe by sanitized value), fills NULL scalar `phone` from highest-confidence new hit. Idempotent for Apollo retries. |
| Gap-fill button | `frontend/components/advisor-list/channel-icon-cell.tsx` + `advisor-detail-client.tsx` | "Gap-fill contacts" on `/advisor-list/{id}`. POST `/api/v1/investment-advisors/{id}/gap-fill-contacts`, poll, render. 30-day cooldown on `last_gap_fill_attempt_at`. |

## Apollo's webhook contract (per their docs + what we observed)

The sync `/people/match` response includes a `person.id` (MongoDB-style ObjectId):

```json
{ "person": { "id": "587cf802f65125cad923a266", "email": "...", "linkedin_url": "...", "phone_numbers": [] } }
```

Minutes later, Apollo POSTs:

```json
{
  "status": "success",
  "total_requested_enrichments": 1,
  "unique_enriched_records": 1,
  "missing_records": 0,
  "credits_consumed": 1,
  "people": [
    {
      "id": "587cf802f65125cad923a266",
      "phone_numbers": [
        {
          "raw_number": "+1 555-123-4567",
          "sanitized_number": "+15551234567",
          "type_cd": "mobile",
          "confidence_cd": "high",
          "status_cd": "..."
        }
      ]
    }
  ]
}
```

Observed delivery facts:
- Source IPs: `34.96.x.x` range (Apollo's egress)
- User-Agent: `Mechanize/2.8.1 Ruby/3.4.9p82` (Apollo's webhook stack)
- **No signature header** — Apollo doesn't sign webhooks. Security relies on the 256-bit secret in the URL path.
- **Multiple callbacks per request** — Apollo can send status pings (no `people`) and per-person deliveries separately. The handler 200s on any payload it understood so Apollo's retry budget doesn't get burned.

## Staging configuration (set 2026-05-28)

### Secret Manager

```bash
# Already created. To rotate:
openssl rand -hex 32 | gcloud secrets versions add APOLLO_WEBHOOK_SECRET --data-file=-
gcloud run services update fis-backend-staging --region=us-central1 \
  --update-secrets=APOLLO_WEBHOOK_SECRET=APOLLO_WEBHOOK_SECRET:latest
```

### Cloud Run env vars on `fis-backend-staging`

```
APOLLO_WEBHOOK_SECRET <- secretKeyRef APOLLO_WEBHOOK_SECRET:latest
PUBLIC_BASE_URL=https://staging-dox.alchemydev.io/api/backend
```

### Why `PUBLIC_BASE_URL` points at the frontend domain (not the backend Cloud Run URL)

`fis-backend-staging` is locked down — IAM only allows the compute SA as
invoker, no `allUsers`. Adding `allUsers` is blocked at the GCP org level
(`iam.allowedPolicyMemberDomains` constraint) and only the org policy admin
can lift it.

Instead, Apollo's POST routes through the existing FE catch-all proxy at
`frontend/app/api/backend/[...path]/route.ts`. The proxy:
- Is publicly reachable on the custom domain (`staging-dox.alchemydev.io`)
- Forwards to `fis-backend-staging` with an OIDC token (the compute SA the
  backend's IAM accepts)
- Passes request body and headers through verbatim

So Apollo POSTs to `https://staging-dox.alchemydev.io/api/backend/api/v1/webhooks/apollo/<secret>/phone-reveal`. The `api/backend` segment is the proxy prefix; the rest is the backend path.

If the backend ever gets `allUsers` (e.g. the org policy is lifted), change `PUBLIC_BASE_URL` to the raw Cloud Run URL and Apollo will hit the backend directly — no code change needed.

## Verifying the wiring

### Is the endpoint reachable?

```bash
SECRET=$(gcloud secrets versions access latest --secret=APOLLO_WEBHOOK_SECRET)

# Wrong secret -> 404 (deliberately the same response as "missing endpoint" so probers can't distinguish)
curl -X POST "https://staging-dox.alchemydev.io/api/backend/api/v1/webhooks/apollo/wrong/phone-reveal" \
  -H "Content-Type: application/json" -d '{"people": []}'

# Right secret -> 200 with {"rows_updated":0,"phones_added":0}
curl -X POST "https://staging-dox.alchemydev.io/api/backend/api/v1/webhooks/apollo/$SECRET/phone-reveal" \
  -H "Content-Type: application/json" -d '{"people": []}'
```

### Has Apollo been calling us?

```bash
gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="fis-backend-staging"
  AND httpRequest.requestUrl=~"phone-reveal"
  AND timestamp>="2026-05-28T12:00:00Z"' \
  --limit=200 --format="value(timestamp,httpRequest.status)"
```

### What did the handler do on each call?

After PR #590 wired root logger to stdout:

```bash
gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="fis-backend-staging"
  AND textPayload=~"apollo phone-reveal"' \
  --limit=50 --format="value(timestamp,textPayload)"
```

Expected lines:
```
INFO [app.api.v1.endpoints.webhooks_apollo] apollo phone-reveal: processed N person(s), updated M row(s), added P phone(s)
INFO [app.api.v1.endpoints.webhooks_apollo] apollo phone-reveal: no matching contact row for person id <id> (may have been deleted, or arrived before the sync write committed)
```

### Which contacts actually got Apollo-revealed phones?

```sql
SELECT
  'advisor' AS table, name, phone,
  (SELECT array_agg(DISTINCT entry->>'source')
   FROM jsonb_array_elements(phones) entry) AS phone_sources
FROM advisor_contacts WHERE phones::text LIKE '%apollo_phone_reveal%'
UNION ALL SELECT 'executive', name, phone,
  (SELECT array_agg(DISTINCT entry->>'source')
   FROM jsonb_array_elements(phones) entry)
FROM executive_contacts WHERE phones::text LIKE '%apollo_phone_reveal%'
UNION ALL SELECT 'investor', name, phone,
  (SELECT array_agg(DISTINCT entry->>'source')
   FROM jsonb_array_elements(phones) entry)
FROM investor_contacts WHERE phones::text LIKE '%apollo_phone_reveal%';
```

## Common ops

### Re-trigger for a specific advisor within the 30-day cooldown

```sql
-- clear cooldown
UPDATE investment_advisors SET last_gap_fill_attempt_at = NULL WHERE id = <ID>;
```

Then click "Gap-fill contacts" on `/advisor-list/<ID>`.

### Trigger from a terminal (bypasses better-auth)

Useful when there's no user session available. Run from `backend/`:

```python
# Env required: DATABASE_URL (postgresql+asyncpg:// — psycopg-async has DNS
# resolver issues on Windows), APOLLO_API_KEY, APOLLO_WEBHOOK_SECRET,
# PUBLIC_BASE_URL, CONTACT_DISCOVERY_CHAIN=apollo_match (or full chain),
# CONTACT_DISCOVERY_MIN_CONFIDENCE=30
import asyncio, sys
if sys.platform == "win32" and sys.version_info < (3, 14):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from app.db.session import SessionLocal
from app.models.pipeline_run import PipelineRun
from app.services.advisor_refresh_orchestrator import (
    GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME, _run_gap_fill_contacts,
)
# Create PipelineRun, call _run_gap_fill_contacts(run_id, advisor_id, "manual_trigger").
```

### Bulk gap-fill the whole 13F-filer cohort

`scripts/gap_fill_investment_advisors.py` runs the full 5-sub-pipeline gap-fill
across every 13F-filer advisor in AUM-descending order. It calls the existing
`_run_enrich_contacts` (which has the idempotency guard) — does NOT yet call
`_run_gap_fill_contacts`. To bulk-trigger phone-reveal across the cohort, the
bulk script needs an update (TODO).

## Cost

Apollo bills **per mobile reveal** — a separate credit budget from match
credits. Typical SaaS pricing for mobile reveals: **$0.10–$0.50 per reveal**.

A single advisor with 27 officers ≈ up to 27 reveals ≈ $3–$15. The 13F-filer
cohort is 3,107 advisors × ~25 officers = ~77,000 potential reveals. At
$0.30/reveal that's ~$23K of credit burn. **Before bulk-gap-filling, confirm
the per-reveal price with the Apollo CSM.**

The endpoint's 30-day cooldown on `last_gap_fill_attempt_at` is the load-
bearing guard against accidental re-burns.

## Reading provenance on a row

Two distinct fields, often confused:

- `advisor_contacts.source` — which provider wrote the ROW (name/email/linkedin).
  Values: `pdl`, `apollo`, `hunter`, `snov`, `adv` (names-only fallback).
- `advisor_contacts.phones[*].source` — which provider wrote each PHONE entry
  in the JSONB. Phones from the new flow are always tagged
  `apollo_phone_reveal`.

A row with `source='pdl'` and `phones=[{value:'+1555...', source:'apollo_phone_reveal'}]`
means: PDL originally wrote the row (name/email), then Apollo's phone-reveal
webhook added the phone later.

## Failure modes and what they look like

| Symptom | Likely cause |
|---|---|
| `403` on the webhook URL | Cloud Run IAM rejected; `PUBLIC_BASE_URL` points at the locked-down backend URL instead of the FE proxy |
| `503` on the webhook URL | `APOLLO_WEBHOOK_SECRET` not configured on the serving revision (yet) |
| `404` on the webhook URL with a real Apollo POST | Secret mismatch; rotate the env var and the Secret Manager value, then re-deploy |
| Webhook returns 200 but no rows update | The `person.id` in the payload doesn't match any `apollo_person_id` on the contact tables. Either the row was deleted, or the sync write that should have stamped `apollo_person_id` failed silently. Check Cloud Run logs for the chain hit. |
| Apollo never calls back | The `webhook_url` we sent on the original `/people/match` was wrong. Check the most recent outbound Apollo request body. |
| Phones land on the wrong row | `apollo_person_id` collision (extremely unlikely with 24-char Mongo ids) OR the same person was matched against the wrong firm by Apollo. The orchestrator's `_firm_name_matches` guard is supposed to catch this on the sync side. |

## Related code

- PR #577 — Gap-fill endpoint + channel icon UI
- PR #581 — "Gap-fill contacts" button on advisor detail
- PR #586 — Apollo reveal flag + webhook handler (the meat)
- PR #590 — Root logger → stdout so handler INFO lines show in Cloud Run logs
