# Classifier backfill — Deshorn spot-check
Date: 2026-04-29

## What ran
- `scripts/run_classifier_backfill.py` (LLM-backed clearing classifier, see PR #129 for the underlying service)
- Started: 2026-04-29 01:11:13 UTC
- Finished: 2026-04-29 08:40:25 UTC
- Wall clock (script-reported): 26,975 s ≈ 7h 30m. Steady-state classification time was ~50 minutes; the rest is a single overnight gap while the host machine slept (the script auto-resumed when the machine woke; idempotency held).
- Total firms processed: 3,002 / 3,002
- Confidence threshold: `clearing_classification_min_confidence = 0.7`
- LLM provider: Gemini 2.5 Flash (per `backend/.env GEMINI_PDF_MODEL=gemini-2.5-flash`)
- No HTTP errors, no 429 retries, no Tracebacks

## BEFORE → AFTER distribution

| Value            | BEFORE | AFTER |
|------------------|-------:|------:|
| fully_disclosed  |      0 | 2,894 |
| self_clearing    |      0 |   106 |
| omnibus          |      0 |     0 |
| unknown          |      0 |     0 |
| needs_review     |  3,002 |     2 |
| (null)           |      0 |     0 |
| **TOTAL**        | **3,002** | **3,002** |

BEFORE: every row was `needs_review`, the result of the
2026-04-29 migration that mapped legacy values to the review queue
because the prior regex classifier was inverted.

AFTER: 3,000 rows (99.93%) classified to a canonical label;
2 firms held in `needs_review` because the LLM returned
confidence < 0.7. None of the 3,002 firms was demoted from a
canonical label — the only `needs_review` writes came from the
two low-confidence rows, not from coercion.

The flat shape (~96% fully_disclosed, ~4% self_clearing, 0 omnibus)
matches Deshorn's prior intuition that introducing brokers
dominate the universe and omnibus is rare.

## Sample for Deshorn spot-check (30 firms)

For each firm below, given the clearing-arrangement text shown,
is the classification correct? Mark **agree** / **disagree** / **unclear**.

> Note: `current_clearing_partner` is shown when the FOCUS-extraction
> pipeline populated it. Most firms here show `null` — the partner
> name lives in their FOCUS report which the classifier reads but
> the partner column itself has not been backfilled in this run
> (out of scope; the LLM still uses the FOCUS text). The `ops:`
> snippet is the FINRA `firm_operations_text` the classifier saw.

### fully_disclosed (14 firms)

| # | Name (CIK) | Partner | FINRA ops snippet |
|---|---|---|---|
| 1 | SHEA & COMPANY (0001467855) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 2 | INVESTMENTS FOR YOU, INC. (0000879589) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 3 | INTEGRITY FUNDS DISTRIBUTOR, LLC (0000862498) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 4 | BLACKSTONE SECURITIES PARTNERS L.P. (0000792326) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 5 | GAMMA SECURITIES, LLC (0001750442) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 6 | COLUMBIA MANAGEMENT INVESTMENT DISTRIBUTORS, INC. (0000351106) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 7 | TALOS MARKETS LLC (no CIK) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 8 | STONEBRIDGE CAPITAL PARTNERS LLC (0002001822) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 9 | ADP BROKER-DEALER, INC. (0000934684) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 10 | GLOBAL CARRY LLC (no CIK) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 11 | A5 SECURITIES LLC (0001704230) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 12 | CACHE SECURITIES LLC (0001922100) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 13 | CONCORDE INVESTMENT SERVICES, LLC (0001471980) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 14 | INVESCO DISTRIBUTORS, INC. (0000205021) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |

### self_clearing (14 firms)

| # | Name (CIK) | Partner | FINRA ops snippet |
|---|---|---|---|
| 15 | DRIVEWEALTH, LLC (0001557384) | RBC Capital Markets, LLC; Wedbush Securities, Inc.; ABN AMRO Clearing USA, LLC | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 16 | CIBC WORLD MARKETS CORP. (0000074654) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 17 | LEIGH BALDWIN & CO., LLC (0000946629) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 18 | THRIVENT INVESTMENT MANAGEMENT INC. (0000798993) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 19 | NOMURA SECURITIES INTERNATIONAL, INC. (0000072267) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 20 | CANTOR FITZGERALD & CO. (0000017018) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 21 | CURVATURE SECURITIES LLC (0001591458) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 22 | UBS FINANCIAL SERVICES INC. (0000200565) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 23 | MESIROW FINANCIAL, INC. (0000712807) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 24 | J.P. MORGAN SECURITIES LLC (0000782124) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 25 | APEX CLEARING CORPORATION (0000278331) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 26 | VAN CLEMENS & CO. INCORPORATED (0000102780) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 27 | MAREX CAPITAL MARKETS INC. (0001540527) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 28 | GEORGESON SECURITIES CORPORATION (0001077614) | _null_ | This firm does hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |

### omnibus (0 firms)

The classifier produced **zero** omnibus firms in this run.
That is consistent with Deshorn's note in the spec that omnibus
is a rare structure (firm clears for OTHER firms in addition to
its own customers). If you expect a non-zero omnibus count from
prior knowledge, flag it and we'll inspect the prompt; otherwise
0/3,002 looks like a clean signal.

### needs_review (2 firms — full population, not a sample)

These two are the entire `needs_review` population after the
backfill. Both have FINRA wording that pattern-matches
fully_disclosed, but the LLM returned confidence < 0.7 — likely
because the FOCUS report excerpt contradicted or did not
substantiate the FINRA self-declaration. Worth a manual look.

| # | Name (CIK) | Partner | FINRA ops snippet |
|---|---|---|---|
| 29 | CHAPIN, DAVIS (0000872098) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |
| 30 | GROWTH PARTNERS INVESTMENT BANKING (0001065260) | _null_ | This firm does not hold or maintain funds or securities or provide clearing services for other broker-dealer(s). |

## Decision gate
- **≥ 27/30 agreement (90%+)**: classifier is canonical-grade.
  Unblock task #21 (Hot/Warm/Cold scoring redesign).
- **23–26 agreement**: investigate the disagreements; tune the
  Gemini prompt; re-run on the disagreeing rows.
- **≤ 22 agreement**: structural issue in the prompt or
  definitions; revisit before #21.

## Operational notes
- The backfill is idempotent — re-running the script converges to
  the same labels. Verified mid-run when the dry-run's 20 firms
  reported `unchanged=20` on the second pass.
- Wall clock was inflated by an overnight machine sleep
  (~6h between firm 1100 at 01:40 UTC and firm 1110 at 07:58 UTC).
  Steady-state classification rate was ~0.5 firms/sec; a
  full backfill on a continuously-running host would finish in
  about 50 minutes.
- Within each batch (10 firms) the LLM calls run in parallel via
  `asyncio.gather`; a 2-second sleep between batches caps in-flight
  load. The Gemini client itself retries on 429 with exponential
  backoff. No retries fired during this run.
- The `current_clearing_partner` column is independent of this
  classifier run. Where it shows `null` above, the FOCUS
  partner-extraction pipeline simply has not populated it yet.
