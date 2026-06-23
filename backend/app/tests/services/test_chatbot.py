"""Unit tests for ``app.services.chatbot.ChatbotService``.

respx mocks the Gemini ``generateContent`` endpoint so no network is touched.
Covers payload shape (system prompt + page context + role mapping), happy
path response extraction, retry/exhaustion error mapping, the missing-key
guard, and the empty-parts (safety-filter) failure mode.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import httpx
import pytest
import respx

from typing import Any

from app.core.config import settings
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext, ChatTurnUsage
from app.services.chatbot import DOXIE_SYSTEM_PROMPT, ChatbotService
from app.services.chatbot_tools import Tool
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)


# Phase 2 added required ``user`` + ``db`` kwargs to ``chat()``. Pass
# ``tools={}`` everywhere in this module so the Phase 1 behaviour these
# tests cover (no tool calls, no tool-usage prompt, no `tools` field on
# the wire) stays the regression contract.
@pytest.fixture
def user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"phase1-test-{secrets.token_hex(4)}",
        name="Phase1 Tester",
        email="phase1-test@example.com",
        role="viewer",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture
def db_stub() -> object:
    return object()

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


def _reply_payload_with_usage(
    text: str = "Hello from Doxie!",
    *,
    prompt: int = 120,
    completion: int = 45,
    total: int = 165,
) -> dict[str, object]:
    payload = _reply_payload(text)
    payload["usageMetadata"] = {
        "promptTokenCount": prompt,
        "candidatesTokenCount": completion,
        "totalTokenCount": total,
    }
    return payload


@respx.mock
@pytest.mark.asyncio
async def test_chat_returns_reply_text_and_sends_doxie_system_prompt(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload("Hi there!"))

    route = respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    reply, _usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Hello")],
        user=user,
        db=db_stub,
        tools={},
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
    user: AuthenticatedUser,
    db_stub: object,
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
        ],
        user=user,
        db=db_stub,
        tools={},
    )

    body = json.loads(captured["body"])
    roles = [c["role"] for c in body["contents"]]
    # FE-side "assistant" must translate to Gemini's "model" wire role.
    assert roles == ["user", "model", "user"]


@respx.mock
@pytest.mark.asyncio
async def test_chat_folds_page_context_into_system_prompt(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload())

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What is this firm?")],
        user=user,
        db=db_stub,
        page_context=ChatbotPageContext(
            path="/broker-dealers/123", title="Acme Securities — Doxie"
        ),
        tools={},
    )

    body = json.loads(captured["body"])
    sys_text = body["systemInstruction"]["parts"][0]["text"]
    assert "/broker-dealers/123" in sys_text
    assert "Acme Securities" in sys_text


@respx.mock
@pytest.mark.asyncio
async def test_chat_system_prompt_contains_todays_utc_date(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """The per-turn system prompt must carry today's UTC date — computed at
    request time, not import time — so Doxie can reason about recency."""
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload())

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What day is it?")],
        user=user,
        db=db_stub,
        tools={},
    )
    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sys_text = json.loads(captured["body"])["systemInstruction"]["parts"][0]["text"]
    # ``before``/``after`` bracket the call so a midnight rollover mid-test
    # can't flake the assertion.
    assert (
        f"Today's date is {before} (UTC)." in sys_text
        or f"Today's date is {after} (UTC)." in sys_text
    )


# ── Entity-aware page context ────────────────────────────────────────────


class _FakeRowResult:
    def __init__(self, row: tuple[str, str | None] | None) -> None:
        self._row = row

    def first(self) -> tuple[str, str | None] | None:
        return self._row


class _FakeEntityDb:
    """Stands in for ``AsyncSession`` in entity-context tests: returns the
    configured ``(name, crd_number)`` row for any SELECT (or raises) and
    counts calls so tests can assert the lookup was/wasn't attempted."""

    def __init__(
        self,
        row: tuple[str, str | None] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.row = row
        self.raises = raises
        self.execute_calls = 0

    async def execute(self, _stmt: object) -> _FakeRowResult:
        self.execute_calls += 1
        if self.raises is not None:
            raise self.raises
        return _FakeRowResult(self.row)


def _capture_route(captured: dict[str, bytes]) -> None:
    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_reply_payload())

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)


