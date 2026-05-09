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


# No-customer-accounts example labels (Examples 9-11) cover the failure
# pattern from staging pipeline_run 365: M&A advisory firms, Section
# 15(c)(3)-1 / (k)(2)(i) exemption reports, and "does not carry customer
# accounts" boilerplate. Without this set the prompt sends those firms
# back as ``unknown`` / partner=null with low confidence and they land
# as ``needs_review``; the rule + examples teach the model to return
# ``self_clearing`` with confidence >= 0.85 instead.
NO_CUSTOMER_ACCOUNTS_EXAMPLE_LABELS = (
    "Example 9 — M&A advisory only",
    "Example 10 — Section 15(c)(3)-1",
    'Example 11 — "Does not carry customer accounts"',
)


# Distinguishing fragments of the special-case rule. Pinning each
# fragment separately gives a precise failure mode if a future refactor
# trims one of the trigger phrases (M&A advisory / 15(c)(3)-1 /
# does-not-carry-customer-accounts boilerplate), each of which gates a
# different real-world filing pattern.
NO_CUSTOMER_ACCOUNTS_RULE_FRAGMENTS = (
    "Special-case rule",
    "does not carry customer accounts",
    "does not hold customer funds",
    "M&A advisory",
    "Section 15(c)(3)-1",
    "(k)(2)(i)",
    "self_clearing",
    "confidence_score >= 0.85",
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

    @pytest.mark.parametrize("fragment", NO_CUSTOMER_ACCOUNTS_RULE_FRAGMENTS)
    def test_contains_no_customer_accounts_rule_fragments(
        self, service: LlmParserService, fragment: str
    ) -> None:
        """The special-case rule that flips no-customer-accounts firms to
        self_clearing is the load-bearing guidance from staging
        pipeline_run 365 — pin every distinguishing phrase so a refactor
        that drops one trigger does not silently re-flag the
        corresponding filing pattern as ``needs_review``."""
        prompt = service.build_prompt()
        assert fragment in prompt, f"missing rule fragment: {fragment!r}"

    @pytest.mark.parametrize("label", NO_CUSTOMER_ACCOUNTS_EXAMPLE_LABELS)
    def test_contains_no_customer_accounts_example_labels(
        self, service: LlmParserService, label: str
    ) -> None:
        """Examples 9-11 ground the special-case rule with worked outputs.
        Without each label the model has no template for the
        partner=null + self_clearing combination on these filings."""
        prompt = service.build_prompt()
        assert label in prompt, f"missing example label: {label!r}"

    def test_no_customer_accounts_examples_pin_self_clearing_partner_null(
        self, service: LlmParserService
    ) -> None:
        """Each Example 9/10/11 worked output must show
        ``"clearing_type": "self_clearing"`` with ``"clearing_partner":
        null`` — that is the exact downstream shape the
        ``partner_required`` flip in ``extract_structured_data`` relies
        on (a self_clearing row with null partner is the only NULL-partner
        case that lands as ``parsed`` instead of ``needs_review``)."""
        prompt = service.build_prompt()
        for label in NO_CUSTOMER_ACCOUNTS_EXAMPLE_LABELS:
            label_idx = prompt.index(label)
            # Slice forward to the start of the next example or the
            # closing analysis prompt to bound the search to this
            # example's expected-output block.
            tail = prompt[label_idx:]
            for next_marker in ("\nExample ", "\nNow analyze"):
                cut = tail.find(next_marker, 1)
                if cut > 0:
                    tail = tail[:cut]
                    break
            assert '"clearing_type": "self_clearing"' in tail, (
                f"{label!r} expected output is not self_clearing"
            )
            assert '"clearing_partner": null' in tail, (
                f"{label!r} expected output does not pin clearing_partner=null"
            )


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

    @pytest.mark.parametrize("fragment", NO_CUSTOMER_ACCOUNTS_RULE_FRAGMENTS)
    def test_inherits_no_customer_accounts_rule_fragments(
        self, service: LlmParserService, fragment: str
    ) -> None:
        """OCR-wrapped prompts must carry the special-case rule too — the
        scanned-image path goes through Vision OCR but the LLM still
        depends on the same rule to flip no-customer-accounts firms."""
        prompt = service.build_prompt_with_ocr_text("placeholder")
        assert fragment in prompt, f"missing rule fragment in OCR prompt: {fragment!r}"

    @pytest.mark.parametrize("label", NO_CUSTOMER_ACCOUNTS_EXAMPLE_LABELS)
    def test_inherits_no_customer_accounts_example_labels(
        self, service: LlmParserService, label: str
    ) -> None:
        prompt = service.build_prompt_with_ocr_text("placeholder")
        assert label in prompt, f"missing example label in OCR prompt: {label!r}"

    def test_contains_base_prompt_verbatim(self, service: LlmParserService) -> None:
        base = service.build_prompt()
        wrapped = service.build_prompt_with_ocr_text("placeholder")
        assert base in wrapped
