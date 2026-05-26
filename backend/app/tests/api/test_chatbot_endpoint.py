"""Endpoint tests for POST /api/v1/chatbot/messages.

Bypasses BetterAuth via ``app.dependency_overrides`` (same pattern as
test_verify_endpoint.py / test_email_extractor_enrich_all.py) and patches
the endpoint module's bound ``chatbot_service`` so we exercise the HTTP
contract — validation, auth wiring, and the upstream-error mapping — without
calling Gemini.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Sequence

import httpx
import pytest

from app.api.v1.endpoints import chatbot as endpoint_module
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext
from app.services.auth import get_current_user
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

ENDPOINT = "/api/v1/chatbot/messages"


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"chatbot-test-{secrets.token_hex(4)}",
        name="Chatbot Tester",
        email="chatbot-test@example.com",
        role="viewer",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture(autouse=True)
def _bypass_auth() -> object:
    app.dependency_overrides[get_current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.clear()


class _StubChatbotService:
    """Drop-in replacement for ``ChatbotService`` capturing its inputs.

    Signature mirrors ``ChatbotService.chat`` after the Phase 2 refactor:
    accepts ``user`` + ``db`` (threaded by the endpoint via Depends) and an
    optional ``tools`` mapping (defaults to the production registry in real
    code; we ignore it here since the stub never dispatches).
    """

    def __init__(
        self,
        *,
        reply: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        user: AuthenticatedUser,
        db: Any,
        page_context: ChatbotPageContext | None = None,
        tools: Any = None,
    ) -> str:
        self.calls.append(
            {
                "messages": list(messages),
                "page_context": page_context,
                "user": user,
                "db": db,
                "tools": tools,
            }
        )
        if self.raises is not None:
            raise self.raises
        assert self.reply is not None
        return self.reply


def _install_stub_service(
    monkeypatch: pytest.MonkeyPatch, stub: _StubChatbotService
) -> _StubChatbotService:
    monkeypatch.setattr(endpoint_module, "chatbot_service", stub)
    return stub


async def _post(payload: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT, json=payload)


async def test_returns_reply_and_forwards_context(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _install_stub_service(
        monkeypatch, _StubChatbotService(reply="Sure — here's the rundown.")
    )

    response = await _post(
        {
            "messages": [{"role": "user", "content": "Tell me about this firm."}],
            "page_context": {
                "path": "/broker-dealers/42",
                "title": "Acme Securities — Doxie",
            },
        }
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Sure — here's the rundown."}
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert [m.content for m in call["messages"]] == ["Tell me about this firm."]
    assert call["page_context"] == ChatbotPageContext(
        path="/broker-dealers/42", title="Acme Securities — Doxie"
    )


async def test_rejects_when_last_message_is_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _install_stub_service(monkeypatch, _StubChatbotService(reply="unused"))

    response = await _post(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
    )

    assert response.status_code == 400
    assert "last message" in response.json()["detail"].lower()
    # Service must NOT be called on a validation failure.
    assert stub.calls == []


async def test_rejects_oversized_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _install_stub_service(monkeypatch, _StubChatbotService(reply="unused"))

    # Six 8000-char messages = 48000 chars, comfortably above the 40000 cap
    # and within each per-message 8000 char limit.
    big = "x" * 8000
    payload_messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": big}
        for i in range(5)
    ]
    payload_messages.append({"role": "user", "content": big})

    response = await _post({"messages": payload_messages})

    assert response.status_code == 413
    assert stub.calls == []


async def test_maps_gemini_config_error_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stub_service(
        monkeypatch,
        _StubChatbotService(raises=GeminiConfigurationError("no key")),
    )

    response = await _post({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


async def test_maps_gemini_extraction_error_to_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_service(
        monkeypatch,
        _StubChatbotService(raises=GeminiExtractionError("upstream blew up")),
    )

    response = await _post({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 502
    assert "try again" in response.json()["detail"].lower()


async def test_threads_user_and_db_into_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint must forward ``current_user`` and the DB session into
    ``chatbot_service.chat`` so the tool dispatcher can enforce per-user
    feature permissions and run repo queries."""
    stub = _install_stub_service(monkeypatch, _StubChatbotService(reply="ok"))

    response = await _post({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert isinstance(call["user"], AuthenticatedUser)
    # We can't easily assert the concrete AsyncSession type without
    # opening one, but it must be non-None and forwarded as a kwarg.
    assert call["db"] is not None


async def test_requires_authenticated_session() -> None:
    """When the auth override is removed, the endpoint must 401."""
    app.dependency_overrides.clear()
    try:
        response = await _post({"messages": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 401
    finally:
        # Restore the override so the autouse fixture's teardown doesn't
        # double-clear anything.
        app.dependency_overrides[get_current_user] = _override_user
