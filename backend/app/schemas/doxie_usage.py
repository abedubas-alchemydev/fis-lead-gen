"""Schemas for the admin Doxie Usage / cost dashboard (`/settings/doxie-usage`).

Read-only observability over the Doxie chatbot's token spend, tool-call
volume, and latency, derived from the per-assistant-turn usage columns on
``chatbot_message`` (added in migration ``20260623_0001``). Two response
shapes:

- ``DoxieUsageSummary`` — the KPI strip: account-wide token totals, an
  *estimated* USD cost from the configurable rates, tool-call volume, and
  how many assistant turns actually carried token data.
- ``DoxieUserUsageListResponse`` — paginated per-user aggregate, each row a
  user's turn count, token sums, estimated cost, and latency stats.

Kept in its own namespace (mirrors ``schemas/extraction_analytics.py``) so
this admin-only surface widens independently of the user-facing stats.
"""

from __future__ import annotations

from pydantic import BaseModel


class DoxieUsageSummary(BaseModel):
    """Account-wide rollup for the KPI strip.

    ``estimated_cost_usd`` is a flat-rate estimate (input/output token sums
    times the configured per-1M prices), labelled "estimated" in the UI —
    not a billing figure. ``assistant_turns_with_tokens`` surfaces coverage:
    turns where Gemini reported ``usageMetadata`` (the rest contribute 0 to
    the token sums via COALESCE).
    """

    total_assistant_turns: int
    assistant_turns_with_tokens: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_tool_calls: int
    estimated_cost_usd: float
    # The rates the cost was computed with, echoed so the UI can show the
    # per-1M prices alongside the estimate.
    cost_input_per_1m_usd: float
    cost_output_per_1m_usd: float


class DoxieUserUsageRow(BaseModel):
    """One user's Doxie usage aggregate.

    ``user_id`` is always present; ``name`` / ``email`` are NULL only if the
    user row was deleted (conversations CASCADE-delete with the user, so this
    is rare — an OUTER JOIN guards the edge). ``avg_latency_ms`` /
    ``p50_latency_ms`` are NULL when no turn recorded a latency.
    """

    user_id: str
    name: str | None
    email: str | None
    assistant_turns: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_tool_calls: int
    estimated_cost_usd: float
    avg_latency_ms: float | None
    p50_latency_ms: float | None


class DoxieUserUsageListResponse(BaseModel):
    items: list[DoxieUserUsageRow]
    total: int
