"""Integration tests for ChatbotHistoryService against a real Postgres.

Marked ``integration`` so it only runs under ``pytest -m integration`` (the
CI Backend Integration job pulls this in via Postgres 15). The non-default
local pytest run skips these.

Verifies the single-active-conversation invariant — both at the application
level (``get_or_create_active_conversation`` is idempotent) and at the DB
level (the partial unique index ``uq_chatbot_conversation_one_active_per_user``
raises IntegrityError on a direct duplicate insert).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.auth import AuthUser
from app.models.chatbot_conversation import ChatbotConversation
from app.schemas.chatbot import ChatbotPageContext
from app.services.chatbot_history import ChatbotHistoryService

pytestmark = pytest.mark.integration


async def _seed_user() -> str:
    user_id = f"chathist-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="History Tester",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def test_get_or_create_active_conversation_creates_on_first_call() -> None:
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        assert conv.id is not None
        assert conv.user_id == user_id
        assert conv.archived_at is None


async def test_get_or_create_active_conversation_idempotent() -> None:
    """Second call returns the same row, no duplicate inserted."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        first = await service.get_or_create_active_conversation(session, user_id=user_id)
    async with SessionLocal() as session:
        second = await service.get_or_create_active_conversation(session, user_id=user_id)
    assert first.id == second.id

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatbotConversation).where(
                    ChatbotConversation.user_id == user_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_append_message_persists_and_bumps_updated_at() -> None:
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        original_updated_at = conv.updated_at

    async with SessionLocal() as session:
        msg = await service.append_message(
            session,
            conversation_id=conv.id,
            role="user",
            content="hello world",
            page_context=ChatbotPageContext(path="/foo", title="Foo Page"),
        )
        assert msg.id is not None
        assert msg.conversation_id == conv.id
        assert msg.role == "user"
        assert msg.content == "hello world"
        assert msg.page_context_path == "/foo"
        assert msg.page_context_title == "Foo Page"

    # The conversation's updated_at should have advanced.
    async with SessionLocal() as session:
        refreshed = await session.get(ChatbotConversation, conv.id)
        assert refreshed is not None
        assert refreshed.updated_at >= original_updated_at


async def test_list_messages_returns_chronological_order() -> None:
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        for i in range(5):
            await service.append_message(
                session,
                conversation_id=conv.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i}",
            )

    async with SessionLocal() as session:
        rows = await service.list_messages(session, conversation_id=conv.id)
    assert [r.content for r in rows] == [f"message {i}" for i in range(5)]


async def test_archive_active_and_create_new_swaps_active() -> None:
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        original = await service.get_or_create_active_conversation(session, user_id=user_id)

    async with SessionLocal() as session:
        replacement = await service.archive_active_and_create_new(session, user_id=user_id)
    assert replacement.id != original.id
    assert replacement.archived_at is None

    async with SessionLocal() as session:
        archived = await session.get(ChatbotConversation, original.id)
        assert archived is not None
        assert archived.archived_at is not None


async def test_partial_unique_index_blocks_two_active_per_user() -> None:
    """The DB-side partial unique index must prevent a malicious or buggy
    caller from inserting a second active conversation directly."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        await service.get_or_create_active_conversation(session, user_id=user_id)

    async with SessionLocal() as session:
        session.add(ChatbotConversation(user_id=user_id))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_archive_then_recreate_only_archives_active() -> None:
    """A second `archive_active_and_create_new` after a no-op (no messages
    sent) still creates a fresh row without touching the previously-
    archived one. Verifies the SELECT WHERE archived_at IS NULL filter."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        first = await service.get_or_create_active_conversation(session, user_id=user_id)
    async with SessionLocal() as session:
        second = await service.archive_active_and_create_new(session, user_id=user_id)
    async with SessionLocal() as session:
        third = await service.archive_active_and_create_new(session, user_id=user_id)

    # Three distinct rows, only the third active.
    assert len({first.id, second.id, third.id}) == 3
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatbotConversation)
                .where(ChatbotConversation.user_id == user_id)
                .order_by(ChatbotConversation.id)
            )
        ).scalars().all()
    assert [r.archived_at is None for r in rows] == [False, False, True]


async def test_page_context_optional() -> None:
    """append_message without page_context stores NULLs (assistant turns
    follow this path)."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        msg = await service.append_message(
            session,
            conversation_id=conv.id,
            role="assistant",
            content="ok",
        )
    assert msg.page_context_path is None
    assert msg.page_context_title is None


async def test_used_with_real_timestamps_for_ordering() -> None:
    """Sanity: rows inserted in order survive list_messages ordering even
    when wall-clock ``now()`` could collide on fast hardware. The Index
    sorts by (created_at, id) so ties on created_at fall back to PK."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        # Insert many in one session to maximize timestamp-collision odds.
        for i in range(20):
            await service.append_message(
                session,
                conversation_id=conv.id,
                role="user",
                content=f"#{i}",
            )

    async with SessionLocal() as session:
        rows = await service.list_messages(session, conversation_id=conv.id)
    assert [r.content for r in rows] == [f"#{i}" for i in range(20)]


# Avoid unused-import lints when pytest collects the file but doesn't run
# integration mode.
_ = (datetime, timezone)
