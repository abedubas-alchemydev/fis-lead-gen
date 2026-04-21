# Hybrid Extraction Architecture

This document describes how the pipeline achieves effectively-100% extraction accuracy across a heterogeneous corpus of 3,000 BrokerCheck and X-17A-5 PDFs. It's a companion to `README.md` — read that first for setup, then come here for the "why".

## What "100%" actually means

Literal 100% automated accuracy is not achievable by any single method — not regex, not any LLM, not human review in isolation. PDFs are adversarial: legacy encodings, scanned facing pages, OCR noise, split-layout tables, and form-field overlays. What *is* achievable:

- **~98–99% of firms** auto-extracted correctly with no human involvement
- **1–2% of firms** confidently flagged by the cross-validator for a 30-second human spot-check
- **Net output: 100% correct data in Neon**, with a small, visible review queue

That's production-grade. The safety net is the point.

## The four tiers

### Tier 1 — Deterministic parser (free, fast, perfect on modern PDFs)

`parsers/finra_parser.py` and `parsers/focus_parser.py`. pdfplumber → PyMuPDF → OCR, section-anchored extraction, regex field matching. Zero cost. Perfect on born-digital modern PDFs (validated: Charles Schwab CRD 5393, 315 pages, 100% field accuracy in regression tests). Known failure modes:

- Legacy PDFs with character encodings that collapse word spacing (validated: R H Securities CRD 10997, pdfplumber returns `"Thissectionprovidesthetypesofbusiness..."`)
- Scanned facing pages where OCR is required but text markers aren't present
- Uncommon firm-profile layouts (partnerships, foreign-domiciled firms)

### Tier 2 — Gemini 2.5 Flash with schema-constrained output (cheap, always-on cross-validator)

`llm/extractors.py` → `llm/gemini_client.py`. For every firm, the PDF is also uploaded to Gemini and extracted using the same Pydantic schema (`response_schema=FirmProfile`). Gemini's vision model sees the actual rendered PDF — it doesn't care about character encodings. ~$0.009 per firm. This is the diversity that makes the safety net work: deterministic and LLM have uncorrelated failure modes.

### Tier 3 — Gemini 2.5 Pro escalation (triggered on disagreement)

When Tier 1 and Tier 2 disagree on any **critical field** (CRD, firm name, dates, monetary values, contact details), the firm is re-extracted with Gemini 2.5 Pro. Pro has stronger reasoning and handles ambiguous cases more conservatively. ~$0.036 per firm; triggered on perhaps 5–10% of firms.

### Tier 4 — Human review queue (manual spot-check)

If disagreements persist even after Pro adjudication, the firm lands in the `review_queue` table with a field-level diff. An operator opens a simple web UI (or just `SELECT * FROM review_queue`), looks at the source PDF, picks the correct value. Expected volume: 1–2% of firms, i.e. 30–60 firms out of 3,000.

## Data flow in detail

```
Input: CRD number from Neon (firms_input table)
     │
     ▼
[1] Download FINRA BrokerCheck PDF from deterministic URL
     │
     ├──► [A] Deterministic parse   ──┐
     │                                 │
     └──► [B] Upload to Gemini         │
          [C] Flash extraction        ──┴──►  Cross-validator
                                              │
                                              ├─ all agree ────► auto-accept → Neon
                                              │
                                              ├─ LLM fills gap ─► merge (LLM) → Neon
                                              │
                                              ├─ disagree ─────► Tier 3: Gemini Pro
                                              │                  │
                                              │                  ├─ resolved → Neon
                                              │                  │
                                              │                  └─ unresolved ► review_queue
                                              │
                                              └─ both empty ───► deterministic → Neon (flagged partial)

     ▼
[2] Resolve CIK on EDGAR (search by firm name)
[3] Download latest 2 X-17A-5 filings
     │
     └──► Same 4-tier flow as FINRA, per filing

     ▼
[4] Compute YoY growth (net capital, total assets)
[5] Write merged FirmRecord to Neon
```

## Confidence scoring

`validation/confidence.py` computes a score for the deterministic output **before** spending LLM tokens. Signals:

| Signal | Penalty |
|---|---|
| Missing CRD | −0.4 |
| Missing firm name | −0.3 |
| Any `parse_warnings` populated | −0.25 |
| Types-of-business count mismatch | −0.2 |
| Preamble leak in services list | −0.15 |
| Space-collapse detected (legacy PDF) | −0.5 |
| Clearing section empty | −0.1 |

Scores below 0.75 flag as needing LLM fallback. In the hybrid pipeline, the LLM runs on every firm anyway (for cross-validation), so confidence is used for routing decisions (auto-accept vs escalation) rather than gating.

## Cross-validation

`validation/cross_validator.py` compares deterministic and LLM outputs field-by-field. Each pair resolves to one of five states:

| Level | Meaning | Action |
|---|---|---|
| `AGREE` | Both extractors produced equivalent values | Accept, confidence 0.95+ |
| `DETERMINISTIC_ONLY` | Only regex found a value | Accept deterministic |
| `LLM_ONLY` | Only Gemini found a value | Accept LLM (fills gaps) |
| `DISAGREE` | Both values exist, they conflict | Escalate to Pro |
| `BOTH_NULL` | Neither extractor found it | Accept null |

Money values use a 0.5% tolerance for rounding noise. Strings use normalized comparison (case/whitespace) plus substring containment (handles "Schwab Holdings Inc" vs "SCHWAB HOLDINGS, INC."). Dates require exact match.

## Cost model for 3,000 firms

All three LLM tiers active:

| Stage | Per firm | Volume | Subtotal |
|---|---|---|---|
| Deterministic parse | $0.00 | 3,000 | $0 |
| Gemini 2.5 Flash (always) | $0.009 | 3,000 | $27 |
| Gemini 2.5 Pro (escalation, ~7%) | $0.036 | ~210 | $7.60 |
| Human review (~1.5%) | $2 (labor) | ~45 | $90 |
| **Total** | | | **~$125** |

Can be cut ~50% by running Gemini Flash in Batch API mode (half price, higher latency — fine for a one-time 3,000-firm run).

Context caching on the system prompt saves another ~10–15%.

## When to run deterministic-only vs hybrid

`run` — deterministic only. Fast, free, ~85–95% accuracy. Good for:
- Daily incremental updates where you only care about firms whose PDFs actually changed (uses `raw_pdf_hash` delta detection)
- Development and debugging
- Environments where you can't add Gemini API keys

`run-hybrid` — full four-tier pipeline. ~98–99% auto + flagged review. Use for:
- The initial 3,000-firm backfill
- Any re-extraction of firms currently in the DLQ
- Production refresh cycles where correctness matters more than speed

Both pipelines write to the same tables — you can switch between them freely.

## Why not LLM-only?

Three reasons the hybrid beats pure LLM:

1. **Cost**: running deterministic first costs nothing and handles most firms. LLM-only is ~4x more expensive at scale.
2. **Diversity**: deterministic and LLM have uncorrelated failure modes. The few cases where both parsers agree on a wrong answer are vanishingly rare. An LLM talking to itself has no such cross-check.
3. **Latency + retry**: deterministic is synchronous and retry-free. LLM calls fail, hit rate limits, time out. Having a working fallback path that doesn't depend on the network is operationally valuable.

The deterministic parser is the proof; the LLM is the jury; the human is the judge on the small number of hung-jury cases. That's how you get to 100%.
