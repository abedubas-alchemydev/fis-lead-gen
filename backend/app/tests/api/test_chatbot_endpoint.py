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
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext, ChatTurnUsage
from app.services.auth import get_current_user
from app.services.chatbot_history import ConversationListing
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

ENDPOINT_POST = "/api/v1/chatbot/messages"
ENDPOINT_STREAM = "/api/v1/chatbot/messages/stream"
ENDPOINT_GET = "/api/v1/chatbot/messages"
ENDPOINT_NEW = "/api/v1/chatbot/conversations/new"
ENDPOINT_BACKFILL = "/api/v1/chatbot/embeddings/backfill"
ENDPOINT_CONVERSATIONS = "/api/v1/chatbot/conversations"


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
    """Drop-in replacement for ``ChatbotService``.

    ``stream_events`` controls what ``chat_stream`` yields — leave empty
    to default to a single text + done sequence. The async-generator
    stub exists so the streaming endpoint tests can drive event-by-event
    behaviour without spinning up Gemini.
    """

    def __init__(
        self,
        *,
        reply: str | None = None,
        raises: Exception | None = None,
        stream_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.reply = reply
        self.raises = raises
        self.stream_events = stream_events
        self.calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def chat(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        user: AuthenticatedUser,
        db: Any,
        page_context: ChatbotPageContext | None = None,
        tools: Any = None,
    ) -> tuple[str, ChatTurnUsage]:
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
        # The real service now returns (reply, usage); the endpoint unpacks
        # the tuple and forwards usage to the assistant append.
        usage = ChatTurnUsage(
            prompt_tokens=11,
            completion_tokens=22,
            total_tokens=33,
            tool_call_count=0,
            latency_ms=44,
        )
        return self.reply, usage

    async def chat_stream(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        user: AuthenticatedUser,
        db: Any,
        page_context: ChatbotPageContext | None = None,
        tools: Any = None,
    ):
        self.stream_calls.append(
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
        events = self.stream_events
        if events is None:
            # Default: single text delta + done with the configured reply.
            reply = self.reply or "ok"
            events = [
                {"type": "text_delta", "text": reply},
                {"type": "done", "reply": reply},
            ]
        for event in events:
            yield event


@dataclass
class _StubConversation:
    id: int = 7
    started_at: datetime = datetime(2026, 5, 26, 9, 0)
    updated_at: datetime = datetime(2026, 5, 26, 9, 5)
    archived_at: datetime | None = None


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
    # ── conversation-history browser stubs ──────────────────────────────
    # What ``list_conversations`` returns.
    listings: list[ConversationListing] = field(default_factory=list)
    # The single conversation the stub "owns" for the current user; a
    # request for any other id simulates not-found / another user's row.
    owned_conversation: _StubConversation | None = None
    # Aggregates returned by ``summarize_conversation``.
    summary_message_count: int = 0
    summary_first_user_message: str | None = None

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
        usage: ChatTurnUsage | None = None,
    ) -> _StubMessageRow:
        self.calls.append(
            {
                "method": "append_message",
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "page_context": page_context,
                "usage": usage,
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

    async def list_conversations(
        self, db: Any, *, user_id: str, limit: int = 50
    ) -> list[ConversationListing]:
        self.calls.append(
            {"method": "list_conversations", "user_id": user_id, "limit": limit}
        )
        return self.listings

    async def get_conversation_for_user(
        self, db: Any, *, conversation_id: int, user_id: str
    ) -> _StubConversation | None:
        self.calls.append(
            {
                "method": "get_conversation_for_user",
                "conversation_id": conversation_id,
                "user_id": user_id,
            }
        )
        if (
            self.owned_conversation is not None
            and self.owned_conversation.id == conversation_id
        ):
            return self.owned_conversation
        return None

    async def reopen_conversation(
        self, db: Any, *, conversation_id: int, user_id: str
    ) -> _StubConversation | None:
        self.calls.append(
            {
                "method": "reopen_conversation",
                "conversation_id": conversation_id,
                "user_id": user_id,
            }
        )
        if (
            self.owned_conversation is not None
            and self.owned_conversation.id == conversation_id
        ):
            # Mirror the real service: the target comes back active.
            self.owned_conversation.archived_at = None
            return self.owned_conversation
        return None

    async def summarize_conversation(
        self, db: Any, *, conversation: _StubConversation
    ) -> ConversationListing:
        self.calls.append(
            {"method": "summarize_conversation", "conversation_id": conversation.id}
        )
        return ConversationListing(
            conversation=conversation,
            message_count=self.summary_message_count,
            first_user_message=self.summary_first_user_message,
        )


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


async def _post_stream(payload: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT_STREAM, json=payload)


def _parse_sse_events(body: bytes) -> list[dict[str, Any]]:
    """Decode an SSE response body into a list of parsed event dicts."""
    import json as _json

    text = body.decode("utf-8")
    events: list[dict[str, Any]] = []
    for raw in text.split("\n\n"):
        if not raw.strip():
            continue
        data_lines = [
            line[5:].lstrip() for line in raw.split("\n") if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            events.append(_json.loads("\n".join(data_lines)))
        except _json.JSONDecodeError:
            continue
    return events


async def _get_history() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(ENDPOINT_GET)


async def _post_new_conversation() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT_NEW)


async def _get_conversations() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(ENDPOINT_CONVERSATIONS)


async def _get_conversation_messages(conversation_id: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(f"{ENDPOINT_CONVERSATIONS}/{conversation_id}/messages")


async def _post_reopen(conversation_id: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"{ENDPOINT_CONVERSATIONS}/{conversation_id}/reopen")


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


async def test_accepts_entity_fields_in_page_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old clients omit ``entity_type``/``entity_id`` (covered by the test
    above); new clients send them from firm detail routes. The payload must
    validate and the parsed fields must reach the chatbot service. The
    history layer keeps persisting only path/title off the same object."""
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="It's Acme.")
    )

    response = await _post_message(
        {
            "messages": [
                {"role": "user", "content": "Is this firm self-clearing?"}
            ],
            "page_context": {
                "path": "/master-list/678",
                "title": "Acme Securities — DOX",
                "entity_type": "broker_dealer",
                "entity_id": 678,
            },
        }
    )

    assert response.status_code == 200
    assert chatbot.calls[0]["page_context"] == ChatbotPageContext(
        path="/master-list/678",
        title="Acme Securities — DOX",
        entity_type="broker_dealer",
        entity_id=678,
    )
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert appends[0]["page_context"].entity_id == 678


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


# ── GET /conversations — history browser listing ────────────────────────


async def test_list_conversations_maps_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _StubConversation(
        id=21,
        started_at=datetime(2026, 6, 2, 10, 0),
        updated_at=datetime(2026, 6, 2, 10, 30),
        archived_at=None,
    )
    archived = _StubConversation(
        id=20,
        started_at=datetime(2026, 6, 1, 9, 0),
        updated_at=datetime(2026, 6, 1, 9, 45),
        archived_at=datetime(2026, 6, 2, 10, 0),
    )
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            listings=[
                ConversationListing(
                    conversation=active,
                    message_count=4,
                    first_user_message="What firms clear through Apex?",
                ),
                ConversationListing(
                    conversation=archived,
                    message_count=0,
                    first_user_message=None,
                ),
            ]
        ),
    )

    response = await _get_conversations()
    assert response.status_code == 200
    rows = response.json()["conversations"]
    assert [r["id"] for r in rows] == [21, 20]

    assert rows[0]["is_active"] is True
    assert rows[0]["archived_at"] is None
    assert rows[0]["message_count"] == 4
    assert rows[0]["preview"] == "What firms clear through Apex?"
    assert rows[0]["started_at"].startswith("2026-06-02")

    # No user turn yet → fallback preview; archived row isn't active.
    assert rows[1]["is_active"] is False
    assert rows[1]["archived_at"] is not None
    assert rows[1]["message_count"] == 0
    assert rows[1]["preview"] == "New conversation"

    assert any(c["method"] == "list_conversations" for c in history.calls)


async def test_list_conversations_preview_truncated_and_collapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_message = "tell me about " + "clearing firms " * 20  # ≫ 80 chars
    multiline = "line one\n\n   line two\t\tspaced"
    _, _history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            listings=[
                ConversationListing(
                    conversation=_StubConversation(id=1),
                    message_count=2,
                    first_user_message=long_message,
                ),
                ConversationListing(
                    conversation=_StubConversation(id=2),
                    message_count=2,
                    first_user_message=multiline,
                ),
            ]
        ),
    )

    response = await _get_conversations()
    assert response.status_code == 200
    rows = response.json()["conversations"]

    truncated = rows[0]["preview"]
    assert truncated.endswith("…")
    # 80 content chars + the ellipsis (rstrip may shave a trailing space).
    assert len(truncated) <= 81
    assert truncated[:-1].rstrip() == long_message[:80].rstrip()

    # Newlines / runs of whitespace collapse to single spaces.
    assert rows[1]["preview"] == "line one line two spaced"


async def test_list_conversations_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(listings=[]),
    )

    response = await _get_conversations()
    assert response.status_code == 200
    assert response.json() == {"conversations": []}


# ── GET /conversations/{id}/messages — transcript + ownership ───────────


async def test_get_conversation_messages_returns_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            owned_conversation=_StubConversation(id=42),
            messages=[
                _StubMessageRow(id=1, role="user", content="hi", created_at=datetime(2026, 5, 1, 9)),
                _StubMessageRow(id=2, role="assistant", content="hey there", created_at=datetime(2026, 5, 1, 9, 1)),
            ],
        ),
    )

    response = await _get_conversation_messages(42)
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == 42
    assert [m["content"] for m in body["messages"]] == ["hi", "hey there"]
    # Transcript was fetched for the OWNED conversation, post-ownership-check.
    assert any(c["method"] == "get_conversation_for_user" for c in history.calls)
    assert any(
        c["method"] == "list_messages" and c["conversation_id"] == 42
        for c in history.calls
    )


async def test_get_conversation_messages_404_when_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another user's id (or a nonexistent one) must 404 without ever
    touching the transcript."""
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            owned_conversation=_StubConversation(id=42),
            messages=[
                _StubMessageRow(id=1, role="user", content="secret", created_at=datetime(2026, 5, 1, 9)),
            ],
        ),
    )

    response = await _get_conversation_messages(999)
    assert response.status_code == 404
    assert not any(c["method"] == "list_messages" for c in history.calls)


# ── POST /conversations/{id}/reopen — swap-active semantics ─────────────


async def test_reopen_conversation_returns_active_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = _StubConversation(
        id=9,
        archived_at=datetime(2026, 6, 1, 8, 0),
    )
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(
            owned_conversation=archived,
            summary_message_count=6,
            summary_first_user_message="Who clears for Robinhood?",
        ),
    )

    response = await _post_reopen(9)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 9
    # Reopened conversation comes back as the active one.
    assert body["is_active"] is True
    assert body["archived_at"] is None
    assert body["message_count"] == 6
    assert body["preview"] == "Who clears for Robinhood?"

    reopens = [c for c in history.calls if c["method"] == "reopen_conversation"]
    assert len(reopens) == 1
    assert reopens[0]["conversation_id"] == 9


async def test_reopen_conversation_404_when_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(reply="unused"),
        history=_StubHistoryService(owned_conversation=_StubConversation(id=9)),
    )

    response = await _post_reopen(123456)
    assert response.status_code == 404
    # No summary computed for a rejected reopen.
    assert not any(
        c["method"] == "summarize_conversation" for c in history.calls
    )


