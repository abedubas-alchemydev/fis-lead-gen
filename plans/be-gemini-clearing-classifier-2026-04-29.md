# BE — Gemini-based clearing classifier (task #19)

**Date:** 2026-04-29
**Branch:** `feature/be-gemini-clearing-classifier`
**Source:** `cc-cli-01.txt` + `reports/clearing-classification-audit-2026-04-28.md`

## Problem

`backend/app/services/classification.py::determine_clearing_classification()` has three structural bugs (per audit):

1. **Inverted Self-Clearing semantics.** Returns `true_self_clearing` only when the FINRA text says the firm does *not* hold/maintain — opposite of Deshorn's canonical definition.
2. **No Omnibus detection.** Top-level decision returns only `{true_self_clearing, introducing, unknown}`.
3. **Two parallel classifiers writing to two columns.** Regex (FINRA → `broker_dealer.clearing_classification`) vs LLM (FOCUS PDF → `clearing_arrangements.clearing_type`). Master-list reads one, detail page reads the other → users see different labels in different surfaces.

Net effect: ~119 pages of `unknown` firms in the master list, most of which are real Self-Clearing firms dropped by the inverted Gate 1.

## Solution

Single LLM-based classifier prompted with Deshorn's canonical definitions verbatim. Reads BOTH the FINRA `firm_operations_text` AND the FOCUS report text. Writes to a single canonical column with a four-value enum (plus `needs_review` for low-confidence rows).

### Deshorn's canonical definitions (used verbatim in the LLM prompt)

- **Fully Disclosed** — firm reveals its clearing arrangement with a national service.
- **Self-Clearing** — firm holds/maintains securities for other broker-dealers.
- **Omnibus** — firm has multiple clearing arrangements AND clears for other companies; must also be self-clearing.

## Design decisions

### Canonical column

Keep `broker_dealer.clearing_classification` (already `String(32)`, indexed). Expand its accepted values to the canonical set.

| Old enum (broken) | New enum (canonical) |
|---|---|
| `true_self_clearing` | `self_clearing` |
| `introducing` | `fully_disclosed` |
| `unknown` | `unknown` |
| (none) | `omnibus` |
| (none) | `needs_review` |

Column type stays `String(32)` — no Postgres `ENUM` to ALTER, just a value-set change. The new constraint is enforced application-side via the `Literal[...]` type on the LLM client and the migration's data backfill.

### Migration backfill (logical, applied in the new migration's `upgrade()`)

The audit confirmed the old labels were inverted, so we cannot trust them as-is. We force a fresh classification by flagging legacy rows for review:

```sql
-- 1. true_self_clearing was inverted — flag for re-classification
UPDATE broker_dealers
SET clearing_classification = 'needs_review'
WHERE clearing_classification = 'true_self_clearing';

-- 2. unknown is over-populated due to the inverted Gate 1 — flag for re-classification
UPDATE broker_dealers
SET clearing_classification = 'needs_review'
WHERE clearing_classification = 'unknown';

-- 3. introducing → fully_disclosed if a clearing partner is known, else needs_review
UPDATE broker_dealers
SET clearing_classification = CASE
    WHEN current_clearing_partner IS NOT NULL THEN 'fully_disclosed'
    ELSE 'needs_review'
END
WHERE clearing_classification = 'introducing';
```

`downgrade()` is best-effort: there is no clean reverse map (the old labels were broken). The downgrade docstring documents this.

### New service: `backend/app/services/clearing_classifier.py`

```python
@dataclass(frozen=True, slots=True)
class ClearingClassificationResult:
    value: str           # 'fully_disclosed' | 'self_clearing' | 'omnibus' | 'unknown'
    confidence: float    # 0.0 .. 1.0
    reasoning: str       # short rationale from the LLM

async def classify(
    firm_operations_text: str | None,
    focus_report_text: str | None,
) -> ClearingClassificationResult: ...
```

- **Provider routing**: same pattern as `services/llm_parser.py` — Gemini default, OpenAI fallback. Reuses `GeminiResponsesClient` / `OpenAIResponsesClient` via a small new method on each (text-only generateContent / responses call — no PDF).
- **Prompt**: Deshorn's three definitions VERBATIM at the top, then two labeled input sections (FINRA / FOCUS) with the actual texts, then a strict JSON-schema response request.
- **Empty-input case**: if both inputs are null/empty → return `{value: "unknown", confidence: 0.0, reasoning: "No source text available."}` without calling the LLM.
- **Provider error / malformed JSON**: caller writes `needs_review`; the classifier itself surfaces the error via a sentinel result rather than raising into the pipeline (a single-firm failure must not kill the run).

