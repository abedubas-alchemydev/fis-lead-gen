# Runbook: bank-contact Apollo enrichment (Tier-2, paid)

How the people extracted from OCC charter-application PDFs
(`bank_contacts`) get their **email / phone / title** filled from Apollo —
the paid second tier on top of the free extractor
(`scripts/watch_bank_charters.py --extract-contacts`, see
[`bank-charter-watch.md`](./bank-charter-watch.md)).

## TL;DR

`scripts/enrich_bank_contacts.py` walks `bank_contacts` rows that have **no
email and have never been attempted**, looks each person up on **Apollo
`/people/match`** (~1 credit per lookup) anchored to their bank's name +
website domain, and fills `email` / `phone` / `title` **only where currently
NULL** — a value extracted from the filing itself is never overwritten.
Acceptance is conservative (close name match + plausible org association);
everything else is **rejected and logged**. Every decided lookup stamps
`enriched_at` + `enrich_status` (`matched` | `no_match`), so **re-runs never
re-spend a credit**. **Dry-run is the default** and makes **zero** Apollo
calls; `--apply` executes; `--limit` (default **50**) hard-caps lookups per
run. Service logic: `backend/app/services/bank_contact_enrichment.py`.

- **Volume context:** ~59 banks / ~40 contacts today, so even an uncapped
  first pass is small — but the cap stays on anyway (paid-job convention).
- **Expect partial coverage.** These are brand-new entities; Apollo often
  hasn't indexed the bank (or lists the person at their previous employer),
  so a large `no_match` share is normal, not a failure.

## What one run does

1. **Plan (read-only, zero Apollo calls).** Eligibility is pure SQL:
   `email IS NULL AND enriched_at IS NULL`, joined to `banks` for the org
   context. Ordered highest-value first — `contact_person` (the person the
   filing says to call), then `proposed_officer`, `organizer`, `counsel` —
   then truncated to `--limit`. The plan table (contact, role, bank, org
   query, domain, missing fields) is printed either way.
2. **Execute (`--apply` only; ~1 credit per row).** For each planned
   contact, one `POST /people/match` with `first_name` + `last_name` +
   `organization_name` (bank name with `, N.A.` / `National Association` /
   `(In Organization)` suffixes stripped) + `domain` (from `banks.website`
   when present). Then the two acceptance gates:
   - **Name**: normalized equality or Levenshtein ≤ 2 on the
     `first last` composite — tolerates PDF text-layer artifacts
     (`Hirshrnan` → `Hirshman`) without accepting a different human.
   - **Org**: Apollo's organization for the person matches the bank
     (normalized-name equality / distinctive-token containment, or website
     domain equality). No org info → reject.
3. **On accept:** fill `email` / `phone` / `title` where NULL (Apollo's
   `email_not_unlocked@…` placeholder is never persisted). If the accepted
   Apollo full name is 1–2 edits from the stored name, the stored name is
   **corrected** and a provenance note is appended to `context_snippet`
   (e.g. `[name corrected via Apollo match; PDF rendered 'John
   Hirshrnan']`) so the original PDF rendering survives as the audit
   trail. Stamp `enrich_status='matched'`.
4. **On reject / no result:** write nothing, stamp
   `enrich_status='no_match'` — logged with the reason (`no_person` /
   `name_mismatch` / `org_mismatch`).
5. **Commits are per-row**, so an interrupt loses only the in-flight
   contact and the next run resumes automatically off the stamps.

Final line of every run (dry-run prints it with zeros):

```
bank_contacts_enrich: eligible=N looked_up=N matched=N emails_added=N phones_added=N titles_added=N names_corrected=N credits_used≈N
```

## Cost model & guardrails

| Guardrail | Behavior |
| --- | --- |
| Dry-run default | No `--apply` → plan only, **zero** Apollo calls, zero writes. |
| `--limit` (default 50) | Hard cap on paid person lookups per run. |
| Idempotency stamps | `enriched_at`/`enrich_status` per decided row → a credit is spent **at most once per contact**, ever. |
| Provider errors | Timeouts / 429 / 5xx retry with backoff; on exhaustion the row is left **unstamped** (retried next run) and after **3 consecutive** provider errors the batch aborts (dead key can't hammer the table). |
| Never overwrite | Extracted email/phone/title always win over Apollo's view. |
| `credits_used≈` | Counts 200-responses that returned a person (Apollo bills match-with-result; clean no-matches are typically free) — treat as an upper-bound estimate. |

Worst-case spend per run ≈ `min(eligible, --limit)` credits. With today's
volume (~40 contacts) a full first pass is ≤ 40 credits; steady-state runs
only touch newly extracted contacts.

## How to run

Secrets come from the **environment only** — never pass a DSN or API key
as an argument (argv leaks into shell history and `ps`).

```bash
cd <repo-root>

# Dry-run (default): print the plan, spend nothing. APOLLO_API_KEY not needed.
DATABASE_URL="$STAGING_DSN" \
python scripts/enrich_bank_contacts.py

# Execute, capped at the default 50 lookups.
DATABASE_URL="$STAGING_DSN" APOLLO_API_KEY="$APOLLO_KEY" \
python scripts/enrich_bank_contacts.py --apply

# Cost-bounded smoke test first (recommended for the first prod pass).
DATABASE_URL="$PROD_DSN" APOLLO_API_KEY="$APOLLO_KEY" \
python scripts/enrich_bank_contacts.py --apply --limit 5
```

Per-environment DSNs follow the repo convention: local dev →
`backend/.env`'s `DATABASE_URL`; staging → the value of
`DATABASE_URL_BACKEND_STAGING`; production → `DATABASE_URL_BACKEND`
(export the chosen one AS `DATABASE_URL` for the run, as above). Exit code
`2` = missing required env. In CI the suite mocks all Apollo HTTP;
`APOLLO_API_KEY` for real runs lives with the other provider secrets.

### One-off staging pass (copy/paste)

```bash
DATABASE_URL="$DATABASE_URL_BACKEND_STAGING" APOLLO_API_KEY="$APOLLO_KEY" \
python scripts/enrich_bank_contacts.py --apply --limit 50
```

## Operational notes

- **Re-attempting rows on purpose.** The stamps are deliberate one-shots
  (a `no_match` today stays skipped even after Apollo indexes the bank).
  To re-attempt a slice, clear the stamps and re-run:

  ```sql
  UPDATE bank_contacts
     SET enriched_at = NULL, enrich_status = NULL
   WHERE enrich_status = 'no_match'
     AND bank_id IN (SELECT id FROM banks WHERE charter_status = 'opened');
  ```

  (Newly *opened* banks are the natural retry cohort — that's when Apollo
  tends to pick them up.)
- **Name corrections are audited.** Every corrected row keeps the PDF's
  original rendering inside `context_snippet`; nothing is lost.
- **Schema**: migration `20260702_0004` (additive, nullable
  `enriched_at` + `enrich_status`); the bank detail API payload is
  unchanged.
- **Monitoring a run**: reject reasons and per-row MATCH/REJECT lines go
  to the standard logger (`app.services.bank_contact_enrichment`); the
  summary line is the last stdout line, grep-able as
  `bank_contacts_enrich:`.
