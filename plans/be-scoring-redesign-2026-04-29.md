# #21 — Hot/Warm/Cold scoring redesign vs ACG ICP

**Date:** 2026-04-29
**Branch:** `feature/be-scoring-redesign-acg-icp`
**Issue:** #21 (2026-04-27 client meeting follow-up)
**Builds on:** #19 classifier rewrite, spot-checked >27/30 agreement

---

## Why

ACG's Ideal Customer Profile ranks broker-dealers by *displacement difficulty*:

| Tier | Displacement difficulty | ACG priority |
|---|---|---|
| Firms using a tracked competitor (Pershing, Apex, Hilltop, RBC, Axos, Vision) | Lowest | **Highest** — easy switch |
| Fully-disclosed firms with non-tracked clearing partner | Medium | Moderate — known buyer, unknown vendor |
| Omnibus | Medium-high | Moderate |
| Self-clearing institutionals | Highest | Lower — hard sell, but still possible |
| `needs_review` / null | Unknown | Skip |

Today's scoring formula gives equal-ish weight to growth, clearing, health, and recency. That doesn't reflect ACG's "competitor-user-first" priority. This PR re-weights so the **competitor-relationship signal dominates** the composite.

## Schema reality check

The brief was drafted assuming a JSONB `weights` column on `scoring_settings`. The actual schema (per migration `20260409_0006_sprint6_contacts_scoring_export.py`) has **four integer weight columns** in basis points:

```
scoring_settings:
  - net_capital_growth_weight        INT (default 35)
  - clearing_arrangement_weight      INT (default 30)
  - financial_health_weight          INT (default 20)
  - registration_recency_weight      INT (default 15)
                                      total = 100 bps
```

Brief constraint: **no schema change, no model edits**. So we keep the four columns and remap their semantic role to fit ACG's 6-component model:

| Existing column (kept) | New default | Drives component(s) | Internal split |
|---|---|---|---|
| `clearing_arrangement_weight` | **60** (was 30) | `competitor_match` + `classification` | 40 / 20 split → 0.667 / 0.333 within bucket |
| `financial_health_weight` | **15** (was 20) | `net_capital` | 100% within bucket |
| `net_capital_growth_weight` | **10** (was 35) | `filing_recency` | 100% within bucket |
| `registration_recency_weight` | **15** (was 15) | `firm_size` + `finra_status` | 10 / 5 split → 0.667 / 0.333 within bucket |
| **Total** | **100 bps** | | |

Conceptual ACG breakdown that the new defaults encode:

```
competitor_match  0.40   ← clearing bucket (60) × 0.667
classification    0.20   ← clearing bucket (60) × 0.333
net_capital       0.15   ← health bucket (15)
filing_recency    0.10   ← growth bucket (10)
firm_size         0.10   ← recency bucket (15) × 0.667
finra_status      0.05   ← recency bucket (15) × 0.333
                  ----
                  1.00
```

Admin keeps four levers in `/settings`. The internal sub-component split is a code-level constant.

## Component scoring functions

Each returns `0.0`–`1.0`.

### `score_competitor_match(firm, lookup)`
- Returns `1.0` if `firm.current_clearing_partner` matches any active `competitor_provider` (case-insensitive substring on `name` + each entry in `aliases`).
- Returns `0.0` otherwise (including null partner).