# ── POST /messages/stream — SSE ─────────────────────────────────────────


async def test_stream_returns_event_stream_content_type_and_events_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chatbot, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(
            stream_events=[
                {"type": "tool_call", "name": "search_broker_dealers"},
                {"type": "tool_result", "name": "search_broker_dealers", "error": None},
                {"type": "text_delta", "text": "Hi "},
                {"type": "text_delta", "text": "there."},
                {"type": "done", "reply": "Hi there."},
            ]
        ),
    )

    response = await _post_stream(
        {"messages": [{"role": "user", "content": "find Apex"}]}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("cache-control") == "no-cache"

    events = _parse_sse_events(response.content)
    assert [e["type"] for e in events] == [
        "tool_call",
        "tool_result",
        "text_delta",
        "text_delta",
        "done",
    ]
    # Service was called once via the streaming method, not the regular one.
    assert len(chatbot.stream_calls) == 1
    assert chatbot.calls == []
    # User turn persisted before stream + assistant turn after done.
    appends = [c for c in history.calls if c["method"] == "append_message"]
    assert [a["role"] for a in appends] == ["user", "assistant"]
    assert appends[1]["content"] == "Hi there."


async def test_stream_rejects_bad_payload_before_opening_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="ignored")
    )

    response = await _post_stream(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
    )
    assert response.status_code == 400
    # Neither service was touched.
    assert chatbot.stream_calls == []
    assert history.calls == []


