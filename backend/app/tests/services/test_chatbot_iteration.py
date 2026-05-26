"""Tests for ``ChatbotService.chat``'s Gemini function-calling iteration loop.

Mocks the Gemini HTTP endpoint with respx and feeds canned multi-turn
responses (``functionCall`` → ... → text) to drive the loop end-to-end.
Tools are stubbed at the registry level so we exercise the loop's
plumbing — payload shape, role mapping, parts echoing, dispatch wiring,
safety brakes — without touching the real BD/IA repos.

Reuses the ``patch_gemini`` and ``no_backoff_sleep`` fixtures' pattern from
``test_chatbot.py`` but locally — copying is cheaper than splitting a
shared conftest for two modules.
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
from app.services.gemini_responses import GeminiExtractionError

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_CHAT_URL = f"{_GEMINI_BASE}/models/gemini-2.5-flash:generateContent"


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSy" + "x" * 33)
    monkeypatch.setattr(settings, "gemini_api_base", _GEMINI_BASE)
    monkeypatch.setattr(settings, "gemini_chat_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 2)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.chatbot.asyncio.sleep", _instant)


@pytest.fixture
def user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"iter-test-{secrets.token_hex(4)}",
        name="Iter Tester",
        email="iter-test@example.com",
        role="admin",
        feature_permissions=[],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture
def db_stub() -> object:
    return object()


# ── Tool stubs + helpers ────────────────────────────────────────────────


def _make_tool_stub(
    name: str,
    *,
    result: dict[str, Any] | None = None,
    raises: Exception | None = None,
    block_forever: bool = False,
) -> Tool:
    """Build a Tool whose ``execute`` returns / raises / blocks as configured.

    ``block_forever=True`` awaits an Event that is never set — used by the
    timeout test to ensure the per-tool ``wait_for`` brake fires. We can't
    use ``asyncio.sleep`` here because the ``no_backoff_sleep`` fixture
    monkey-patches the global ``asyncio.sleep``, which would make the
    stub return instantly and defeat the test.
    """

    async def execute(_user: Any, _db: Any, _args: dict[str, Any]) -> dict[str, Any]:
        if block_forever:
            await asyncio.Event().wait()  # cancelled by wait_for on timeout
        if raises is not None:
            raise raises
        return result or {"ok": True}

    return Tool(
        name=name,
        description=f"Stub tool {name}",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        feature_key="master_list",  # value doesn't matter, admin bypasses
        execute=execute,
    )


def _text_response(text: str = "All set!") -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ]
    }


def _function_call_response(
    *calls: tuple[str, dict[str, Any]],
    include_text: str | None = None,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if include_text:
        parts.append({"text": include_text})
    for name, args in calls:
        parts.append({"functionCall": {"name": name, "args": args}})
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts},
                "finishReason": "STOP",
            }
        ]
    }


# ── 0-tool parity ───────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_zero_tool_path_matches_phase1(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """tools={} must NOT add the `tools` field to the payload — preserves
    byte-identical wire shape with Phase 1."""
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_text_response("Hi!"))

    route = respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Hello")],
        user=user,
        db=db_stub,
        tools={},
    )
    assert reply == "Hi!"
    assert route.call_count == 1
    body = json.loads(captured["body"])
    assert "tools" not in body


# ── 1-tool happy path ───────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_one_tool_round_trip_echoes_parts_and_appends_function_response(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        # Turn 1: model asks for a tool call. Turn 2: model returns text.
        if len(bodies) == 1:
            return httpx.Response(
                200, json=_function_call_response(("lookup", {"id": 7}))
            )
        return httpx.Response(200, json=_text_response("Done!"))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    tool = _make_tool_stub("lookup", result={"name": "Acme"})
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Tell me about firm 7")],
        user=user,
        db=db_stub,
        tools={"lookup": tool},
    )

    assert reply == "Done!"
    assert len(bodies) == 2
    second = json.loads(bodies[1])
    # The model turn must echo the functionCall part verbatim.
    model_turn = second["contents"][-2]
    assert model_turn["role"] == "model"
    assert model_turn["parts"] == [{"functionCall": {"name": "lookup", "args": {"id": 7}}}]
    # The user turn must contain the functionResponse with our stub's return.
    user_turn = second["contents"][-1]
    assert user_turn["role"] == "user"
    assert user_turn["parts"] == [
        {"functionResponse": {"name": "lookup", "response": {"name": "Acme"}}}
    ]
    # First turn's payload should have the `tools` field set.
    first = json.loads(bodies[0])
    decls = first["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in decls] == ["lookup"]


# ── 2-tool sequential ───────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_two_sequential_tool_calls_make_three_posts(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    route = respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(200, json=_function_call_response(("search", {"q": "Acme"}))),
            httpx.Response(200, json=_function_call_response(("profile", {"id": 1}))),
            httpx.Response(200, json=_text_response("Net capital is $1.5M.")),
        ]
    )

    tools = {
        "search": _make_tool_stub("search", result={"items": [{"id": 1, "name": "Acme"}]}),
        "profile": _make_tool_stub("profile", result={"id": 1, "net_capital": 1500000}),
    }
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Find Acme and tell me its net capital.")],
        user=user,
        db=db_stub,
        tools=tools,
    )
    assert reply == "Net capital is $1.5M."
    assert route.call_count == 3


# ── Parallel functionCalls in one response ──────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_parallel_function_calls_one_user_turn_two_responses(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json=_function_call_response(
                    ("lookup_a", {"x": 1}), ("lookup_b", {"x": 2})
                ),
            )
        return httpx.Response(200, json=_text_response("Both done."))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    tools = {
        "lookup_a": _make_tool_stub("lookup_a", result={"a": 1}),
        "lookup_b": _make_tool_stub("lookup_b", result={"b": 2}),
    }
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Look up both.")],
        user=user,
        db=db_stub,
        tools=tools,
    )
    assert reply == "Both done."
    assert len(bodies) == 2
    second = json.loads(bodies[1])
    # Both functionResponse parts must be packed into the single user turn
    # that follows the model turn — not split across two turns.
    user_parts = second["contents"][-1]["parts"]
    assert len(user_parts) == 2
    names = sorted(p["functionResponse"]["name"] for p in user_parts)
    assert names == ["lookup_a", "lookup_b"]


# ── Mixed text + functionCall ───────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_mixed_text_and_function_call_continues_loop(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """If the model emits text alongside a functionCall, the loop should
    treat the call as the signal to continue — pre-emitted text is
    discarded so it doesn't get rendered alongside the final answer."""
    route = respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_function_call_response(
                    ("lookup", {"id": 1}), include_text="Let me look that up…"
                ),
            ),
            httpx.Response(200, json=_text_response("Found it: Acme.")),
        ]
    )
    tool = _make_tool_stub("lookup", result={"name": "Acme"})
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="What is firm 1?")],
        user=user,
        db=db_stub,
        tools={"lookup": tool},
    )
    assert reply == "Found it: Acme."
    assert route.call_count == 2