def _sys_text(captured: dict[str, bytes]) -> str:
    return json.loads(captured["body"])["systemInstruction"]["parts"][0]["text"]


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "label", "page_path"),
    [
        ("broker_dealer", "broker-dealer", "/master-list/678"),
        ("investment_advisor", "investment advisor", "/advisor-list/678"),
    ],
)
async def test_chat_entity_context_resolves_firm_into_system_prompt(
    patch_gemini: None,
    user: AuthenticatedUser,
    entity_type: str,
    label: str,
    page_path: str,
) -> None:
    captured: dict[str, bytes] = {}
    _capture_route(captured)
    db = _FakeEntityDb(row=("Acme Securities", "12345"))

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Is this firm self-clearing?")],
        user=user,
        db=db,
        page_context=ChatbotPageContext(
            path=page_path,
            title="Acme Securities — DOX",
            entity_type=entity_type,
            entity_id=678,
        ),
        tools={},
    )

    sys_text = _sys_text(captured)
    assert (
        f"The user is currently viewing the detail page for {label} "
        f"Acme Securities (CRD 12345, id 678)" in sys_text
    )
    assert '"this firm" refers to it' in sys_text
    # Path/title context still rides along with the firm line.
    assert f"path={page_path}" in sys_text
    assert db.execute_calls == 1


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "db",
    [
        pytest.param(_FakeEntityDb(row=None), id="id-not-found"),
        pytest.param(_FakeEntityDb(raises=RuntimeError("db down")), id="db-error"),
    ],
)
async def test_chat_entity_context_miss_falls_back_to_path_title(
    patch_gemini: None,
    user: AuthenticatedUser,
    db: _FakeEntityDb,
) -> None:
    """Unknown id or a DB failure must degrade silently to the plain
    path/title context — no firm line, no error surfaced to the user."""
    captured: dict[str, bytes] = {}
    _capture_route(captured)

    reply, _usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What is this firm?")],
        user=user,
        db=db,
        page_context=ChatbotPageContext(
            path="/master-list/999999",
            title="Broker Dealers — DOX",
            entity_type="broker_dealer",
            entity_id=999999,
        ),
        tools={},
    )

    assert reply == "Hello from Doxie!"
    sys_text = _sys_text(captured)
    assert "path=/master-list/999999" in sys_text
    assert "detail page for" not in sys_text
    assert db.execute_calls == 1


@respx.mock
@pytest.mark.asyncio
async def test_chat_entity_context_unknown_type_skips_db_lookup(
    patch_gemini: None,
    user: AuthenticatedUser,
) -> None:
    """An entity_type the BE doesn't recognise (FE/BE version skew) must
    not even hit the DB — same silent fallback to path/title."""
    captured: dict[str, bytes] = {}
    _capture_route(captured)
    db = _FakeEntityDb(row=("Should Not Appear", "1"))

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What's this?")],
        user=user,
        db=db,
        page_context=ChatbotPageContext(
            path="/vault/3", entity_type="vault_folder", entity_id=3
        ),
        tools={},
    )

    sys_text = _sys_text(captured)
    assert "path=/vault/3" in sys_text
    assert "detail page for" not in sys_text
    assert db.execute_calls == 0


@respx.mock
@pytest.mark.asyncio
async def test_chat_entity_context_omits_crd_when_firm_has_none(
    patch_gemini: None,
    user: AuthenticatedUser,
) -> None:
    captured: dict[str, bytes] = {}
    _capture_route(captured)
    db = _FakeEntityDb(row=("Acme Securities", None))

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Tell me about this firm.")],
        user=user,
        db=db,
        page_context=ChatbotPageContext(
            path="/master-list/678",
            entity_type="broker_dealer",
            entity_id=678,
        ),
        tools={},
    )

    sys_text = _sys_text(captured)
    assert "detail page for broker-dealer Acme Securities (id 678)" in sys_text


def test_page_context_schema_accepts_payloads_with_and_without_entity_fields() -> None:
    """Backwards compatibility: old clients omit the entity fields; new
    clients send them from firm detail routes. Both shapes validate."""
    bare = ChatbotPageContext.model_validate(
        {"path": "/master-list", "title": "Broker Dealers"}
    )
    assert bare.entity_type is None
    assert bare.entity_id is None

    rich = ChatbotPageContext.model_validate(
        {
            "path": "/master-list/678",
            "title": "Acme Securities — DOX",
            "entity_type": "broker_dealer",
            "entity_id": 678,
        }
    )
    assert rich.entity_type == "broker_dealer"
    assert rich.entity_id == 678


@pytest.mark.asyncio
async def test_chat_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(GeminiConfigurationError):
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_raises_extraction_error_on_persistent_5xx(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_CHAT_URL).mock(
        return_value=httpx.Response(503, text="upstream unavailable")
    )
    with pytest.raises(GeminiExtractionError):
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )


