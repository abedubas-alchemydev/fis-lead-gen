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
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_message import ChatbotMessage
from app.schemas.chatbot import ChatbotPageContext

logger = logging.getLogger(__name__)


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
