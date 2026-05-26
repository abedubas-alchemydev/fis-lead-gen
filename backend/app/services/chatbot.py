"""Doxie chatbot service — free-form Gemini chat with optional tool calling.

Phase 1 shipped a stateless ``generateContent`` wrapper with persona + page
context. Phase 2 (this revision) layers in **Gemini function calling**: when
``tools`` is non-empty, the response can contain ``functionCall`` parts that
the service executes against the registry and re-submits as
``functionResponse`` parts, looping until Gemini returns plain text or a
safety brake trips.

Backwards-compatible: passing ``tools={}`` (or omitting it in callers that
upgraded their signature) is byte-for-byte equivalent to the Phase 1
single-shot path — the existing tests in ``test_chatbot.py`` still pass
without modification beyond the new mandatory ``user`` / ``db`` kwargs.

Safety brakes (all module constants below):
- ``MAX_TOOL_ITERATIONS`` — hard cap on the number of round-trips. Once
  hit, the loop returns whatever text Gemini emitted in its final response
  (or a fallback string) and stops.
- ``TOOL_EXECUTION_TIMEOUT_S`` — per-tool execution timeout. Each tool's
  ``execute`` is wrapped in ``asyncio.wait_for``; timeouts surface back to
  Gemini as a structured ``{"error": "timeout"}`` payload so the model can
  apologize gracefully instead of the chat 502'ing.
- ``CHAT_WALL_CLOCK_BUDGET_S`` — total budget across the whole chat call.
  Prevents pathological ``many-rounds × many-tools`` chats from approaching
  Cloud Run's 60-min request ceiling. Mapped to 502 by the endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping, Sequence

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.auth import AuthenticatedUser
from app.schemas.chatbot import ChatbotMessage, ChatbotPageContext
from app.services.chatbot_tools import TOOL_REGISTRY, Tool
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)

# Doxie persona — kept short so most of the model's prompt budget stays
# available for the user's conversation history. The "use tools" guidance
# is appended only when tools are actually passed (see ``_build_system_text``)
# so the Phase 1 zero-tool path emits the same system prompt as before.
DOXIE_SYSTEM_PROMPT = (
    "You are Doxie, the in-app assistant for the Alchemy broker-dealer "
    "and investment-advisor intelligence platform. Help users navigate "
    "the app, understand financial regulatory data (Form BD, Form ADV, "
    "FOCUS reports, clearing relationships), and draft outreach. Be "
    "concise, friendly, and direct. If a question is outside the app's "
    "scope, answer briefly without speculating about firm data you "
    "have not been shown. Do not invent numbers, names, or filings."
)

DOXIE_TOOL_USAGE_PROMPT = (
    "When the user asks about a specific firm by name, CRD, or CIK, use "
    "the available tools to look it up rather than guessing. Prefer "
    "search tools first to disambiguate, then call the profile tool on "
    "the chosen numeric id. Never invent ids. If a tool returns an error "
    "object (e.g. no_access, not_found), relay that meaning to the user "
    "in plain language instead of re-trying the same call."
)


# Safety brakes — see module docstring for rationale.
MAX_TOOL_ITERATIONS = 5
TOOL_EXECUTION_TIMEOUT_S = 5.0
CHAT_WALL_CLOCK_BUDGET_S = 60.0


# ─── Tool result cache ────────────────────────────────────────────────────
#
# Per-process LRU keyed by ``(tool_name, args_json, user_id)`` with a short
# TTL. The point isn't multi-request caching (Cloud Run revisions cycle
# faster than the TTL on most days) — it's deduplicating the redundant
# calls Gemini sometimes makes within a single chat (e.g. asking for
# ``search_broker_dealers(query="Apex")`` twice in two iterations after
# being prompted to verify the same firm), and shielding the DB from
# obvious within-conversation thrash.
#
# Why the user_id is in the key: ``ensure_feature`` runs inside each tool's
# ``execute``, so two users with different permissions calling the same
# tool with the same args could see different outcomes (real data vs.
# ``no_access`` refusal). Keying on user.id keeps that distinction safe
# without lifting the permission check out of the tool layer.
#
# Why error results are skipped: a transient ``tool_error`` (DB hiccup,
# upstream timeout) shouldn't poison the cache for 60 seconds. The
# ``no_access`` refusal is technically deterministic per (user, feature)
# but treating all errors uniformly keeps the cache rule simple — re-runs
# are cheap once the tool actually succeeds.
TOOL_CACHE_TTL_S = 60.0
TOOL_CACHE_MAX_ENTRIES = 256

_TOOL_CACHE: "OrderedDict[tuple[str, str, str], tuple[dict[str, Any], float]]" = (
    OrderedDict()
)
_TOOL_CACHE_LOCK = asyncio.Lock()


def _tool_cache_key(
    tool_name: str, args: Mapping[str, Any], user_id: str
) -> tuple[str, str, str]:
    """Stable cache key — JSON-encodes args with sorted keys for determinism."""
    try:
        args_json = json.dumps(args, sort_keys=True, default=str)
    except TypeError:
        # If args contain something exotic that doesn't JSON-encode, skip
        # the cache by returning a unique key that will never hit.
        args_json = f"__unhashable__:{id(args)}"
    return (tool_name, args_json, user_id)


async def _tool_cache_get(
    tool_name: str, args: Mapping[str, Any], user_id: str
) -> dict[str, Any] | None:
    """Return the cached result if present and unexpired."""
    key = _tool_cache_key(tool_name, args, user_id)
    async with _TOOL_CACHE_LOCK:
        hit = _TOOL_CACHE.get(key)
        if hit is None:
            return None
        result, expires_at = hit
        if monotonic() >= expires_at:
            _TOOL_CACHE.pop(key, None)
            return None
        _TOOL_CACHE.move_to_end(key)
        # Return a shallow copy so a downstream consumer mutating the dict
        # can't poison the cached entry for the next caller.
        return dict(result)


async def _tool_cache_put(
    tool_name: str, args: Mapping[str, Any], user_id: str, result: dict[str, Any]
) -> None:
    """Insert/refresh a cache entry. Errors are not cached."""
    if "error" in result:
        return
    key = _tool_cache_key(tool_name, args, user_id)
    expires_at = monotonic() + TOOL_CACHE_TTL_S
    async with _TOOL_CACHE_LOCK:
        _TOOL_CACHE[key] = (dict(result), expires_at)
        _TOOL_CACHE.move_to_end(key)
        while len(_TOOL_CACHE) > TOOL_CACHE_MAX_ENTRIES:
            _TOOL_CACHE.popitem(last=False)


def _tool_cache_clear_for_tests() -> None:
    """Test-only helper. Sync because most tests run inside event loops and
    don't need lock acquisition for setup/teardown isolation."""
    _TOOL_CACHE.clear()


