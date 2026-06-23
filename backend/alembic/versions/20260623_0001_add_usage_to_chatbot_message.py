"""Add token/tool/latency usage columns to chatbot_message.

Doxie throws away the Gemini ``usageMetadata`` block (prompt/output token
counts) on every chat turn, so there's no visibility into the chatbot's
cost or load. These five columns capture per-assistant-turn usage:

- ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` — the
  Gemini token counts off the terminal response's ``usageMetadata``.
- ``tool_call_count`` — how many tool calls the turn dispatched.
- ``latency_ms`` — wall-clock time the turn took inside ``chat`` /
  ``chat_stream``.

All five are **nullable with no server default** on purpose: NULL means
"not captured" (every pre-migration row, every user turn, and any
assistant turn where Gemini omitted ``usageMetadata`` on the final
streaming chunk) — distinct from a real captured ``0``. The admin usage
dashboard COALESCEs NULL to 0 when aggregating. No backfill is possible
(the discarded counts are gone), and no index is added (the dashboard
scans the assistant rows in full).

Revision ID: 20260623_0001
Revises: 20260621_0001
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260623_0001"
down_revision: str | None = "20260621_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chatbot_message",
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "chatbot_message",
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "chatbot_message",
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "chatbot_message",
        sa.Column("tool_call_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "chatbot_message",
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chatbot_message", "latency_ms")
    op.drop_column("chatbot_message", "tool_call_count")
    op.drop_column("chatbot_message", "total_tokens")
    op.drop_column("chatbot_message", "completion_tokens")
    op.drop_column("chatbot_message", "prompt_tokens")
