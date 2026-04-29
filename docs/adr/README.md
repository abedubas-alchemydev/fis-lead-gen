# Architecture Decision Records

This directory holds the architecture decision records (ADRs) for
fis-lead-gen. Each ADR captures a single technical decision: the context
that prompted it, the alternatives considered, the decision itself, and
the trade-offs accepted.

## Status lifecycle

- **Proposed** — drafted and open for review.
- **Accepted** — agreed on; the implementation is shipping or has shipped.
- **Superseded** — replaced by a later ADR (linked at the top of the
  superseded record).
- **Rejected** — considered and declined; kept for the audit trail.

## When to write a new ADR

- Cross-cutting decisions that span more than one service or layer.
- Trade-offs that future readers will want the reasoning for (storage
  format, provider choice, auth model, rollout strategy).
- Anything you would otherwise have to re-derive from a Slack thread.

Day-to-day implementation choices (a function name, a query shape, a
specific test layout) belong in commit messages and PR bodies, not here.

## Numbering

ADRs are numbered sequentially with a 4-digit prefix
(`0001-`, `0002-`, ...). New ADRs take the next available number — do not
renumber. Filename pattern: `NNNN-kebab-case-title.md`.

## Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-streaming-finra-ingestion.md) | Replace 9 GB PDF cache with streaming FINRA + LLM Files API ingestion | Proposed |
