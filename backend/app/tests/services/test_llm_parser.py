"""Tests for the clearing-partner extraction prompt builder.

The extraction quality of the Gemini/OpenAI call is gated entirely by
the prompt content returned from ``LlmParserService.build_prompt``. The
brief calls for an expanded set of search hints plus four extra example
labels covering edge cases that the original three-example prompt
missed (introducing-broker, omnibus, foreign clearing partner, prime
broker, "executes and clears" phrasing).

These tests are pure-string assertions — they don't exercise the
provider clients. The provider clients are covered by
``test_gemini_responses.py`` and ``test_openai_responses.py`` and
treat the prompt as an opaque payload, so prompt content has no
collision with their fixtures.
"""

from __future__ import annotations

import pytest

from app.services.llm_parser import LlmParserService


SEARCH_HINTS = (
    "clearing agreement with",
    "clears all transactions through",
    "introducing broker",
    "carries customer accounts on a fully disclosed basis",
    "self-clearing",
    "executes and clears",
    "prime broker",
    "omnibus account",
)


NEW_EXAMPLE_LABELS = (
    "Example 4 — Introducing Broker",
    "Example 5 — Omnibus",
    "Example 6 — Foreign clearing partner",
    "Example 7 — Prime brokerage",
    'Example 8 — "Executes and clears"',
)


@pytest.fixture
def service() -> LlmParserService:
    return LlmParserService()


class TestBuildPrompt:
    def test_returns_non_empty_string(self, service: LlmParserService) -> None:
        prompt = service.build_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    @pytest.mark.parametrize("hint", SEARCH_HINTS)
    def test_contains_each_search_hint(self, service: LlmParserService, hint: str) -> None:
        """Each anchor phrase must appear verbatim in the prompt so the
        LLM is primed to scan for it before reading the document
        end-to-end."""
        prompt = service.build_prompt()
        assert hint in prompt, f"missing search hint: {hint!r}"

    def test_search_hints_section_present(self, service: LlmParserService) -> None:
        prompt = service.build_prompt()
        assert "Search hints" in prompt

    @pytest.mark.parametrize("label", NEW_EXAMPLE_LABELS)
    def test_contains_each_new_example_label(
        self, service: LlmParserService, label: str
    ) -> None:
        prompt = service.build_prompt()
        assert label in prompt, f"missing example label: {label!r}"

    def test_preserves_legacy_examples(self, service: LlmParserService) -> None:
        """Examples 1–3 from the original prompt must still be present so
        the well-tested fully_disclosed / self_clearing / unknown paths
        keep their grounding signal."""
        prompt = service.build_prompt()
        assert "Example 1 — Fully Disclosed" in prompt
        assert "Example 2 — Self-Clearing" in prompt
        assert "Example 3 — Unknown/Ambiguous" in prompt

    def test_clearing_type_enum_unchanged(self, service: LlmParserService) -> None:
        """The downstream ``ClearingExtractionResult`` and rollup logic
        depend on the exact set ``fully_disclosed | self_clearing |
        omnibus | unknown``. Adding examples must not introduce a new
        enum value into the schema instructions."""
        prompt = service.build_prompt()
        assert "'fully_disclosed', 'self_clearing', 'omnibus', or 'unknown'" in prompt


class TestBuildPromptWithOcrText:
    def test_wraps_ocr_text_with_markers(self, service: LlmParserService) -> None:
        ocr_text = "The Company clears through Pershing LLC."
        prompt = service.build_prompt_with_ocr_text(ocr_text)
        assert "── BEGIN OCR TEXT ──" in prompt
        assert "── END OCR TEXT ──" in prompt
        assert ocr_text in prompt

    def test_collapses_whitespace_in_ocr_text(self, service: LlmParserService) -> None:
        """``build_prompt_with_ocr_text`` collapses arbitrary whitespace so
        page-break newlines from Vision OCR don't blow up the token
        budget. Verify a multi-line input lands as a single space-joined
        line."""
        prompt = service.build_prompt_with_ocr_text("foo\n\n  bar\tbaz")
        assert "foo bar baz" in prompt

    def test_handles_empty_ocr_text(self, service: LlmParserService) -> None:
        prompt = service.build_prompt_with_ocr_text("")
        assert "── BEGIN OCR TEXT ──" in prompt
        assert "── END OCR TEXT ──" in prompt

    @pytest.mark.parametrize("hint", SEARCH_HINTS)
    def test_inherits_search_hints(self, service: LlmParserService, hint: str) -> None:
        prompt = service.build_prompt_with_ocr_text("placeholder")
        assert hint in prompt

    @pytest.mark.parametrize("label", NEW_EXAMPLE_LABELS)
    def test_inherits_new_example_labels(
        self, service: LlmParserService, label: str
    ) -> None:
        prompt = service.build_prompt_with_ocr_text("placeholder")
        assert label in prompt

    def test_contains_base_prompt_verbatim(self, service: LlmParserService) -> None:
        base = service.build_prompt()
        wrapped = service.build_prompt_with_ocr_text("placeholder")
        assert base in wrapped