# ── Max iterations brake ────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_max_iterations_returns_fallback_without_raising(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    """Six consecutive functionCall responses must exit the loop cleanly
    with the final response's text (or a fallback), never raising."""
    responses = [
        httpx.Response(200, json=_function_call_response(("lookup", {"i": i})))
        for i in range(chatbot_module.MAX_TOOL_ITERATIONS + 1)
    ]
    respx.post(_GEMINI_CHAT_URL).mock(side_effect=responses)

    tool = _make_tool_stub("lookup", result={"ok": True})
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Loop forever")],
        user=user,
        db=db_stub,
        tools={"lookup": tool},
    )
    # Last response had no text, so the fallback string should land.
    assert "rephrase" in reply.lower() or "couldn't" in reply.lower() or "wasn't" in reply.lower()


# ── Tool returns error dict ─────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_tool_error_dict_is_forwarded_to_gemini(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                200, json=_function_call_response(("lookup", {"id": 1}))
            )
        return httpx.Response(200, json=_text_response("Sorry, can't help with that."))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    tool = _make_tool_stub("lookup", result={"error": "no_access", "message": "denied"})
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="Try anyway")],
        user=user,
        db=db_stub,
        tools={"lookup": tool},
    )
    assert reply == "Sorry, can't help with that."
    second = json.loads(bodies[1])
    fr = second["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["response"] == {"error": "no_access", "message": "denied"}


# ── Tool timeout ────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_tool_timeout_surfaces_timeout_error_to_gemini(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Crank the per-tool timeout down so the test runs in milliseconds.
    monkeypatch.setattr(chatbot_module, "TOOL_EXECUTION_TIMEOUT_S", 0.05)

    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                200, json=_function_call_response(("slow", {}))
            )
        return httpx.Response(200, json=_text_response("Apologies, it timed out."))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    # Tool blocks forever; wait_for must cancel it after the timeout.
    slow_tool = _make_tool_stub("slow", block_forever=True)
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={"slow": slow_tool},
    )
    assert reply == "Apologies, it timed out."
    second = json.loads(bodies[1])
    fr = second["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["response"]["error"] == "timeout"


# ── Tool that raises ────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_tool_that_raises_returns_tool_error_to_gemini(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                200, json=_function_call_response(("boom", {}))
            )
        return httpx.Response(200, json=_text_response("Something went wrong."))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    bad_tool = _make_tool_stub("boom", raises=RuntimeError("kaboom"))
    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={"boom": bad_tool},
    )
    assert reply == "Something went wrong."
    second = json.loads(bodies[1])
    fr = second["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["response"]["error"] == "tool_error"


# ── Unknown tool name ───────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_unknown_tool_returns_unknown_tool_error(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    bodies: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                200, json=_function_call_response(("does_not_exist", {}))
            )
        return httpx.Response(200, json=_text_response("Can't do that."))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    reply = await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={"existing": _make_tool_stub("existing")},  # registry doesn't have "does_not_exist"
    )
    assert reply == "Can't do that."
    second = json.loads(bodies[1])
    fr = second["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["response"]["error"] == "unknown_tool"


# ── Wall-clock budget ───────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_wall_clock_budget_raises_extraction_error(
    patch_gemini: None,
    no_backoff_sleep: None,
    user: AuthenticatedUser,
    db_stub: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock monotonic so the second iteration appears to be past the deadline."""
    # First call returns a functionCall so we get into iteration 1.
    # On iteration 1's top-of-loop check, monotonic appears to have jumped
    # past the deadline → loop raises.
    respx.post(_GEMINI_CHAT_URL).mock(
        side_effect=[
            httpx.Response(200, json=_function_call_response(("lookup", {}))),
            httpx.Response(200, json=_text_response("never reached")),
        ]
    )

    base = [1000.0]
    call_count = [0]

    def fake_monotonic() -> float:
        # First two calls: now=1000 (deadline_set + first-iter check).
        # Third+: jump past the budget so the next iteration aborts.
        call_count[0] += 1
        if call_count[0] >= 3:
            return base[0] + chatbot_module.CHAT_WALL_CLOCK_BUDGET_S + 1
        return base[0]

    monkeypatch.setattr(chatbot_module, "monotonic", fake_monotonic)

    tool = _make_tool_stub("lookup", result={"ok": True})
    with pytest.raises(GeminiExtractionError) as exc:
        await ChatbotService().chat(
            messages=[ChatbotMessage(role="user", content="hi")],
            user=user,
            db=db_stub,
            tools={"lookup": tool},
        )
    assert "wall-clock" in str(exc.value).lower() or "budget" in str(exc.value).lower()


# ── System prompt + tools field ─────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_tool_usage_prompt_appended_when_tools_present(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_text_response("hi"))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={"x": _make_tool_stub("x")},
    )
    body = json.loads(captured["body"])
    system_text = body["systemInstruction"]["parts"][0]["text"]
    # The tool-usage guidance should be present.
    assert "tools" in system_text.lower()
    assert "never invent ids" in system_text.lower()
    # And the payload exposes the declarations.
    assert "x" == body["tools"][0]["functionDeclarations"][0]["name"]


@respx.mock
@pytest.mark.asyncio
async def test_tool_usage_prompt_absent_when_no_tools(
    patch_gemini: None,
    user: AuthenticatedUser,
    db_stub: object,
) -> None:
    captured: dict[str, bytes] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_text_response("hi"))

    respx.post(_GEMINI_CHAT_URL).mock(side_effect=capture)

    await ChatbotService().chat(
        messages=[ChatbotMessage(role="user", content="hi")],
        user=user,
        db=db_stub,
        tools={},
    )
    body = json.loads(captured["body"])
    system_text = body["systemInstruction"]["parts"][0]["text"]
    assert "never invent ids" not in system_text.lower()
    assert "tools" not in body
