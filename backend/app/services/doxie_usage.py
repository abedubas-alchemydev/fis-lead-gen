"""Aggregation queries for the admin Doxie Usage / cost dashboard.

Rolls up the per-assistant-turn usage columns on ``chatbot_message``
(``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` /
``tool_call_count`` / ``latency_ms``, added in migration ``20260623_0001``)
into an account-wide summary and a per-user aggregate. Mirrors
``services/extraction_analytics.py`` — a read-only repository, no new
columns beyond the migration.

Counting convention:

- Only ``role == "assistant"`` rows carry usage (user turns leave the
  columns NULL), so every query filters to assistant turns.
- Token / tool columns COALESCE to 0 when NULL — a turn where Gemini
  omitted ``usageMetadata`` still counts as a turn but adds 0 tokens.
- Estimated cost is computed in Python from the configurable per-1M
  rates (``settings.doxie_cost_*``) — a flat blended estimate, never a
  billing figure.
- Latency stats use ``func.avg`` (portable) and ``func.percentile_cont``
  (Postgres-only — unit tests stub this repository so the percentile SQL
  never executes off Postgres).
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import AuthUser
from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_message import ChatbotMessage
from app.schemas.doxie_usage import (
    DoxieUsageSummary,
    DoxieUserUsageListResponse,
    DoxieUserUsageRow,
)

# Column expressions reused across both queries. COALESCE NULL→0 so a
# token-less turn contributes 0 rather than dropping the whole SUM to NULL.
_PROMPT = func.coalesce(ChatbotMessage.prompt_tokens, 0)
_COMPLETION = func.coalesce(ChatbotMessage.completion_tokens, 0)
_TOTAL = func.coalesce(ChatbotMessage.total_tokens, 0)
_TOOLS = func.coalesce(ChatbotMessage.tool_call_count, 0)


def _estimated_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Flat-rate USD estimate from the configured per-1M-token prices.

    Input tokens priced at ``doxie_cost_input_per_1m_usd``, output at
    ``doxie_cost_output_per_1m_usd``. Rounded to 4 dp — enough to show
    fractions of a cent on low-volume accounts without float noise.
    """
    cost = (
        prompt_tokens / 1_000_000 * settings.doxie_cost_input_per_1m_usd
        + completion_tokens / 1_000_000 * settings.doxie_cost_output_per_1m_usd
    )
    return round(cost, 4)


class DoxieUsageRepository:
    """Read-only aggregates for the Doxie Usage endpoints."""

    async def summary(self, db: AsyncSession) -> DoxieUsageSummary:
        # ``total_tokens`` flagged > 0 marks a turn that carried real token
        # data (NULL or 0 both read as "no tokens reported"), so the count
        # mirrors how the sums COALESCE.
        has_tokens = case(
            (ChatbotMessage.total_tokens > 0, 1), else_=0
        )
        stmt = select(
            func.count(ChatbotMessage.id),
            func.coalesce(func.sum(has_tokens), 0),
            func.coalesce(func.sum(_PROMPT), 0),
            func.coalesce(func.sum(_COMPLETION), 0),
            func.coalesce(func.sum(_TOTAL), 0),
            func.coalesce(func.sum(_TOOLS), 0),
        ).where(ChatbotMessage.role == "assistant")

        row = (await db.execute(stmt)).one()
        (
            total_turns,
            turns_with_tokens,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            tool_calls,
        ) = (int(v or 0) for v in row)

        return DoxieUsageSummary(
            total_assistant_turns=total_turns,
            assistant_turns_with_tokens=turns_with_tokens,
            total_prompt_tokens=prompt_tokens,
            total_completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            total_tool_calls=tool_calls,
            estimated_cost_usd=_estimated_cost_usd(
                prompt_tokens, completion_tokens
            ),
            cost_input_per_1m_usd=settings.doxie_cost_input_per_1m_usd,
            cost_output_per_1m_usd=settings.doxie_cost_output_per_1m_usd,
        )

    async def list_users(
        self, db: AsyncSession, *, page: int, limit: int
    ) -> DoxieUserUsageListResponse:
        # Assistant message → its conversation (for user_id) → the user row
        # (LEFT OUTER so a deleted-user edge still aggregates by id; in
        # practice the user CASCADE removes their conversations first).
        base = (
            select(
                ChatbotConversation.user_id.label("user_id"),
                AuthUser.name.label("name"),
                AuthUser.email.label("email"),
                func.count(ChatbotMessage.id).label("assistant_turns"),
                func.coalesce(func.sum(_PROMPT), 0).label("prompt_tokens"),
                func.coalesce(func.sum(_COMPLETION), 0).label(
                    "completion_tokens"
                ),
                func.coalesce(func.sum(_TOTAL), 0).label("total_tokens"),
                func.coalesce(func.sum(_TOOLS), 0).label("tool_calls"),
                func.avg(ChatbotMessage.latency_ms).label("avg_latency_ms"),
                func.percentile_cont(0.5)
                .within_group(ChatbotMessage.latency_ms.asc())
                .label("p50_latency_ms"),
            )
            .select_from(ChatbotMessage)
            .join(
                ChatbotConversation,
                ChatbotConversation.id == ChatbotMessage.conversation_id,
            )
            .outerjoin(AuthUser, AuthUser.id == ChatbotConversation.user_id)
            .where(ChatbotMessage.role == "assistant")
            .group_by(
                ChatbotConversation.user_id, AuthUser.name, AuthUser.email
            )
        )

        # Total distinct users with at least one assistant turn — the count
        # of grouped rows, resolved before pagination.
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await db.execute(count_stmt)).scalar_one() or 0)

        # Heaviest spenders first, id as a stable tiebreaker.
        paged_stmt = (
            base.order_by(
                func.coalesce(func.sum(_TOTAL), 0).desc(),
                ChatbotConversation.user_id.asc(),
            )
            .limit(limit)
            .offset((page - 1) * limit)
        )
        rows = (await db.execute(paged_stmt)).all()

        items = [
            DoxieUserUsageRow(
                user_id=r.user_id,
                name=r.name,
                email=r.email,
                assistant_turns=int(r.assistant_turns or 0),
                total_prompt_tokens=int(r.prompt_tokens or 0),
                total_completion_tokens=int(r.completion_tokens or 0),
                total_tokens=int(r.total_tokens or 0),
                total_tool_calls=int(r.tool_calls or 0),
                estimated_cost_usd=_estimated_cost_usd(
                    int(r.prompt_tokens or 0), int(r.completion_tokens or 0)
                ),
                avg_latency_ms=(
                    round(float(r.avg_latency_ms), 1)
                    if r.avg_latency_ms is not None
                    else None
                ),
                p50_latency_ms=(
                    round(float(r.p50_latency_ms), 1)
                    if r.p50_latency_ms is not None
                    else None
                ),
            )
            for r in rows
        ]
        return DoxieUserUsageListResponse(items=items, total=total)
