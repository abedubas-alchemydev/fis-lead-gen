"""Endpoint tests for the /api/v1/chatbot routes.

Bypasses BetterAuth via ``app.dependency_overrides`` (same pattern as
test_verify_endpoint.py) and patches the endpoint module's bound
``chatbot_service`` AND ``chatbot_history_service`` so we exercise the
HTTP contract — validation, auth wiring, persistence ordering, and the
upstream-error mapping — without calling Gemini or touching a real DB.

The integration counterparts (``test_chatbot_history.py``) cover the
service's interaction with Postgres.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
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

ENDPOINT_POST = "/api/v1/chatbot/messages"
ENDPOINT_GET = "/api/v1/chatbot/messages"
ENDPOINT_NEW = "/api/v1/chatbot/conversations/new"


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
    """Drop-in replacement for ``ChatbotService``."""

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


@dataclass
class _StubConversation:
    id: int = 7


@dataclass
class _StubMessageRow:
    """Mimics enough of a ChatbotMessage ORM row for ``model_validate``."""
    id: int
    role: str
    content: str
    created_at: datetime


@dataclass
class _StubHistoryService:
    """Drop-in for ``ChatbotHistoryService`` — records every call.

    ``conversation`` is what ``get_or_create_active_conversation`` returns
    (and what ``archive_active_and_create_new`` returns). ``messages`` is
    what ``list_messages`` returns. Per-method ``*_raises`` lets a test
    inject a failure at a specific point in the flow.
    """

    conversation: _StubConversation = field(default_factory=_StubConversation)
    messages: list[_StubMessageRow] = field(default_factory=list)
    append_raises: Exception | None = None
    archive_raises: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)
    # Tracks how many appends have happened so a test can inject a failure
    # only on the second (assistant-turn) append.
    append_fail_on_call: int | None = None

    async def get_or_create_active_conversation(
        self, db: Any, *, user_id: str
    ) -> _StubConversation:
        self.calls.append({"method": "get_or_create_active_conversation", "user_id": user_id})
        return self.conversation

    async def append_message(
        self,
        db: Any,
        *,
        conversation_id: int,
        role: str,
        content: str,
        page_context: ChatbotPageContext | None = None,
    ) -> _StubMessageRow:
        self.calls.append(
            {
                "method": "append_message",
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "page_context": page_context,
            }
        )
        append_count = sum(
            1 for c in self.calls if c["method"] == "append_message"
        )
        if (
            self.append_fail_on_call is not None
            and append_count == self.append_fail_on_call
        ):
            raise RuntimeError("simulated append failure")
        if self.append_raises is not None:
            raise self.append_raises
        return _StubMessageRow(
            id=len(self.messages) + 1,
            role=role,
            content=content,
            created_at=datetime(2026, 5, 26, 12, 0),
        )

    async def list_messages(
        self, db: Any, *, conversation_id: int
    ) -> list[_StubMessageRow]:
        self.calls.append({"method": "list_messages", "conversation_id": conversation_id})
        return self.messages

    async def archive_active_and_create_new(
        self, db: Any, *, user_id: str
    ) -> _StubConversation:
        self.calls.append({"method": "archive_active_and_create_new", "user_id": user_id})
        if self.archive_raises is not None:
            raise self.archive_raises
        return _StubConversation(id=self.conversation.id + 1)


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chatbot: _StubChatbotService,
    history: _StubHistoryService | None = None,
) -> tuple[_StubChatbotService, _StubHistoryService]:
    monkeypatch.setattr(endpoint_module, "chatbot_service", chatbot)
    history_stub = history or _StubHistoryService()
    monkeypatch.setattr(endpoint_module, "chatbot_history_service", history_stub)
    return chatbot, history_stub


async def _post_message(payload: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT_POST, json=payload)


async def _get_history() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(ENDPOINT_GET)


async def _post_new_conversation() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT_NEW)


# ── POST /messages — happy path + validation ────────────────────────────


async def test_returns_reply_and_persists_user_then_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="Sure — here's the rundown.")
    )

    response = await _post_message(
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

    # Both turns persisted in order: user first, then assistant.
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert [a["role"] for a in appends] == ["user", "assistant"]
    assert appends[0]["content"] == "Tell me about this firm."
    assert appends[0]["page_context"] == ChatbotPageContext(
        path="/broker-dealers/42", title="Acme Securities — Doxie"
    )
    # Assistant turn is the chatbot reply.
    assert appends[1]["content"] == "Sure — here's the rundown."
    # Assistant turns don't carry a page context.
    assert appends[1]["page_context"] is None
    # Chatbot service is called once and gets the user + db.
    assert len(chatbot.calls) == 1


async def test_rejects_when_last_message_is_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="unused")
    )

    response = await _post_message(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
    )

    assert response.status_code == 400
    # Neither service should have been touched on a validation failure.
    assert chatbot.calls == []
    assert history.calls == []


async def test_rejects_oversized_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="unused")
    )

    big = "x" * 8000
    payload_messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": big}
        for i in range(5)
    ]
    payload_messages.append({"role": "user", "content": big})

    response = await _post_message({"messages": payload_messages})
    assert response.status_code == 413
    assert chatbot.calls == []
    assert history.calls == []


async def test_maps_gemini_config_error_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(raises=GeminiConfigurationError("no key")),
    )

    response = await _post_message({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 503
    # User turn still persisted before Gemini was tried.
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert [a["role"] for a in appends] == ["user"]


async def test_maps_gemini_extraction_error_to_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(raises=GeminiExtractionError("upstream blew up")),
    )

    response = await _post_message({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 502
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert [a["role"] for a in appends] == ["user"]


async def test_threads_user_and_db_into_service(monkeypatch: pytest.MonkeyPatch) -> None:
    chatbot, _ = _install_stubs(monkeypatch, chatbot=_StubChatbotService(reply="ok"))

    response = await _post_message({"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert len(chatbot.calls) == 1
    call = chatbot.calls[0]
    assert isinstance(call["user"], AuthenticatedUser)
    assert call["db"] is not None


async def test_assistant_persistence_failure_maps_to_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the assistant-turn append fails, the endpoint must surface 500.
    The user already got a reply from Gemini but we can't silently lose
    it from history."""
    chatbot, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="ignored — should not reach the user"),
        history=_StubHistoryService(append_fail_on_call=2),  # fail on 2nd append
    )

    response = await _post_message({"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 500
    assert "couldn't be saved" in response.json()["detail"]
    # Gemini was called.
    assert len(chatbot.calls) == 1
    # User append happened, assistant append attempted (and failed).
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert [a["role"] for a in appends] == ["user", "assistant"]


# ── GET /messages — history load ────────────────────────────────────────


async def test_get_history_returns_persisted_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            conversation=_StubConversation(id=42),
            messages=[
                _StubMessageRow(id=1, role="user", content="hi", created_at=datetime(2026, 5, 1, 9)),
                _StubMessageRow(id=2, role="assistant", content="hey there", created_at=datetime(2026, 5, 1, 9, 1)),
            ],
        ),
    )

    response = await _get_history()
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == 42
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert [m["content"] for m in body["messages"]] == ["hi", "hey there"]
    assert all("created_at" in m for m in body["messages"])


async def test_get_history_empty_when_no_prior_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(messages=[]),
    )

    response = await _get_history()
    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == []
    # Even with no prior history, BE should have created/found a
    # conversation id so subsequent POSTs reuse it.
    assert isinstance(body["conversation_id"], int)


# ── POST /conversations/new — archive + start fresh ─────────────────────


async def test_new_conversation_returns_new_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(conversation=_StubConversation(id=10)),
    )

    response = await _post_new_conversation()
    assert response.status_code == 201
    body = response.json()
    # Stub archive_active_and_create_new returns conversation.id + 1.
    assert body["conversation_id"] == 11
    assert any(
        c["method"] == "archive_active_and_create_new" for c in history.calls
    )


# ── Auth gate ───────────────────────────────────────────────────────────


async def test_requires_authenticated_session() -> None:
    """When the auth override is removed, every chatbot route must 401."""
    app.dependency_overrides.clear()
    try:
        for fetch in (
            _post_message({"messages": [{"role": "user", "content": "hi"}]}),
            _get_history(),
            _post_new_conversation(),
        ):
            response = await fetch
            assert response.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = _override_user