@dataclass(frozen=True)
class _FunctionCall:
    """One ``functionCall`` part extracted from a Gemini response."""

    name: str
    args: dict[str, Any]


class ChatbotService:
    """Stateless wrapper around Gemini's ``generateContent`` with tools."""

    def __init__(self) -> None:
        self.base_url = settings.gemini_api_base.rstrip("/")
        self.timeout = settings.gemini_request_timeout_seconds
        self.max_retries = max(1, settings.gemini_request_max_retries)

    async def chat(
        self,
        *,
        messages: Sequence[ChatbotMessage],
        user: AuthenticatedUser,
        db: AsyncSession,
        page_context: ChatbotPageContext | None = None,
        tools: Mapping[str, Tool] | None = None,
    ) -> str:
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        if not messages:
            raise GeminiExtractionError("messages must contain at least one entry.")

        # ``tools=None`` defaults to the production registry; ``tools={}``
        # explicitly opts out (used by Phase 1 parity tests).
        active_tools: Mapping[str, Tool] = (
            TOOL_REGISTRY if tools is None else tools
        )

        contents = self._build_contents(messages)
        deadline = monotonic() + CHAT_WALL_CLOCK_BUDGET_S

        for iteration in range(MAX_TOOL_ITERATIONS + 1):
            if monotonic() > deadline:
                logger.warning(
                    "doxie chat exceeded wall-clock budget user_id=%s "
                    "iterations_used=%d",
                    user.id,
                    iteration,
                )
                raise GeminiExtractionError(
                    "Chat exceeded the wall-clock budget; aborting."
                )

            payload = self._build_payload(
                contents=contents,
                page_context=page_context,
                tools=active_tools,
            )
            response_payload = await self._post_with_retries(payload)
            function_calls, model_parts = self._extract_function_calls_and_parts(
                response_payload
            )

            # Terminal: no tool calls. Either we got our final text or we hit
            # the iteration cap and surface whatever the model said last.
            if not function_calls:
                return self._extract_text(response_payload)
            if iteration == MAX_TOOL_ITERATIONS:
                logger.warning(
                    "doxie chat max tool iterations hit user_id=%s",
                    user.id,
                )
                # Try to surface any final text; fall back to a friendly
                # message rather than raising into a 502.
                try:
                    return self._extract_text(response_payload)
                except GeminiExtractionError:
                    return (
                        "I wasn't able to finalize that lookup. "
                        "Could you rephrase the question?"
                    )

            # Echo the model's turn verbatim — Gemini requires the original
            # functionCall parts in the conversation, not reconstructed ones.
            contents.append({"role": "model", "parts": model_parts})

            response_parts: list[dict[str, Any]] = []
            for call in function_calls:
                logger.info(
                    "doxie tool dispatch user_id=%s tool=%s",
                    user.id,
                    call.name,
                )
                result = await self._dispatch_tool(
                    call, tools=active_tools, user=user, db=db
                )
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": call.name,
                            "response": result,
                        }
                    }
                )
            contents.append({"role": "user", "parts": response_parts})

        # Unreachable: the loop above either returns or raises. Defensive
        # raise so a future refactor that breaks the invariant fails loud.
        raise GeminiExtractionError("Tool iteration loop exited unexpectedly.")

    @staticmethod
    def _build_contents(
        messages: Sequence[ChatbotMessage],
    ) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in messages:
            # Gemini calls the assistant role "model"; the FE emits the
            # OpenAI-style "assistant" label that the rest of the codebase
            # already uses, so map at the wire boundary.
            role = "model" if message.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})
        return contents

    def _build_payload(
        self,
        *,
        contents: list[dict[str, Any]],
        page_context: ChatbotPageContext | None,
        tools: Mapping[str, Tool],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": self._build_system_text(page_context, tools)}]
            },
            "contents": contents,
            "generationConfig": {
                "temperature": settings.gemini_chat_temperature,
                "maxOutputTokens": settings.gemini_chat_max_output_tokens,
            },
        }
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters_schema,
                        }
                        for tool in tools.values()
                    ]
                }
            ]
        return payload

    @classmethod
    def _build_system_text(
        cls,
        page_context: ChatbotPageContext | None,
        tools: Mapping[str, Tool],
    ) -> str:
        text = DOXIE_SYSTEM_PROMPT
        if tools:
            text = f"{text}\n\n{DOXIE_TOOL_USAGE_PROMPT}"
        context_block = cls._format_page_context(page_context)
        if context_block:
            text = f"{text}\n\n{context_block}"
        return text

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

    async def _dispatch_tool(
        self,
        call: _FunctionCall,
        *,
        tools: Mapping[str, Tool],
        user: AuthenticatedUser,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Execute one tool with cache check + per-call timeout + failure guard.

        Cache lookup is keyed on ``(tool_name, args, user_id)`` so a viewer
        with feature X gets their own slot in the cache; a different user
        with the same args might see different output (auth gate). Errors
        are never cached so transient failures self-heal on the next call.

        Tools that raise unexpected exceptions are caught here so the
        iteration loop can carry on and the model can apologize gracefully.
        Returning an error dict preserves the per-call audit trail (each
        ``functionResponse`` reflects what actually happened).
        """
        tool = tools.get(call.name)
        if tool is None:
            return {
                "error": "unknown_tool",
                "message": (
                    f"No tool named {call.name!r} is registered. Do not call "
                    f"this name again."
                ),
            }

        cached = await _tool_cache_get(call.name, call.args, user.id)
        if cached is not None:
            logger.info(
                "doxie tool cache hit user_id=%s tool=%s",
                user.id,
                call.name,
            )
            return cached

        try:
            result = await asyncio.wait_for(
                tool.execute(user, db, call.args),
                timeout=TOOL_EXECUTION_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "doxie tool timeout user_id=%s tool=%s", user.id, call.name
            )
            return {
                "error": "timeout",
                "message": (
                    "The lookup took too long. Ask the user if they want "
                    "to retry."
                ),
            }
        except Exception:
            logger.exception(
                "doxie tool dispatch failed user_id=%s tool=%s",
                user.id,
                call.name,
            )
            return {
                "error": "tool_error",
                "message": (
                    "An internal error occurred running the tool. Apologize "
                    "and ask the user to try again."
                ),
            }

        await _tool_cache_put(call.name, call.args, user.id, result)
        return result

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
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
    def _extract_function_calls_and_parts(
        payload: dict[str, Any],
    ) -> tuple[list[_FunctionCall], list[dict[str, Any]]]:
        """Pull functionCall parts out of a response.

        Returns ``(calls, parts)`` where ``parts`` is the *entire* parts list
        from the model's turn (text + functionCalls intermixed). The full
        list must be echoed back to Gemini verbatim in the next turn —
        partial echoes ("just the functionCall parts") trigger schema errors
        from the API.

        A response with mixed text + functionCall is treated as a tool call
        (loop continues); the pre-emitted text is discarded because it would
        be confusing to render alongside the eventual final answer.
        """
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return [], []
        first = candidates[0]
        if not isinstance(first, dict):
            return [], []
        content = first.get("content", {})
        if not isinstance(content, dict):
            return [], []
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            return [], []
        calls: list[_FunctionCall] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall")
            if isinstance(fc, dict):
                name = fc.get("name")
                args = fc.get("args") or {}
                if isinstance(name, str) and isinstance(args, dict):
                    calls.append(_FunctionCall(name=name, args=args))
        return calls, list(parts)

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
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
