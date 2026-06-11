"""Persistence layer for Doxie chat history.

One ACTIVE conversation per user (``archived_at IS NULL``); the "New
chat" button archives the current row and inserts a fresh one. The
partial unique index ``uq_chatbot_conversation_one_active_per_user``
enforces this at the DB level so a racing double-POST can't create two
active conversations.

The endpoint persists user + assistant turns inside the same request that
calls Gemini. Persistence failures are propagated (not swallowed) so the
FE can show an error rather than the conversation silently diverging
between the rendered messages and the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_message import ChatbotMessage
from app.schemas.chatbot import ChatbotPageContext

logger = logging.getLogger(__name__)

# Hard cap on GET /chatbot/conversations — the history browser shows the
# 50 most recently touched threads; anything older falls off the list
# (still in the DB, just not browsable).
CONVERSATION_LIST_CAP = 50


@dataclass(frozen=True)
class ConversationListing:
    """One conversation plus the aggregates the history browser needs.

    ``first_user_message`` is the full body of the conversation's first
    user turn (``None`` when the user never sent one) — the endpoint
    layer truncates it into the display preview.
    """

    conversation: ChatbotConversation
    message_count: int
    first_user_message: str | None


class ChatbotHistoryService:
    async def get_or_create_active_conversation(
        self, db: AsyncSession, *, user_id: str
    ) -> ChatbotConversation:
        """Return the user's active conversation, creating one on first use.

        Race semantics: two concurrent first-message requests from the same
        user can both miss the SELECT and try to INSERT. The partial unique
        index makes the second insert raise ``IntegrityError``; we catch it
        and re-select the winner.
        """
        existing = await self._select_active(db, user_id=user_id)
        if existing is not None:
            return existing

        conversation = ChatbotConversation(user_id=user_id)
        db.add(conversation)
        try:
            await db.commit()
        except IntegrityError:
            # A racing request beat us to creating the active row. Roll back
            # and re-select — the winner is whichever insert landed first.
            await db.rollback()
            winner = await self._select_active(db, user_id=user_id)
            if winner is None:
                # Extremely unlikely: integrity violation but no row found.
                # Surface as a real error rather than recursing.
                raise
            return winner
        await db.refresh(conversation)
        return conversation

    async def append_message(
        self,
        db: AsyncSession,
        *,
        conversation_id: int,
        role: str,
        content: str,
        page_context: ChatbotPageContext | None = None,
    ) -> ChatbotMessage:
        message = ChatbotMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            page_context_path=page_context.path if page_context else None,
            page_context_title=page_context.title if page_context else None,
        )
        db.add(message)
        # Bump the conversation's updated_at so a future "most recent first"
        # listing reflects the chat's true age, not its created date.
        conversation = await db.get(ChatbotConversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(message)
        return message

    async def list_messages(
        self, db: AsyncSession, *, conversation_id: int
    ) -> list[ChatbotMessage]:
        stmt = (
            select(ChatbotMessage)
            .where(ChatbotMessage.conversation_id == conversation_id)
            .order_by(ChatbotMessage.created_at.asc(), ChatbotMessage.id.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def archive_active_and_create_new(
        self, db: AsyncSession, *, user_id: str
    ) -> ChatbotConversation:
        """Archive the user's current active conversation and open a fresh one.

        Both writes happen in one transaction so a crash can't leave the
        user with two archived rows and no active one (which would just
        get re-created on next message, but still — atomicity is cheap).
        """
        existing = await self._select_active(db, user_id=user_id)
        if existing is not None:
            existing.archived_at = datetime.now(timezone.utc)
        new_conversation = ChatbotConversation(user_id=user_id)
        db.add(new_conversation)
        await db.commit()
        await db.refresh(new_conversation)
        return new_conversation

    async def list_conversations(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        limit: int = CONVERSATION_LIST_CAP,
    ) -> list[ConversationListing]:
        """Newest-first (by ``updated_at``) conversations for one user.

        Aggregates (message count + first user message) are resolved with
        two grouped queries over the page of conversation ids — never a
        per-conversation round-trip — so the listing stays three queries
        total no matter how many rows the cap admits.
        """
        stmt = (
            select(ChatbotConversation)
            .where(ChatbotConversation.user_id == user_id)
            .order_by(
                ChatbotConversation.updated_at.desc(),
                ChatbotConversation.id.desc(),
            )
            .limit(limit)
        )
        result = await db.execute(stmt)
        conversations = list(result.scalars().all())
        if not conversations:
            return []

        stats = await self._message_stats(
            db, conversation_ids=[c.id for c in conversations]
        )
        return [
            ConversationListing(
                conversation=conversation,
                message_count=stats.get(conversation.id, (0, None))[0],
                first_user_message=stats.get(conversation.id, (0, None))[1],
            )
            for conversation in conversations
        ]

    async def summarize_conversation(
        self, db: AsyncSession, *, conversation: ChatbotConversation
    ) -> ConversationListing:
        """Aggregates for a single conversation (reopen response body)."""
        stats = await self._message_stats(
            db, conversation_ids=[conversation.id]
        )
        message_count, first_user_message = stats.get(
            conversation.id, (0, None)
        )
        return ConversationListing(
            conversation=conversation,
            message_count=message_count,
            first_user_message=first_user_message,
        )

    async def get_conversation_for_user(
        self, db: AsyncSession, *, conversation_id: int, user_id: str
    ) -> ChatbotConversation | None:
        """The conversation iff it exists AND belongs to ``user_id``.

        Returns ``None`` for both "no such id" and "someone else's id" so
        the endpoint can 404 without leaking which ids exist.
        """
        stmt = (
            select(ChatbotConversation)
            .where(ChatbotConversation.id == conversation_id)
            .where(ChatbotConversation.user_id == user_id)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def reopen_conversation(
        self, db: AsyncSession, *, conversation_id: int, user_id: str
    ) -> ChatbotConversation | None:
        """Make an archived conversation the user's active one again.

        Archives the currently active conversation (when it's a different
        row), then clears the target's ``archived_at`` — both in one
        transaction. Idempotent when the target is already active.
        Returns ``None`` when the id doesn't exist / isn't the user's.
        """
        target = await self.get_conversation_for_user(
            db, conversation_id=conversation_id, user_id=user_id
        )
        if target is None:
            return None
        if target.archived_at is None:
            # Already the active conversation — nothing to swap.
            return target

        active = await self._select_active(db, user_id=user_id)
        if active is not None:
            active.archived_at = datetime.now(timezone.utc)
            # Flush the archive UPDATE before un-archiving the target:
            # the partial unique index allows one active row per user and
            # Postgres checks it per statement, so the reverse order
            # would raise mid-transaction.
            await db.flush()
        target.archived_at = None
        await db.commit()
        await db.refresh(target)
        return target

    @staticmethod
    async def _message_stats(
        db: AsyncSession, *, conversation_ids: Sequence[int]
    ) -> dict[int, tuple[int, str | None]]:
        """Per-conversation ``(message_count, first_user_message)``.

        One grouped aggregate resolves the count and the id of the first
        user turn (ids are monotonic, so MIN(id) over user rows is the
        earliest); a second query fetches just those messages' bodies.
        Conversations with no messages simply don't appear in the result
        — callers default to ``(0, None)``.
        """
        agg_stmt = (
            select(
                ChatbotMessage.conversation_id,
                func.count(ChatbotMessage.id).label("message_count"),
                func.min(
                    case((ChatbotMessage.role == "user", ChatbotMessage.id))
                ).label("first_user_message_id"),
            )
            .where(ChatbotMessage.conversation_id.in_(conversation_ids))
            .group_by(ChatbotMessage.conversation_id)
        )
        agg_rows = (await db.execute(agg_stmt)).all()

        counts: dict[int, int] = {}
        first_message_ids: dict[int, int] = {}
        for conversation_id, message_count, first_user_message_id in agg_rows:
            counts[conversation_id] = message_count
            if first_user_message_id is not None:
                first_message_ids[conversation_id] = first_user_message_id

        first_messages: dict[int, str] = {}
        if first_message_ids:
            content_stmt = select(
                ChatbotMessage.conversation_id, ChatbotMessage.content
            ).where(ChatbotMessage.id.in_(first_message_ids.values()))
            for conversation_id, content in (
                await db.execute(content_stmt)
            ).all():
                first_messages[conversation_id] = content

        return {
            conversation_id: (count, first_messages.get(conversation_id))
            for conversation_id, count in counts.items()
        }

    @staticmethod
    async def _select_active(
        db: AsyncSession, *, user_id: str
    ) -> ChatbotConversation | None:
        stmt = (
            select(ChatbotConversation)
            .where(ChatbotConversation.user_id == user_id)
            .where(ChatbotConversation.archived_at.is_(None))
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