### `score_classification(firm)`
Uses `firm.clearing_classification` (the #19 classifier output):

| Value | Score |
|---|---|
| `fully_disclosed` | 1.0 |
| `omnibus` | 0.7 |
| `self_clearing` | 0.3 |
| `needs_review` / null | 0.0 |

Rationale: fully-disclosed firms are confirmed buyers of clearing services; omnibus is moderate; self-clearing is hard-sell; needs_review can't be acted on.

### `score_net_capital(firm)`
Uses `firm.latest_net_capital`. Log-scale normalized:

| Net capital | Score |
|---|---|
| `null` | 0.0 |
| `< $1M` | 0.2 |
| `$1M – $10M` | 0.5 |
| `$10M – $100M` | 0.8 |
| `≥ $100M` | 1.0 |

### `score_filing_recency(firm)`
Uses `firm.last_filing_date` and today's date:

| Days since filing | Score |
|---|---|
| `≤ 90` | 1.0 |
| `91 – 180` | 0.7 |
| `181 – 365` | 0.4 |
| `> 365` or null | 0.0 |

### `score_firm_size(firm)`
Uses `firm.branch_count` as the size proxy:

| Branch count | Score |
|---|---|
| `≥ 100` | 1.0 |
| `25 – 99` | 0.75 |
| `5 – 24` | 0.5 |
| `1 – 4` | 0.25 |
| `0` / null | 0.0 |

### `score_finra_status(firm)`
Uses `firm.is_deficient` and `firm.is_niche_restricted`:

| Deficient | Niche-restricted | Score |
|---|---|---|
| False | False | 1.0 |
| False | True | 0.5 |
| True | * | 0.2 |

## Composite formula

```
clearing_bucket = 0.667 * competitor_match + 0.333 * classification
health_bucket   = net_capital
growth_bucket   = filing_recency
recency_bucket  = 0.667 * firm_size + 0.333 * finra_status

W = clearing_arrangement_weight + financial_health_weight
  + net_capital_growth_weight + registration_recency_weight   # = 100 in default

composite = (
    clearing_arrangement_weight   * clearing_bucket
  + financial_health_weight       * health_bucket
  + net_capital_growth_weight     * growth_bucket
  + registration_recency_weight   * recency_bucket
) / W                                                          # 0.0 – 1.0

lead_score = round(composite * 100, 2)                         # 0.0 – 100.0
```

`W` divides through to keep the formula stable if an admin re-balances weights without keeping them at 100.

## Health-status thresholds (lead_priority)

Brief states:
- Hot: composite ≥ 0.70 → `lead_score ≥ 70`
- Warm: 0.40 ≤ composite < 0.70 → `40 ≤ lead_score < 70`
- Cold: composite < 0.40 → `lead_score < 40`

These are a small change from the current 75/45 thresholds (down to 70/40).

## What stays unchanged

- `calculate_yoy_growth` (still used by `services/focus_reports.py` and `services/focus_ceo_extraction.py`).
- `classify_health_status` (drives `broker_dealer.health_status`, separate from `lead_priority`).
- The 4-column schema of `scoring_settings`.
- The admin `/settings` UI.
- All endpoints, models, migrations beyond the new data-only one.
- `services/classification.py` (locked by #19).

## Caller update

Single touchpoint: `BrokerDealerRepository.refresh_lead_scores` in
`backend/app/services/broker_dealers.py:521`. The old call passes scalars
(`yoy_growth`, `clearing_type`, `is_competitor`, `health_status`,
`registration_date`). The new signature takes `firm`, `competitor_lookup`,
`weights`. We pre-build the lookup once before the per-row loop, then pass
`broker_dealer` itself.

This file is **not** in any parallel CLI's lane (cli03 owns the
`api/v1/endpoints/broker_dealers.py` *endpoint*, not the *service*). The
brief's "stay in your lane" rule covers parallel-CLI conflict avoidance,
and since the endpoint and service files are distinct, the change is safe.

## Data-only migration

`backend/alembic/versions/20260429_0010_update_default_scoring_weights.py`:

```sql
-- upgrade
UPDATE scoring_settings
SET clearing_arrangement_weight   = 60,
    financial_health_weight       = 15,
    net_capital_growth_weight     = 10,
    registration_recency_weight   = 15,
    updated_at                    = NOW()
WHERE settings_key = 'default';

-- downgrade
UPDATE scoring_settings
SET clearing_arrangement_weight   = 30,
    financial_health_weight       = 20,
    net_capital_growth_weight     = 35,
    registration_recency_weight   = 15,
    updated_at                    = NOW()
WHERE settings_key = 'default';
```

The downgrade restores the seed defaults from migration
`20260409_0006_sprint6_contacts_scoring_export.py` lines 44–47.

## Testing

`backend/app/tests/services/test_scoring.py` (new):

1. `score_competitor_match` — 1.0 for "Pershing LLC" via name match; 1.0 via alias; 0.0 for "Goldman Sachs"; 0.0 for null partner.
2. `score_classification` — 1.0/0.7/0.3/0.0/0.0 for fully_disclosed / omnibus / self_clearing / needs_review / null.
3. `score_net_capital` — boundary cases at $1M, $10M, $100M; null returns 0.0.
4. `score_filing_recency` — boundary at 90, 180, 365 days; null returns 0.0.
5. `score_firm_size` — boundaries at 100, 25, 5, 1; null returns 0.0.
6. `score_finra_status` — 1.0 / 0.5 / 0.2 truth table.
7. `calculate_lead_score` composite — fake `ScoringSetting` with custom weights; verify weighted sum is correct.
8. `classify_lead_priority` — boundary at 70 (hot/warm), 40 (warm/cold); null returns None.
9. **End-to-end ACG ICP scenario** — a firm using "Pershing LLC" as `current_clearing_partner` with `clearing_classification='fully_disclosed'`, healthy net capital, recent filing → scores Hot. A pure self-clearing firm with no competitor partner → scores Warm or Cold. A firm with `clearing_classification='needs_review'` and no other strong signals → scores Cold.

## Sequence

1. Branch off `origin/develop`.
2. Write `plans/be-scoring-redesign-2026-04-29.md` (this file).
3. Refactor `services/scoring.py` with the 6 component functions + new composite.
4. Update `services/broker_dealers.py:530` call site.
5. Author migration `20260429_0010_update_default_scoring_weights.py`.
6. Write `app/tests/services/test_scoring.py`.
7. `pytest app/tests/ -v` — full suite green.
8. Commit, push, open PR vs `develop`.
9. Phase A: CI + squash-merge.
10. Phase B: develop → main (atomic-ship since it's a data migration).
11. Smoke prod: hit `/api/v1/health`, query `scoring_settings` for new defaults.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Caller change in `services/broker_dealers.py` outside the brief's strict write list | The brief lists scoring.py + tests + migration as the *write target* but the function-rename creates an unavoidable caller change. The change is one block (~7 lines). Documented here. cli03 doesn't touch this file. |
| Score distribution shifts dramatically on first re-run after deploy | This IS the intent (re-prioritize toward ACG ICP). Spot-check 5 known firms post-deploy as the brief specifies. |
| Old tests using the prior `calculate_lead_score` signature break CI | None exist (verified by `glob backend/app/tests/services/test_scoring*.py` → no files). |
| Admin previously customized weights | The migration only touches the row where `settings_key = 'default'`. Custom rows are not modified. |
| Phase B auto-promote pattern HALTs because of new migration file | The brief explicitly bypasses the HALT — this is "atomic-ship" since the data migration affects only the same row the formula reads. We open the release PR manually. |

## Done when

- [ ] Plan file committed.
- [ ] `services/scoring.py` refactored with 6 component fns + new composite.
- [ ] `services/broker_dealers.py` call site updated.
- [ ] Data migration file committed and `alembic upgrade head` succeeds locally.
- [ ] Test file added; full pytest suite passes.
- [ ] Feature PR opened against `develop` with no AI attribution.
- [ ] Phase A: CI green, squash-merged.
- [ ] Phase B: release PR opened against `main`, AI scrub passed, merged.
- [ ] Prod deploy watched and health check returns 200.
- [ ] Default weights row in `scoring_settings` confirmed = 60/15/10/15.
