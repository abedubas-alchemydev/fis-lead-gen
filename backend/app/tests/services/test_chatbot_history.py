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


# ── Conversation-history browser (list / ownership / reopen) ────────────


async def test_list_conversations_newest_first_with_counts_and_previews() -> None:
    """Two threads: counts + first-user-message resolve per conversation
    and the most recently touched one lists first."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        first = await service.get_or_create_active_conversation(session, user_id=user_id)
        await service.append_message(
            session, conversation_id=first.id, role="user", content="first question"
        )
        await service.append_message(
            session, conversation_id=first.id, role="assistant", content="first answer"
        )

    async with SessionLocal() as session:
        second = await service.archive_active_and_create_new(session, user_id=user_id)
        await service.append_message(
            session, conversation_id=second.id, role="user", content="second thread opener"
        )

    async with SessionLocal() as session:
        listings = await service.list_conversations(session, user_id=user_id)

    assert [item.conversation.id for item in listings] == [second.id, first.id]
    by_id = {item.conversation.id: item for item in listings}
    assert by_id[first.id].message_count == 2
    assert by_id[first.id].first_user_message == "first question"
    assert by_id[first.id].conversation.archived_at is not None
    assert by_id[second.id].message_count == 1
    assert by_id[second.id].first_user_message == "second thread opener"
    assert by_id[second.id].conversation.archived_at is None


async def test_list_conversations_first_user_message_skips_assistant_turn() -> None:
    """An assistant turn that lands first (greeting backfill etc.) must not
    become the preview — the preview is the first USER message."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)
        await service.append_message(
            session, conversation_id=conv.id, role="assistant", content="welcome aboard"
        )
        await service.append_message(
            session, conversation_id=conv.id, role="user", content="actual question"
        )

    async with SessionLocal() as session:
        listings = await service.list_conversations(session, user_id=user_id)
    assert len(listings) == 1
    assert listings[0].message_count == 2
    assert listings[0].first_user_message == "actual question"


async def test_list_conversations_empty_thread_defaults() -> None:
    """A conversation with no messages lists with count 0 / no preview."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)

    async with SessionLocal() as session:
        listings = await service.list_conversations(session, user_id=user_id)
    assert [item.conversation.id for item in listings] == [conv.id]
    assert listings[0].message_count == 0
    assert listings[0].first_user_message is None


async def test_list_conversations_scoped_to_user_and_capped_at_50() -> None:
    """55 archived rows → only the 50 newest come back; another user's
    rows never bleed in."""
    user_id = await _seed_user()
    other_user_id = await _seed_user()
    service = ChatbotHistoryService()

    archived_marker = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        rows = [
            ChatbotConversation(user_id=user_id, archived_at=archived_marker)
            for _ in range(55)
        ]
        session.add_all(rows)
        # One conversation for the other user — must not appear below.
        session.add(ChatbotConversation(user_id=other_user_id))
        await session.commit()
        inserted_ids = [row.id for row in rows]

    async with SessionLocal() as session:
        listings = await service.list_conversations(session, user_id=user_id)

    assert len(listings) == 50
    returned_ids = [item.conversation.id for item in listings]
    # All rows share one server_default updated_at; the id-desc tiebreak
    # makes "newest 50" the 50 highest ids.
    assert returned_ids == sorted(inserted_ids, reverse=True)[:50]


async def test_get_conversation_for_user_enforces_ownership() -> None:
    user_id = await _seed_user()
    other_user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)

    async with SessionLocal() as session:
        owner_hit = await service.get_conversation_for_user(
            session, conversation_id=conv.id, user_id=user_id
        )
        stranger_hit = await service.get_conversation_for_user(
            session, conversation_id=conv.id, user_id=other_user_id
        )
        missing_hit = await service.get_conversation_for_user(
            session, conversation_id=99_999_999, user_id=user_id
        )
    assert owner_hit is not None and owner_hit.id == conv.id
    assert stranger_hit is None
    assert missing_hit is None


async def test_reopen_conversation_swaps_active() -> None:
    """Reopening an archived thread archives the current active one and
    reactivates the target — exactly one active row survives."""
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        original = await service.get_or_create_active_conversation(session, user_id=user_id)
    async with SessionLocal() as session:
        replacement = await service.archive_active_and_create_new(session, user_id=user_id)

    async with SessionLocal() as session:
        reopened = await service.reopen_conversation(
            session, conversation_id=original.id, user_id=user_id
        )
    assert reopened is not None
    assert reopened.id == original.id
    assert reopened.archived_at is None

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatbotConversation)
                .where(ChatbotConversation.user_id == user_id)
                .order_by(ChatbotConversation.id)
            )
        ).scalars().all()
    states = {row.id: row.archived_at is None for row in rows}
    assert states == {original.id: True, replacement.id: False}


async def test_reopen_conversation_idempotent_when_already_active() -> None:
    user_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        conv = await service.get_or_create_active_conversation(session, user_id=user_id)

    async with SessionLocal() as session:
        reopened = await service.reopen_conversation(
            session, conversation_id=conv.id, user_id=user_id
        )
    assert reopened is not None
    assert reopened.id == conv.id
    assert reopened.archived_at is None

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatbotConversation).where(
                    ChatbotConversation.user_id == user_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].archived_at is None


async def test_reopen_conversation_rejects_other_users_row() -> None:
    """Reopen with someone else's id returns None and mutates nothing."""
    owner_id = await _seed_user()
    intruder_id = await _seed_user()
    service = ChatbotHistoryService()

    async with SessionLocal() as session:
        owned = await service.get_or_create_active_conversation(session, user_id=owner_id)
    async with SessionLocal() as session:
        # Archive it so a successful (buggy) reopen would be observable.
        await service.archive_active_and_create_new(session, user_id=owner_id)

    async with SessionLocal() as session:
        result = await service.reopen_conversation(
            session, conversation_id=owned.id, user_id=intruder_id
        )
    assert result is None

    async with SessionLocal() as session:
        row = await session.get(ChatbotConversation, owned.id)
        assert row is not None
        assert row.archived_at is not None  # untouched


# Avoid unused-import lints when pytest collects the file but doesn't run
# integration mode.
_ = (datetime, timezone)
