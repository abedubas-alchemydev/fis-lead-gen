from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import (
    ChatbotHistoryMessage,
    ChatbotHistoryResponse,
    ChatbotNewConversationResponse,
    ChatbotRequest,
    ChatbotResponse,
)
from app.services.auth import get_current_user
from app.services.chatbot import ChatbotService
from app.services.chatbot_history import ChatbotHistoryService
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chatbot")
chatbot_service = ChatbotService()
chatbot_history_service = ChatbotHistoryService()

# Cap the cumulative size of all message bodies in one call. Per-message
# size is already capped at 8000 chars in the schema; this is a second
# guard against a 50×8000 worst-case payload hammering the Gemini quota
# from a single click.
_MAX_TOTAL_CHARS = 40_000


@router.post("/messages", response_model=ChatbotResponse)
async def post_chatbot_message(
    payload: ChatbotRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotResponse:
    if payload.messages[-1].role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The last message must be from the user.",
        )

    total_chars = sum(len(m.content) for m in payload.messages)
    if total_chars > _MAX_TOTAL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Conversation exceeds maximum size "
                f"({total_chars} > {_MAX_TOTAL_CHARS} characters)."
            ),
        )

    # Persist BEFORE calling Gemini so a slow / failed model call still
    # leaves the user's turn on the record. The assistant reply is
    # appended after a successful Gemini round-trip.
    conversation = await chatbot_history_service.get_or_create_active_conversation(
        db, user_id=current_user.id
    )
    last_user_message = payload.messages[-1]
    await chatbot_history_service.append_message(
        db,
        conversation_id=conversation.id,
        role="user",
        content=last_user_message.content,
        page_context=payload.page_context,
    )

    try:
        reply = await chatbot_service.chat(
            messages=payload.messages,
            user=current_user,
            db=db,
            page_context=payload.page_context,
        )
    except GeminiConfigurationError as exc:
        # 503 — the server is misconfigured (no API key). Treated as a
        # transient unavailable rather than a client error. The user
        # message stays persisted so the next attempt sees the full
        # history.
        logger.warning(
            "Doxie chat unavailable: %s (user_id=%s)", exc, current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Doxie chat is not configured on this environment.",
        ) from exc
    except GeminiExtractionError as exc:
        # 502 — Gemini itself failed (upstream / safety filter / etc).
        logger.warning(
            "Doxie chat upstream error: %s (user_id=%s)", exc, current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Doxie couldn't generate a reply. Please try again.",
        ) from exc

    # Persist the assistant turn. A failure here would mean the user
    # got their answer but history is silently incomplete — better to
    # surface it as an explicit error than diverge silently. The reply
    # has already been computed, so we re-raise as 500 rather than 502.
    try:
        await chatbot_history_service.append_message(
            db,
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        )
    except Exception as exc:
        logger.exception(
            "Doxie history persistence failed for assistant turn "
            "(user_id=%s conversation_id=%s)",
            current_user.id,
            conversation.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Doxie generated a reply but it couldn't be saved.",
        ) from exc

    return ChatbotResponse(reply=reply)


@router.get("/messages", response_model=ChatbotHistoryResponse)
async def get_chatbot_messages(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotHistoryResponse:
    """Return the persisted messages for the user's active conversation.

    Returns an empty list (with a newly-created conversation_id) when the
    user has never chatted before — the FE renders the welcome message in
    that case without needing a separate "no history" branch.
    """
    conversation = await chatbot_history_service.get_or_create_active_conversation(
        db, user_id=current_user.id
    )
    rows = await chatbot_history_service.list_messages(
        db, conversation_id=conversation.id
    )
    return ChatbotHistoryResponse(
        conversation_id=conversation.id,
        messages=[ChatbotHistoryMessage.model_validate(row) for row in rows],
    )


@router.post(
    "/conversations/new",
    response_model=ChatbotNewConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_chatbot_new_conversation(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotNewConversationResponse:
    """Archive the user's current conversation and open a fresh one.

    Idempotent in the sense that an already-archived conversation with no
    follow-up just gets re-created — the user sees a clean slate either
    way. The previous conversation stays in the DB.
    """
    conversation = await chatbot_history_service.archive_active_and_create_new(
        db, user_id=current_user.id
    )
    return ChatbotNewConversationResponse(conversation_id=conversation.id)