async def test_stream_rejects_oversized_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chatbot, history = _install_stubs(
        monkeypatch, chatbot=_StubChatbotService(reply="ignored")
    )

    big = "x" * 8000
    payload_messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": big}
        for i in range(5)
    ]
    payload_messages.append({"role": "user", "content": big})

    response = await _post_stream({"messages": payload_messages})
    assert response.status_code == 413
    assert chatbot.stream_calls == []
    assert history.calls == []


async def test_stream_error_event_does_not_persist_assistant_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the stream terminates with an ``error`` event (no ``done``),
    the assistant-turn append must be skipped — the user message stays
    on record but no empty assistant row is inserted."""
    _, history = _install_stubs(
        monkeypatch,
        chatbot=_StubChatbotService(
            stream_events=[
                {"type": "error", "code": "extraction", "message": "boom"},
            ]
        ),
    )

    response = await _post_stream(
        {"messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 200  # SSE response opened successfully
    events = _parse_sse_events(response.content)
    assert [e["type"] for e in events] == ["error"]
    appends = [c for c in history.calls if c["method"] == "append_message"]
    # Only the user turn was persisted.
    assert [a["role"] for a in appends] == ["user"]


# ── POST /embeddings/backfill ───────────────────────────────────────────


@dataclass
class _StubSemanticService:
    embedded: int = 0
    skipped: int = 0
    failed: int = 0
    ia_embedded: int = 0
    ia_skipped: int = 0
    ia_failed: int = 0
    calls: list[dict[str, object]] = field(default_factory=list)

    async def backfill_broker_dealers(self, db: Any) -> Any:
        self.calls.append({"method": "backfill_broker_dealers", "db": db})
        from app.services.chatbot_semantic import BackfillResult

        return BackfillResult(
            embedded=self.embedded, skipped=self.skipped, failed=self.failed
        )

    async def backfill_investment_advisors(self, db: Any) -> Any:
        self.calls.append({"method": "backfill_investment_advisors", "db": db})
        from app.services.chatbot_semantic import BackfillResult

        return BackfillResult(
            embedded=self.ia_embedded,
            skipped=self.ia_skipped,
            failed=self.ia_failed,
        )


def _install_semantic_stub(
    monkeypatch: pytest.MonkeyPatch, stub: _StubSemanticService
) -> _StubSemanticService:
    monkeypatch.setattr(endpoint_module, "chatbot_semantic_service", stub)
    return stub


def _override_admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"chatbot-admin-test-{secrets.token_hex(4)}",
        name="Admin Tester",
        email="admin-test@example.com",
        role="admin",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _post_backfill() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(ENDPOINT_BACKFILL)


async def test_backfill_admin_returns_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_current_user] = _override_admin_user
    try:
        stub = _install_semantic_stub(
            monkeypatch,
            _StubSemanticService(
                embedded=12,
                skipped=3,
                failed=1,
                ia_embedded=7,
                ia_skipped=2,
                ia_failed=0,
            ),
        )
        response = await _post_backfill()
        assert response.status_code == 200
        # Top-level counts stay the all-entities totals (the original
        # contract); the per-entity breakdowns are additive.
        assert response.json() == {
            "embedded": 19,
            "skipped": 5,
            "failed": 1,
            "broker_dealers": {"embedded": 12, "skipped": 3, "failed": 1},
            "investment_advisors": {"embedded": 7, "skipped": 2, "failed": 0},
        }
        assert [c["method"] for c in stub.calls] == [
            "backfill_broker_dealers",
            "backfill_investment_advisors",
        ]
    finally:
        # Restore the viewer override so subsequent tests see the
        # autouse fixture's expected state.
        app.dependency_overrides[get_current_user] = _override_user


async def test_backfill_non_admin_403(monkeypatch: pytest.MonkeyPatch) -> None:
    # Viewer is the autouse default — no admin override here.
    stub = _install_semantic_stub(monkeypatch, _StubSemanticService())
    response = await _post_backfill()
    assert response.status_code == 403
    # Service must NOT be called when the auth gate rejects.
    assert stub.calls == []


# ── Auth gate ───────────────────────────────────────────────────────────


async def test_requires_authenticated_session() -> None:
    """When the auth override is removed, every chatbot route must 401."""
    app.dependency_overrides.clear()
    try:
        for fetch in (
            _post_message({"messages": [{"role": "user", "content": "hi"}]}),
            _post_stream({"messages": [{"role": "user", "content": "hi"}]}),
            _get_history(),
            _post_new_conversation(),
            _post_backfill(),
            _get_conversations(),
            _get_conversation_messages(1),
            _post_reopen(1),
        ):
            response = await fetch
            assert response.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = _override_user