@respx.mock
@pytest.mark.asyncio
async def test_chat_retries_transient_5xx_then_succeeds(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    route = respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(503, text="try again"),
            httpx.Response(200, json=_reply_payload("recovered")),
        ]
    )

    reply, _usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={},
    )

    assert reply == "recovered"
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_chat_raises_on_empty_safety_blocked_response(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
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
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )
    assert "SAFETY" in str(exc.value)


def test_function_declaration_omits_parameters_for_no_arg_tools() -> None:
    """Gemini 400s the WHOLE turn if a ``parameters`` object has empty
    ``properties``, so no-arg tools (e.g. list_vault_folders) must declare
    NO ``parameters`` field while tools with args keep theirs."""
    from app.services.chatbot import _function_declaration
    from app.services.chatbot_tools import TOOL_REGISTRY

    no_arg = _function_declaration(TOOL_REGISTRY["list_vault_folders"])
    assert no_arg["name"] == "list_vault_folders"
    assert "parameters" not in no_arg

    with_args = _function_declaration(TOOL_REGISTRY["ask_vault"])
    assert with_args["parameters"]["properties"]


def test_no_advertised_tool_emits_empty_properties() -> None:
    """Regression guard for every tool in the live registry: a declaration
    either omits ``parameters`` or carries a non-empty ``properties`` — never
    the empty-``properties`` object Gemini rejects."""
    from app.services.chatbot import _function_declaration
    from app.services.chatbot_tools import TOOL_REGISTRY

    for tool in TOOL_REGISTRY.values():
        decl = _function_declaration(tool)
        if "parameters" in decl:
            assert decl["parameters"].get("properties"), (
                f"{tool.name} declares parameters with empty properties — "
                "Gemini will 400 the whole chat turn"
            )


# ── Usage capture (token + tool counts on the returned ChatTurnUsage) ─────


@respx.mock
@pytest.mark.asyncio
async def test_chat_returns_usage_from_terminal_response_metadata(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """A plain (no-tool) turn returns the terminal response's token counts
    on the ChatTurnUsage, with zero tool calls and a non-negative latency."""
    respx.post(_GEMINI_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json=_reply_payload_with_usage(
                "Hi!", prompt=120, completion=45, total=165
            ),
        )
    )

    reply, usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Hello")],
        user=user,
        db=db_stub,
        tools={},
    )

    assert reply == "Hi!"
    assert isinstance(usage, ChatTurnUsage)
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 45
    assert usage.total_tokens == 165
    assert usage.tool_call_count == 0
    assert usage.latency_ms >= 0


@respx.mock
@pytest.mark.asyncio
async def test_chat_usage_tokens_none_when_metadata_absent(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """When Gemini omits usageMetadata, token fields stay None while tool
    count + latency remain valid (NULL-tolerant persistence path)."""
    respx.post(_GEMINI_CHAT_URL).mock(
        return_value=httpx.Response(200, json=_reply_payload("No metadata here."))
    )

    _reply, usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Hello")],
        user=user,
        db=db_stub,
        tools={},
    )

    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
    assert usage.total_tokens is None
    assert usage.tool_call_count == 0
    assert usage.latency_ms >= 0


def _function_call_payload(name: str, args: dict[str, Any]) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": name, "args": args}}],
                },
                "finishReason": "STOP",
            }
        ]
    }


def _usage_tool_stub(name: str, result: dict[str, Any]) -> Tool:
    async def execute(_user: Any, _db: Any, _args: dict[str, Any]) -> dict[str, Any]:
        return result

    return Tool(
        name=name,
        description=f"Stub {name}",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        feature_key="master_list",
        execute=execute,
    )


@respx.mock
@pytest.mark.asyncio
async def test_chat_usage_counts_one_tool_call(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """A turn that dispatches one tool before the terminal text reports
    ``tool_call_count == 1`` and reads tokens off the terminal response."""
    respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(200, json=_function_call_payload("lookup", {"id": 7})),
            httpx.Response(
                200,
                json=_reply_payload_with_usage(
                    "Done!", prompt=300, completion=80, total=380
                ),
            ),
        ]
    )

    reply, usage = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Tell me about firm 7")],
        user=user,
        db=db_stub,
        tools={"lookup": _usage_tool_stub("lookup", {"name": "Acme"})},
    )

    assert reply == "Done!"
    assert usage.tool_call_count == 1
    # Tokens come from the terminal (second) response.
    assert usage.total_tokens == 380
