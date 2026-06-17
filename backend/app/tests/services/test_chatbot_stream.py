"""Tests for ``ChatbotService.chat_stream`` (Gemini SSE function-calling loop).

Mocks Gemini's :streamGenerateContent endpoint with respx, returning
canned SSE bytes that the service parses into events. The tests focus on
the event sequence the FE consumes (text_delta / tool_call / tool_result
/ done / error) rather than the underlying Gemini wire shape.

Tools are stubbed at the registry level (same pattern as
``test_chatbot_iteration.py``) so the dispatch path doesn't touch the
real BD/IA repos. The tool-result cache is cleared per-test via the
autouse fixture.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from app.core.config import settings
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import ChatbotMessage
from app.services import chatbot as chatbot_module
from app.services.chatbot import ChatbotService
from app.services.chatbot_tools import Tool

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_STREAM_URL = (
    f"{_GEMINI_BASE}/models/gemini-2.5-flash:streamGenerateContent"
)


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSy" + "x" * 33)
    monkeypatch.setattr(settings, "gemini_api_base", _GEMINI_BASE)
    monkeypatch.setattr(settings, "gemini_chat_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 2)


@pytest.fixture(autouse=True)
def _clear_tool_cache() -> object:
    chatbot_module._tool_cache_clear_for_tests()
    yield
    chatbot_module._tool_cache_clear_for_tests()


@pytest.fixture
def user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"stream-test-{secrets.token_hex(4)}",
        name="Stream Tester",
        email="stream-test@example.com",
        role="admin",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture
def db_stub() -> object:
    return object()


# ── Tool stub + SSE body helpers ────────────────────────────────────────


def _make_tool_stub(
    name: str,
    *,
    result: dict[str, Any] | None = None,
) -> Tool:
    async def execute(_user: Any, _db: Any, _args: dict[str, Any]) -> dict[str, Any]:
        return result or {"ok": True}

    return Tool(
        name=name,
        description=f"Stub tool {name}",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        feature_key="master_list",
        execute=execute,
    )


def _gemini_chunk_text(text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
            }
        ]
    }


def _gemini_chunk_function_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": name, "args": args}}],
                }
            }
        ]
    }


def _sse_bytes(chunks: list[dict[str, Any]]) -> bytes:
    """Build a Gemini-style SSE body from a list of candidate dicts."""
    out = b""
    for chunk in chunks:
        out += b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
    return out


async def _collect(stream: Any) -> list[dict[str, Any]]:
    """Drain an async generator into a list."""
    events: list[dict[str, Any]] = []
    async for ev in stream:
        events.append(ev)
    return events


# ── Single-iteration text response ──────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_streams_text_deltas_then_done_for_terminal_iteration(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        return_value=httpx.Response(
            200,
            content=_sse_bytes(
                [
                    _gemini_chunk_text("Hello"),
                    _gemini_chunk_text(", "),
                    _gemini_chunk_text("Doxie here."),
                ]
            ),
        )
    )

    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )
    )

    # 3 text_delta events in order + 1 done with the concatenated reply.
    types = [e["type"] for e in events]
    assert types == ["text_delta", "text_delta", "text_delta", "done"]
    assert [e["text"] for e in events[:3]] == ["Hello", ", ", "Doxie here."]
    assert events[-1]["reply"] == "Hello, Doxie here."


# ── Single-tool-call iteration ──────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_emits_tool_call_then_tool_result_then_done(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            # Iteration 0: model calls one tool, no text.
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_function_call("lookup", {"id": 7})]
                ),
            ),
            # Iteration 1: model streams the final answer.
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_text("Found "), _gemini_chunk_text("it.")]
                ),
            ),
        ]
    )

    tool = _make_tool_stub("lookup", result={"name": "Acme"})
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="who is firm 7?")],
            user=user,
            db=db_stub,
            tools={"lookup": tool},
        )
    )

    types = [e["type"] for e in events]
    assert types == [
        "tool_call",
        "tool_result",
        "text_delta",
        "text_delta",
        "done",
    ]
    assert events[0] == {"type": "tool_call", "name": "lookup"}
    assert events[1] == {"type": "tool_result", "name": "lookup", "error": None}
    assert events[-1]["reply"] == "Found it."


# ── Multi-tool sequential ───────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_two_sequential_tool_calls_emit_two_pairs(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_function_call("search", {"q": "Acme"})]
                ),
            ),
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_function_call("profile", {"id": 1})]
                ),
            ),
            httpx.Response(
                200,
                content=_sse_bytes([_gemini_chunk_text("All done.")]),
            ),
        ]
    )

    tools = {
        "search": _make_tool_stub("search", result={"items": [{"id": 1}]}),
        "profile": _make_tool_stub("profile", result={"id": 1}),
    }
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="…")],
            user=user,
            db=db_stub,
            tools=tools,
        )
    )

    types = [e["type"] for e in events]
    assert types == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "text_delta",
        "done",
    ]
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["search", "profile"]


# ── Error: tool returns an error dict, FE sees ``error`` field on result ─


@respx.mock
@pytest.mark.asyncio
async def test_tool_result_carries_error_field(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_function_call("lookup", {})]
                ),
            ),
            httpx.Response(
                200,
                content=_sse_bytes([_gemini_chunk_text("Sorry, you can't.")]),
            ),
        ]
    )

    tool = _make_tool_stub(
        "lookup", result={"error": "no_access", "message": "denied"}
    )
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="…")],
            user=user,
            db=db_stub,
            tools={"lookup": tool},
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["name"] == "lookup"
    assert tool_result["error"] == "no_access"


# ── Whitelisted outreach-draft tools mirror their result payload ────────
#
# The FE renders an interactive draft card from the ``result`` field on
# ``tool_result`` events — emitted ONLY for the tools in
# ``STREAM_RESULT_TOOLS``, only on success, and only under the size cap.


def _mock_one_tool_call_then_text(name: str, args: dict[str, Any]) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                content=_sse_bytes([_gemini_chunk_function_call(name, args)]),
            ),
            httpx.Response(
                200,
                content=_sse_bytes([_gemini_chunk_text("Draft ready.")]),
            ),
        ]
    )


@respx.mock
@pytest.mark.asyncio
async def test_whitelisted_draft_tool_result_payload_is_mirrored(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    _mock_one_tool_call_then_text("save_outreach_draft", {})

    draft_result = {
        "draft_id": 41,
        "subject": "Clearing services intro",
        "to": "cfo@acme.example",
        "folder_id": None,
        "link": "/outreach/sent?tab=drafts",
        "message": "Saved.",
    }
    tool = _make_tool_stub("save_outreach_draft", result=draft_result)
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="save it")],
            user=user,
            db=db_stub,
            tools={"save_outreach_draft": tool},
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["name"] == "save_outreach_draft"
    assert tool_result["error"] is None
    assert tool_result["result"] == draft_result


@respx.mock
@pytest.mark.asyncio
async def test_non_whitelisted_tool_result_payload_is_not_mirrored(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """Every tool outside STREAM_RESULT_TOOLS keeps the lean event shape —
    its data must never leave the server on the SSE stream."""
    _mock_one_tool_call_then_text("search_broker_dealers", {"q": "Acme"})

    tool = _make_tool_stub(
        "search_broker_dealers", result={"items": [{"id": 1, "name": "Acme"}]}
    )
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="find acme")],
            user=user,
            db=db_stub,
            tools={"search_broker_dealers": tool},
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result == {
        "type": "tool_result",
        "name": "search_broker_dealers",
        "error": None,
    }


@respx.mock
@pytest.mark.asyncio
async def test_whitelisted_tool_error_result_is_not_mirrored(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """An error dict carries no draft fields — the event keeps only the
    error code, same as before."""
    _mock_one_tool_call_then_text("get_outreach_draft", {"draft_id": 9})

    tool = _make_tool_stub(
        "get_outreach_draft",
        result={"error": "not_found", "message": "No draft with that id."},
    )
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="open draft 9")],
            user=user,
            db=db_stub,
            tools={"get_outreach_draft": tool},
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["error"] == "not_found"
    assert "result" not in tool_result


@respx.mock
@pytest.mark.asyncio
async def test_oversized_whitelisted_result_is_dropped_not_truncated(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    _mock_one_tool_call_then_text("get_outreach_draft", {"draft_id": 7})

    oversized = {
        "draft_id": 7,
        "subject": "big",
        "body": "x" * (chatbot_module.STREAM_RESULT_MAX_CHARS + 1),
    }
    tool = _make_tool_stub("get_outreach_draft", result=oversized)
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="open draft 7")],
            user=user,
            db=db_stub,
            tools={"get_outreach_draft": tool},
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["error"] is None
    assert "result" not in tool_result


def test_stream_tool_result_payload_helper_rules() -> None:
    """Unit coverage for the whitelist/size/serializability gate itself."""
    helper = chatbot_module._stream_tool_result_payload

    ok = {"draft_id": 3, "subject": "hi", "body": "text"}
    # All three outreach-draft tools pass through; the payload is a copy.
    for name in chatbot_module.STREAM_RESULT_TOOLS:
        mirrored = helper(name, ok)
        assert mirrored == ok
        assert mirrored is not ok
    assert chatbot_module.STREAM_RESULT_TOOLS == {
        "draft_outreach_email",
        "save_outreach_draft",
        "get_outreach_draft",
    }

    # Any other tool — including the send tool — never mirrors.
    assert helper("send_outreach_draft", ok) is None
    assert helper("search_broker_dealers", ok) is None

    # Error results, oversized results, and unserializable results drop.
    assert helper("get_outreach_draft", {"error": "not_found"}) is None
    assert (
        helper(
            "get_outreach_draft",
            {"body": "x" * (chatbot_module.STREAM_RESULT_MAX_CHARS + 1)},
        )
        is None
    )
    assert helper("draft_outreach_email", {"body": object()}) is None


# ── Error: missing API key ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_api_key_emits_config_error(
    monkeypatch: pytest.MonkeyPatch,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )
    )
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "config"


# ── Error: Gemini returns 5xx ───────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_gemini_5xx_emits_extraction_error_then_returns(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        return_value=httpx.Response(503, text="upstream unavailable")
    )

    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )
    )

    # Only one event: the error. Generator returns immediately after.
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "extraction"
    assert "503" in events[0]["message"]


# ── Wall-clock budget ───────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_wall_clock_budget_emits_error_event(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                200, content=_sse_bytes([_gemini_chunk_function_call("lookup", {})])
            ),
            httpx.Response(200, content=_sse_bytes([_gemini_chunk_text("never")])),
        ]
    )

    call_count = [0]

    def fake_monotonic() -> float:
        call_count[0] += 1
        # 3rd call (top of iteration 1) appears past the deadline.
        if call_count[0] >= 3:
            return 10_000.0 + chatbot_module.CHAT_WALL_CLOCK_BUDGET_S + 1
        return 10_000.0

    monkeypatch.setattr(chatbot_module, "monotonic", fake_monotonic)

    tool = _make_tool_stub("lookup", result={"ok": True})
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="…")],
            user=user,
            db=db_stub,
            tools={"lookup": tool},
        )
    )
    # The first iteration emits tool_call + tool_result, then the deadline
    # check at the top of iteration 1 kicks in.
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "extraction"
    assert "wall-clock" in events[-1]["message"].lower() or "budget" in events[-1]["message"].lower()


# ── Max iterations ──────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_max_iterations_emits_fallback_done(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    # MAX_TOOL_ITERATIONS + 1 consecutive functionCall responses (one over
    # the cap).
    respx.post(_GEMINI_STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                content=_sse_bytes(
                    [_gemini_chunk_function_call("lookup", {"i": i})]
                ),
            )
            for i in range(chatbot_module.MAX_TOOL_ITERATIONS + 1)
        ]
    )

    tool = _make_tool_stub("lookup", result={"ok": True})
    events = await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="loop forever")],
            user=user,
            db=db_stub,
            tools={"lookup": tool},
        )
    )

    # Must end cleanly with a done — never raise.
    assert events[-1]["type"] == "done"
    # The fallback message lands as both a text_delta and the done reply.
    assert "rephrase" in events[-1]["reply"].lower() or "couldn't" in events[-1]["reply"].lower() or "wasn't" in events[-1]["reply"].lower()


# ── SSE wire shape ──────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_streaming_request_hits_streamgeneratecontent_endpoint(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """The wire path must be :streamGenerateContent (with alt=sse), not the
    non-streaming :generateContent endpoint."""
    captured: dict[str, Any] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=_sse_bytes([_gemini_chunk_text("ok")]))

    respx.post(_GEMINI_STREAM_URL).mock(side_effect=capture)

    await _collect(
        ChatbotService().chat_stream(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={},
        )
    )
    assert ":streamGenerateContent" in captured["url"]
    assert "alt=sse" in captured["url"]


# Avoid unused-import lint when no test uses asyncio directly.
_ = asyncio
