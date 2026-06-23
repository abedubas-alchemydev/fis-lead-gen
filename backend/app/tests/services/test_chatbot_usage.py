"""Unit tests for the Doxie usage-metadata extraction helpers.

Covers ``ChatbotService._extract_usage_metadata`` (the raw usageMetadata
parser) and ``_build_turn_usage`` (the ChatTurnUsage assembler). Pure
functions over a payload dict — no network, no DB, no monkeypatching of
``settings``.
"""

from __future__ import annotations

from app.schemas.chatbot import ChatTurnUsage
from app.services.chatbot import ChatbotService


def test_extract_usage_metadata_reads_all_three_counts() -> None:
    payload = {
        "usageMetadata": {
            "promptTokenCount": 120,
            "candidatesTokenCount": 45,
            "totalTokenCount": 165,
        }
    }
    assert ChatbotService._extract_usage_metadata(payload) == (120, 45, 165)


def test_extract_usage_metadata_returns_none_when_block_absent() -> None:
    # A response with no usageMetadata at all (Gemini omits it sometimes,
    # especially on the terminal streaming chunk).
    assert ChatbotService._extract_usage_metadata({}) == (None, None, None)


def test_extract_usage_metadata_returns_none_for_empty_block() -> None:
    assert ChatbotService._extract_usage_metadata({"usageMetadata": {}}) == (
        None,
        None,
        None,
    )


def test_extract_usage_metadata_partial_fields_default_to_none() -> None:
    # Only prompt + total reported (no candidates) — the missing field is
    # NULL, not coerced to 0.
    payload = {
        "usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 100}
    }
    assert ChatbotService._extract_usage_metadata(payload) == (100, None, 100)


def test_extract_usage_metadata_ignores_non_integer_and_bool_values() -> None:
    payload = {
        "usageMetadata": {
            "promptTokenCount": "120",  # string, not int
            "candidatesTokenCount": True,  # bool is not a valid count
            "totalTokenCount": 165,
        }
    }
    assert ChatbotService._extract_usage_metadata(payload) == (None, None, 165)


def test_extract_usage_metadata_tolerates_non_dict_block() -> None:
    assert ChatbotService._extract_usage_metadata(
        {"usageMetadata": "nonsense"}
    ) == (None, None, None)


def test_build_turn_usage_combines_tokens_tool_count_and_latency() -> None:
    payload = {
        "usageMetadata": {
            "promptTokenCount": 200,
            "candidatesTokenCount": 60,
            "totalTokenCount": 260,
        }
    }
    usage = ChatbotService._build_turn_usage(
        payload, tool_calls_total=3, started_at=0.0
    )
    assert isinstance(usage, ChatTurnUsage)
    assert usage.prompt_tokens == 200
    assert usage.completion_tokens == 60
    assert usage.total_tokens == 260
    assert usage.tool_call_count == 3
    # monotonic() - 0.0 is a large positive number of seconds; * 1000 ms.
    assert usage.latency_ms >= 0


def test_build_turn_usage_keeps_tokens_none_when_missing() -> None:
    usage = ChatbotService._build_turn_usage(
        {}, tool_calls_total=0, started_at=0.0
    )
    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
    assert usage.total_tokens is None
    assert usage.tool_call_count == 0
    assert usage.latency_ms >= 0
