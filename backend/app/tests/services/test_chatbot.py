"""Unit tests for ``app.services.chatbot.ChatbotService``.

respx mocks the Gemini ``generateContent`` endpoint so no network is touched.
Covers payload shape (system prompt + page context + role mapping), happy
path response extraction, retry/exhaustion error mapping, the missing-key
guard, and the empty-parts (safety-filter) failure mode.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext
from app.services.chatbot import DOXIE_SYSTEM_PROMPT, ChatbotService
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_CHAT_URL = f"{_GEMINI_BASE}/models/gemini-2.5-flash:generateContent"


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "gemini_api_key", "AIzaSy" + "x" * 33
    )
    monkeypatch.setattr(settings, "gemini_api_base", _GEMINI_BASE)
    monkeypatch.setattr(settings, "gemini_chat_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 2)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.chatbot.asyncio.sleep", _instant_sleep)


def _reply_payload(text: str = "Hello from Doxie!") -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": text}],
                },
                "finishReason": "STOP",
            }
        ]
    }


@respx.mock
@pytest.mark.asyncio
async def test_chat_returns_reply_text_and_sends_doxie_system_prompt(
    patch_gemini: None,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload("Hi there!"))

    route = respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Hello")]
    )

    assert reply == "Hi there!"
    assert route.call_count == 1
    body = json.loads(captured["body"])
    # Doxie persona is wired into systemInstruction.
    sys_text = body["systemInstruction"]["parts"][0]["text"]
    assert DOXIE_SYSTEM_PROMPT in sys_text
    # Conversation history is in contents with role=user.
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Hello"}]}
    ]
    # Generation config uses the chat settings, not extraction defaults.
    gen = body["generationConfig"]
    assert gen["temperature"] == settings.gemini_chat_temperature
    assert gen["maxOutputTokens"] == settings.gemini_chat_max_output_tokens
    # No JSON schema constraint — free-form replies.
    assert "responseJsonSchema" not in gen
    assert "responseMimeType" not in gen


@respx.mock
@pytest.mark.asyncio
async def test_chat_maps_assistant_role_to_model_for_gemini(
    patch_gemini: None,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload())

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    await ChatbotService().chat(
        messages=[
            ChatbotMessage(role="user", content="Hi"),
            ChatbotMessage(role="assistant", content="Hello back"),
            ChatbotMessage(role="user", content="How are you?"),
        ]
    )

    body = json.loads(captured["body"])
    roles = [c["role"] for c in body["contents"]]
    # FE-side "assistant" must translate to Gemini's "model" wire role.
    assert roles == ["user", "model", "user"]


@respx.mock
@pytest.mark.asyncio
async def test_chat_folds_page_context_into_system_prompt(
    patch_gemini: None,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload())

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What is this firm?")],
        page_context=ChatbotPageContext(
            path="/broker-dealers/123", title="Acme Securities — Doxie"
        ),
    )

    body = json.loads(captured["body"])
    sys_text = body["systemInstruction"]["parts"][0]["text"]
    assert "/broker-dealers/123" in sys_text
    assert "Acme Securities" in sys_text


@pytest.mark.asyncio
async def test_chat_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(GeminiConfigurationError):
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")]
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_raises_extraction_error_on_persistent_5xx(
    patch_gemini: None, no_backoff_sleep: None
) -> None:
    respx.post(_GEMINI_CHAT_URL).mock(
        return_value=httpx.Response(503, text="upstream unavailable")
    )
    with pytest.raises(GeminiExtractionError):
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")]
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_retries_transient_5xx_then_succeeds(
    patch_gemini: None, no_backoff_sleep: None
) -> None:
    route = respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(503, text="try again"),
            httpx.Response(200, json=_reply_payload("recovered")),
        ]
    )

    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")]
    )

    assert reply == "recovered"
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_chat_raises_on_empty_safety_blocked_response(
    patch_gemini: None,
) -> None:
    """Gemini returns an empty parts array with finishReason=SAFETY when its
    safety filter trips. The service must surface that as a clean error
    rather than returning an empty string the FE would render as a blank
    bubble."""
    respx.post(_GEMINI_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": []}, "finishReason": "SAFETY"}
                ]
            },
        )
    )

    with pytest.raises(GeminiExtractionError) as exc:
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")]
        )
    assert "SAFETY" in str(exc.value)