### New config

`CLEARING_CLASSIFICATION_MIN_CONFIDENCE` (default `0.7`) on `core/config.py`. Tunable without redeploy. Mirrors the existing `clearing_extraction_min_confidence` / `financial_extraction_min_confidence` knobs.

### Pipeline integration

`services/classification.py::apply_classification_to_all(db)` is rewritten internally to use the new classifier. The function signature, name, and call sites stay unchanged so:

- `services/pipeline.py:167` → no change required.
- `api/v1/endpoints/settings.py:219` → no change required.
- `scripts/initial_load.py:139` → no change required (script files are forbidden in this PR anyway).

Per-BD logic inside the new `apply_classification_to_all`:

1. Pull `firm_operations_text` from the BD.
2. Pull `focus_report_text` from the latest `ClearingArrangement.clearing_statement_text` for that BD (cheap — already extracted by the FOCUS pipeline).
3. `result = await clearing_classifier.classify(...)`.
4. If `result.confidence >= settings.clearing_classification_min_confidence` and `result.value != "unknown"` → write `result.value`.
5. Otherwise → write `"needs_review"`.
6. Preserve the niche-restricted check (`classify_niche_restricted` on `types_of_business`) — unrelated to clearing classification, no reason to remove it.
7. Preserve the existing partner-extraction fallback when `current_clearing_partner` is null (helpers like `extract_clearing_partner_from_operations` stay).

### Deprecation of the synchronous regex top-level

`determine_clearing_classification(firm_operations_text)` is annotated as deprecated. Two callers remain:

- `services/classification.py::apply_classification_to_all` — rewritten in this PR, no longer calls it.
- `api/v1/endpoints/broker_dealers.py:680` (PR-forbidden path) — still calls it from a sync context.

**Conflict with the prompt**: cli-01 says "raise NotImplementedError." That would break the forbidden endpoint. Resolution: instead of `NotImplementedError`, the deprecated function returns `"needs_review"` — semantically correct (the next pipeline pass will reclassify with the LLM) and preserves the endpoint contract without modifying it. The function carries a deprecation docstring pointing to `clearing_classifier.classify()`.

Helper functions (`classify_self_clearing`, `classify_introducing`, `extract_clearing_partner_from_operations`, `classify_niche_restricted`) stay — `classify_niche_restricted` is still in active use, and the partner-extraction helper is still useful as a fallback.

## Tests

`backend/app/tests/services/test_clearing_classifier.py` (NEW). Uses `respx` to mock Gemini/OpenAI HTTP calls. Covers:

- Each of the four enum values returned with confidence ≥ threshold → correct `value` persisted.
- Confidence below threshold → caller writes `needs_review`.
- LLM provider error (httpx network error / 5xx) → sentinel result; caller writes `needs_review` and does NOT raise.
- Both inputs null → `{value: "unknown", confidence: 0}` without calling the LLM at all.
- LLM returns malformed JSON → sentinel result → `needs_review`.
- **Self-clearing inversion regression**: a firm whose FINRA text says "this firm holds and maintains funds and securities for other broker-dealers" classifies as `self_clearing`, NOT `unknown` (the old inverted regex behavior).

`backend/app/tests/services/test_classification.py` — only update if existing tests break (none currently exist; the helpers are tested indirectly through the pipeline tests).

## Scope boundary

- Backfill is NOT in this PR. cli-03 will run the full reclassification across all firms after this lands and Deshorn spot-checks ~30 firms.
- No frontend changes; no API changes (existing endpoints already read from the canonical column).
- No changes to scripts/, frontend/, fis-placeholder/, other model files, or other endpoint files.

## Auto-promote contract

This PR ships a migration. Phase B's auto-promote halts on migrations by design. STEP 12 of cli-01 detects the new file under `backend/alembic/versions/`, prints the manual-release instruction, and exits. Arvin opens the develop→main promote PR by hand after CI is green on develop.
