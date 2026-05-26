"""Doxie chatbot service — free-form Gemini chat (no JSON schema).

Distinct from ``gemini_responses.py`` which constrains every call with a
``responseJsonSchema`` because its outputs feed structured DB writes. The
chatbot just needs natural-language replies, so we drop the schema and let
the model speak normally.

The Gemini ``generateContent`` payload uses a ``systemInstruction`` block for
the persona + optional page context, then walks the conversation history as
alternating ``user`` and ``model`` turns. ``ChatbotMessage.role`` uses the
OpenAI-style ``user``/``assistant`` labels (which is what the FE already
emits); this service maps ``assistant`` → ``model`` at the wire boundary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

import httpx

from app.core.config import settings
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)

# Doxie persona — kept short so most of the model's prompt budget stays
# available for the user's conversation history.
DOXIE_SYSTEM_PROMPT = (
    "You are Doxie, the in-app assistant for the Alchemy broker-dealer "
    "and investment-advisor intelligence platform. Help users navigate "
    "the app, understand financial regulatory data (Form BD, Form ADV, "
    "FOCUS reports, clearing relationships), and draft outreach. Be "
    "concise, friendly, and direct. If a question is outside the app's "
    "scope, answer briefly without speculating about firm data you "
    "have not been shown. Do not invent numbers, names, or filings."
)


class ChatbotService:
    """Stateless wrapper around Gemini's free-form ``generateContent``."""

    def __init__(self) -> None:
        self.base_url = settings.gemini_api_base.rstrip("/")
        self.timeout = settings.gemini_request_timeout_seconds
        self.max_retries = max(1, settings.gemini_request_max_retries)

    async def chat(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        page_context: ChatbotPageContext | None = None,
    ) -> str:
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        if not messages:
            raise GeminiExtractionError("messages must contain at least one entry.")

        payload = self._build_payload(messages=messages, page_context=page_context)
        response_payload = await self._post_with_retries(payload)
        return self._extract_text(response_payload)

    def _build_payload(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        page_context: ChatbotPageContext | None,
    ) -> dict[str, object]:
        system_text = DOXIE_SYSTEM_PROMPT
        context_block = self._format_page_context(page_context)
        if context_block:
            system_text = f"{system_text}\n\n{context_block}"

        contents: list[dict[str, object]] = []
        for message in messages:
            # Gemini calls the assistant role "model"; the FE emits the
            # OpenAI-style "assistant" label that the rest of the codebase
            # already uses, so map at the wire boundary.
            role = "model" if message.role == "assistant" else "user"
            contents.append(
                {"role": role, "parts": [{"text": message.content}]}
            )

        return {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {
                "temperature": settings.gemini_chat_temperature,
                "maxOutputTokens": settings.gemini_chat_max_output_tokens,
            },
        }

    @staticmethod
    def _format_page_context(context: ChatbotPageContext | None) -> str:
        if context is None:
            return ""
        parts: list[str] = []
        if context.path:
            parts.append(f"path={context.path}")
        if context.title:
            parts.append(f"title={context.title!r}")
        if not parts:
            return ""
        return (
            "Current page context (the user is viewing this page right now): "
            + ", ".join(parts)
        )

    async def _post_with_retries(self, payload: dict[str, object]) -> dict[str, object]:
        url = (
            f"{self.base_url}/models/{settings.gemini_chat_model}:generateContent"
        )
        headers = {"x-goog-api-key": settings.gemini_api_key}
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                retriable = exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
                if not retriable or attempt == self.max_retries:
                    detail = exc.response.text.strip()
                    raise GeminiExtractionError(
                        f"Gemini chat request failed with status "
                        f"{exc.response.status_code}: {detail or 'No response body.'}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise GeminiExtractionError(
                        "Gemini chat request failed due to a network error."
                    ) from exc

            await asyncio.sleep(min(2**attempt, 8))

        raise GeminiExtractionError("Gemini chat request failed after retries.") from last_error

    @staticmethod
    def _extract_text(payload: dict[str, object]) -> str:
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            raise GeminiExtractionError(
                "Gemini chat response did not include any candidates."
            )

        first = candidates[0]
        if not isinstance(first, dict):
            raise GeminiExtractionError("Gemini chat response candidate was malformed.")

        finish_reason = first.get("finishReason")
        content = first.get("content", {})
        if not isinstance(content, dict):
            raise GeminiExtractionError("Gemini chat response content was malformed.")

        parts = content.get("parts", [])
        chunks: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)

        joined = "".join(chunks).strip()
        if joined:
            return joined

        # Safety filters trigger an empty parts array with a finishReason
        # like "SAFETY" or "OTHER"; surface that as a clean error so the FE
        # can render a fallback instead of an empty bubble.
        raise GeminiExtractionError(
            f"Gemini chat response was empty (finishReason={finish_reason!r})."
        )
