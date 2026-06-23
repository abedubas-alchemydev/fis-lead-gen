from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import (
    ChatbotConversationListResponse,
    ChatbotConversationSummary,
    ChatbotEmbeddingBackfillEntityCounts,
    ChatbotEmbeddingBackfillResponse,
    ChatbotHistoryMessage,
    ChatbotHistoryResponse,
    ChatbotNewConversationResponse,
    ChatbotRequest,
    ChatbotResponse,
    ChatTurnUsage,
)
from app.services.auth import get_current_user
from app.services.chatbot import ChatbotService
from app.services.chatbot_history import (
    ChatbotHistoryService,
    ConversationListing,
)
from app.services.chatbot_semantic import ChatbotSemanticService
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chatbot")
chatbot_service = ChatbotService()
chatbot_history_service = ChatbotHistoryService()
chatbot_semantic_service = ChatbotSemanticService()

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
        reply, usage = await chatbot_service.chat(
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
            usage=usage,
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


def _shared_validate(payload: ChatbotRequest) -> None:
    """Shared validation used by both the streaming and non-streaming POSTs."""
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


@router.post("/messages/stream")
async def post_chatbot_message_stream(
    payload: ChatbotRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """SSE variant of POST /messages.

    Same validation + persistence contract as the non-streaming endpoint:
    user turn is saved before the stream opens, assistant turn is saved
    after the ``done`` event fires. Errors mid-stream surface as a final
    ``{type: "error", code, message}`` event rather than HTTP 5xx — the
    FE has already started rendering and the SSE response status is
    locked to 200 the moment the first byte is sent.

    SSE format: ``data: <json>\\n\\n`` per event. The wrapper adds
    ``Cache-Control: no-cache`` and ``X-Accel-Buffering: no`` so the
    Cloud Run / Next.js proxy chain doesn't buffer.
    """
    _shared_validate(payload)

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

    async def _sse_stream() -> AsyncIterator[bytes]:
        final_reply = ""
        final_usage: ChatTurnUsage | None = None
        try:
            async for event in chatbot_service.chat_stream(
                messages=payload.messages,
                user=current_user,
                db=db,
                page_context=payload.page_context,
            ):
                if event.get("type") == "done":
                    final_reply = event.get("reply", "")
                    # The terminal done event carries the turn's usage —
                    # tool/latency always set, tokens NULL when Gemini
                    # omitted usageMetadata on the final chunk.
                    final_usage = ChatTurnUsage(
                        prompt_tokens=event.get("prompt_tokens"),
                        completion_tokens=event.get("completion_tokens"),
                        total_tokens=event.get("total_tokens"),
                        tool_call_count=event.get("tool_call_count", 0),
                        latency_ms=event.get("latency_ms", 0),
                    )
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
                if event.get("type") in ("done", "error"):
                    break
        except Exception as exc:  # noqa: BLE001
            # Generator should yield errors as events, not raise — but
            # defend against an unhandled crash mid-stream so the FE
            # always sees a terminal event.
            logger.exception(
                "Doxie stream crashed (user_id=%s)", current_user.id
            )
            crash = {
                "type": "error",
                "code": "extraction",
                "message": "Doxie crashed mid-reply. Please try again.",
            }
            yield f"data: {json.dumps(crash)}\n\n".encode("utf-8")
            return

        # Persist the assistant turn after a clean done. A failure here
        # would diverge history from what the user saw, but we've already
        # committed to the 200 SSE response — emit a follow-up error
        # event so the FE can flag the inconsistency.
        if final_reply:
            try:
                await chatbot_history_service.append_message(
                    db,
                    conversation_id=conversation.id,
                    role="assistant",
                    content=final_reply,
                    usage=final_usage,
                )
            except Exception:
                logger.exception(
                    "Doxie history persistence failed for streamed "
                    "assistant turn (user_id=%s conversation_id=%s)",
                    current_user.id,
                    conversation.id,
                )
                persist_err = {
                    "type": "error",
                    "code": "persistence",
                    "message": "Reply couldn't be saved to history.",
                }
                yield f"data: {json.dumps(persist_err)}\n\n".encode("utf-8")

    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/embeddings/backfill",
    response_model=ChatbotEmbeddingBackfillResponse,
)
async def post_chatbot_embeddings_backfill(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotEmbeddingBackfillResponse:
    """Admin-only: (re)populate the BD + IA embedding index for semantic
    search.

    Synchronous — both tables are small enough that batch-of-50 embedding
    against Gemini's API completes inside the Cloud Run request budget
    (a few minutes at worst). Re-runs are cheap because the service
    skips rows whose content hash didn't change. The populate-all pipeline
    also runs this incrementally after each run, so the manual endpoint is
    mostly for first-time population and recovery.

    Non-admins get 403; admins always pass.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    bd = await chatbot_semantic_service.backfill_broker_dealers(db)
    ia = await chatbot_semantic_service.backfill_investment_advisors(db)
    return ChatbotEmbeddingBackfillResponse(
        embedded=bd.embedded + ia.embedded,
        skipped=bd.skipped + ia.skipped,
        failed=bd.failed + ia.failed,
        broker_dealers=ChatbotEmbeddingBackfillEntityCounts(
            embedded=bd.embedded, skipped=bd.skipped, failed=bd.failed
        ),
        investment_advisors=ChatbotEmbeddingBackfillEntityCounts(
            embedded=ia.embedded, skipped=ia.skipped, failed=ia.failed
        ),
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


# Preview length for the conversation list — long enough to recognise a
# thread, short enough for one line in the 380px panel.
_PREVIEW_MAX_CHARS = 80


def _conversation_preview(first_user_message: str | None) -> str:
    """Collapse the first user message into a one-line list preview."""
    collapsed = " ".join((first_user_message or "").split())
    if not collapsed:
        # Conversation with no user turn yet (e.g. "New chat" pressed and
        # abandoned) — give the FE something human to render.
        return "New conversation"
    if len(collapsed) <= _PREVIEW_MAX_CHARS:
        return collapsed
    return collapsed[:_PREVIEW_MAX_CHARS].rstrip() + "…"


def _to_conversation_summary(
    listing: ConversationListing,
) -> ChatbotConversationSummary:
    conversation = listing.conversation
    return ChatbotConversationSummary(
        id=conversation.id,
        started_at=conversation.started_at,
        updated_at=conversation.updated_at,
        archived_at=conversation.archived_at,
        is_active=conversation.archived_at is None,
        message_count=listing.message_count,
        preview=_conversation_preview(listing.first_user_message),
    )


@router.get("/conversations", response_model=ChatbotConversationListResponse)
async def get_chatbot_conversations(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotConversationListResponse:
    """Newest-first list of the current user's conversations (cap 50).

    Powers the history browser in the chat panel — includes the active
    conversation (``is_active``) alongside archived ones so the list is
    a complete picture of the user's recent threads.
    """
    listings = await chatbot_history_service.list_conversations(
        db, user_id=current_user.id
    )
    return ChatbotConversationListResponse(
        conversations=[_to_conversation_summary(listing) for listing in listings]
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatbotHistoryResponse,
)
async def get_chatbot_conversation_messages(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotHistoryResponse:
    """Read-only transcript of one conversation (active or archived).

    404 — not 403 — when the id doesn't exist or belongs to a different
    user, so the response doesn't leak which conversation ids exist.
    """
    conversation = await chatbot_history_service.get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=current_user.id
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    rows = await chatbot_history_service.list_messages(
        db, conversation_id=conversation.id
    )
    return ChatbotHistoryResponse(
        conversation_id=conversation.id,
        messages=[ChatbotHistoryMessage.model_validate(row) for row in rows],
    )


@router.post(
    "/conversations/{conversation_id}/reopen",
    response_model=ChatbotConversationSummary,
)
async def post_chatbot_conversation_reopen(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatbotConversationSummary:
    """Make an archived conversation the active one again.

    Archives the currently active conversation (when a different one
    exists), clears the target's ``archived_at``, and returns the
    target's summary. Idempotent when the target is already active.
    Same 404-over-403 ownership semantics as the transcript endpoint.
    """
    conversation = await chatbot_history_service.reopen_conversation(
        db, conversation_id=conversation_id, user_id=current_user.id
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    listing = await chatbot_history_service.summarize_conversation(
        db, conversation=conversation
    )
    return _to_conversation_summary(listing)
