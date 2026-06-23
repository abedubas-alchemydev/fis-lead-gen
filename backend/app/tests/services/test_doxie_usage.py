"""Integration tests for DoxieUsageRepository against a real Postgres.

Marked ``integration`` so it only runs under ``pytest -m integration`` (the
CI Backend Integration job pulls this in via Postgres 15). The default
local run skips it — ``func.percentile_cont`` is Postgres-only and the
unit tests stub the repository instead.

Seeds a user + an active conversation + a handful of assistant messages
(some with NULL token columns, mimicking turns where Gemini omitted
usageMetadata) plus a user turn, then asserts the summary and per-user
aggregates COALESCE NULL→0 and apply the configured cost formula.
"""

from __future__ import annotations

import secrets

import pytest

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.auth import AuthUser
from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_message import ChatbotMessage
from app.services.doxie_usage import DoxieUsageRepository

pytestmark = pytest.mark.integration


async def _seed_user() -> str:
    user_id = f"doxieusage-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Usage Tester",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _seed_conversation_with_messages(user_id: str) -> int:
    """One conversation with three assistant turns + one user turn.

    Assistant turns:
      - tokens (100/40/140), 1 tool, 500ms
      - tokens (200/60/260), 0 tools, 1500ms
      - NULL tokens / NULL tool / NULL latency (Gemini omitted usageMetadata)
    The user turn carries no usage at all (the columns stay NULL) and must
    be excluded from every aggregate.
    """
    async with SessionLocal() as session:
        conv = ChatbotConversation(user_id=user_id)
        session.add(conv)
        await session.flush()
        session.add_all(
            [
                ChatbotMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a1",
                    prompt_tokens=100,
                    completion_tokens=40,
                    total_tokens=140,
                    tool_call_count=1,
                    latency_ms=500,
                ),
                ChatbotMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a2",
                    prompt_tokens=200,
                    completion_tokens=60,
                    total_tokens=260,
                    tool_call_count=0,
                    latency_ms=1500,
                ),
                ChatbotMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a3-no-usage",
                ),
                ChatbotMessage(
                    conversation_id=conv.id,
                    role="user",
                    content="u1",
                ),
            ]
        )
        await session.commit()
        return conv.id


async def test_summary_coalesces_nulls_and_applies_cost_formula() -> None:
    user_id = await _seed_user()
    await _seed_conversation_with_messages(user_id)

    repo = DoxieUsageRepository()
    async with SessionLocal() as session:
        summary = await repo.summary(session)

    # 3 assistant turns total; only 2 carried real token data.
    assert summary.total_assistant_turns == 3
    assert summary.assistant_turns_with_tokens == 2
    # NULL tokens COALESCE to 0: 100+200 / 40+60 / 140+260.
    assert summary.total_prompt_tokens == 300
    assert summary.total_completion_tokens == 100
    assert summary.total_tokens == 400
    # NULL tool count COALESCEs to 0: 1 + 0.
    assert summary.total_tool_calls == 1

    expected_cost = round(
        300 / 1_000_000 * settings.doxie_cost_input_per_1m_usd
        + 100 / 1_000_000 * settings.doxie_cost_output_per_1m_usd,
        4,
    )
    assert summary.estimated_cost_usd == expected_cost
    assert summary.cost_input_per_1m_usd == settings.doxie_cost_input_per_1m_usd
    assert summary.cost_output_per_1m_usd == settings.doxie_cost_output_per_1m_usd


async def test_list_users_aggregates_per_user_with_latency_stats() -> None:
    user_id = await _seed_user()
    await _seed_conversation_with_messages(user_id)

    repo = DoxieUsageRepository()
    async with SessionLocal() as session:
        result = await repo.list_users(session, page=1, limit=50)

    row = next((r for r in result.items if r.user_id == user_id), None)
    assert row is not None
    assert row.name == "Usage Tester"
    assert row.email.endswith("@example.com")
    assert row.assistant_turns == 3
    assert row.total_prompt_tokens == 300
    assert row.total_completion_tokens == 100
    assert row.total_tokens == 400
    assert row.total_tool_calls == 1
    # avg latency over the two non-NULL latencies (500, 1500) = 1000.
    assert row.avg_latency_ms == 1000.0
    # p50 (median) over {500, 1500} = 1000 via linear interpolation.
    assert row.p50_latency_ms == 1000.0

    expected_cost = round(
        300 / 1_000_000 * settings.doxie_cost_input_per_1m_usd
        + 100 / 1_000_000 * settings.doxie_cost_output_per_1m_usd,
        4,
    )
    assert row.estimated_cost_usd == expected_cost


async def test_user_with_only_null_usage_turns_reports_zeros() -> None:
    """A user whose every assistant turn omitted usageMetadata still appears
    with zeroed token/tool sums and NULL latency stats."""
    user_id = await _seed_user()
    async with SessionLocal() as session:
        conv = ChatbotConversation(user_id=user_id)
        session.add(conv)
        await session.flush()
        session.add(
            ChatbotMessage(
                conversation_id=conv.id,
                role="assistant",
                content="no usage at all",
            )
        )
        await session.commit()

    repo = DoxieUsageRepository()
    async with SessionLocal() as session:
        result = await repo.list_users(session, page=1, limit=50)

    row = next((r for r in result.items if r.user_id == user_id), None)
    assert row is not None
    assert row.assistant_turns == 1
    assert row.total_tokens == 0
    assert row.total_tool_calls == 0
    assert row.avg_latency_ms is None
    assert row.p50_latency_ms is None
    assert row.estimated_cost_usd == 0.0
