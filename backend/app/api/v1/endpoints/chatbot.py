from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import ChatbotRequest, ChatbotResponse
from app.services.auth import get_current_user
from app.services.chatbot import ChatbotService
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chatbot")
chatbot_service = ChatbotService()

# Cap the cumulative size of all message bodies in one call. Per-message
# size is already capped at 8000 chars in the schema; this is a second
# guard against a 50×8000 worst-case payload hammering the Gemini quota
# from a single click.
_MAX_TOTAL_CHARS = 40_000


@router.post("/messages", response_model=ChatbotResponse)
async def post_chatbot_message(
    payload: ChatbotRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
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

    try:
        reply = await chatbot_service.chat(
            messages=payload.messages,
            page_context=payload.page_context,
        )
    except GeminiConfigurationError as exc:
        # 503 — the server is misconfigured (no API key). Treated as a
        # transient unavailable rather than a client error.
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

    return ChatbotResponse(reply=reply)
