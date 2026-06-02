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


# No-customer-accounts example labels (Examples 9 and 11) cover firms
# that genuinely have no clearing relationship — M&A advisory and
# "does not carry customer accounts" boilerplate. Without this set the
# prompt sends those firms back as ``unknown`` / partner=null with low
# confidence and they land as ``needs_review``; the rule + examples
# teach the model to return ``non_carrying`` with confidence >= 0.85
# instead (distinct from self_clearing, which means the firm holds
# securities for OTHER broker-dealers).
#
# Example 10 was a (k)(2)(i)/15c3-3 example that incorrectly directed
# the model to return ``self_clearing`` for (k)(2)(ii) introducing
# brokers. Reclassified to ``fully_disclosed`` in the FINRA/FOCUS
# clearing-mismatch fix (2026-05-29) — see ``K2II_EXAMPLE_LABEL`` and
# ``test_k2ii_example_pins_fully_disclosed_null_partner`` below.
NO_CUSTOMER_ACCOUNTS_EXAMPLE_LABELS = (
    "Example 9 — M&A advisory only",
    'Example 11 — "Does not carry customer accounts"',
)


# Example 10 is now the (k)(2)(ii) introducing-broker case. The model
# must NOT treat (k)(2)(ii) as a self-clearing signal — every firm
# claiming this exemption has a clearing partner by definition. The
# example pins the expected output to ``fully_disclosed`` with
# ``partner=null`` and ``confidence_score=0.5`` so a downstream FINRA
# reconciliation step fills in the partner name.
K2II_EXAMPLE_LABEL = (
    "Example 10 — (k)(2)(ii) introducing-broker exemption "
    "(partner not named in FOCUS)"
)


# Distinguishing fragments of the special-case rule. Pinning each
# fragment separately gives a precise failure mode if a future refactor
# trims one of the trigger phrases. The fragments include both the
# legitimate self-clearing triggers (M&A advisory / 15(c)(3)-1 / (k)(1)
# / (k)(2)(i)) and the explicit (k)(2)(ii) exclusion clause — the
# latter is load-bearing for the FINRA/FOCUS clearing-mismatch fix
# (2026-05-29): removing it re-introduces the bug where introducing
# brokers are misclassified as self-clearing.
NO_CUSTOMER_ACCOUNTS_RULE_FRAGMENTS = (
    "Special-case rule",
    "no customer accounts at all",
    "M&A advisory",
    "Section 15(c)(3)-1",
    "(k)(1) exemption",
    "(k)(2)(i) exemption",
    "non_carrying",
    "confidence_score >= 0.85",
    "(k)(2)(ii) is NOT a self-clearing signal",
    "introducing-broker exemption",
    "Never return self_clearing for a (k)(2)(ii) firm",
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

    def test_clearing_type_enum_includes_non_carrying(self, service: LlmParserService) -> None:
        """The clearing-type enum is the five-value canonical set
        ``fully_disclosed | self_clearing | omnibus | non_carrying |
        unknown``. ``non_carrying`` was added so no-customer-account firms
        stop being mislabeled ``self_clearing``."""
        prompt = service.build_prompt()
        assert (
            "'fully_disclosed', 'self_clearing', 'omnibus', 'non_carrying', or "
            "'unknown'" in prompt
        )

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

    def test_no_customer_accounts_examples_pin_non_carrying_partner_null(
        self, service: LlmParserService
    ) -> None:
        """Examples 9 and 11 (M&A advisory + "does not carry customer
        accounts" boilerplate) worked outputs must show
        ``"clearing_type": "non_carrying"`` with ``"clearing_partner":
        null`` — these firms have no customer accounts at all and are
        distinct from self_clearing (which means the firm holds securities
        for OTHER broker-dealers). The partner-gate exemption + the
        clearing_validator rely on this shape so no-customer-account firms
        stop being mislabeled self_clearing. Example 10 — the (k)(2)(ii)
        introducing case — is intentionally excluded; see
        ``test_k2ii_example_pins_fully_disclosed_null_partner``.
        """
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
            assert '"clearing_type": "non_carrying"' in tail, (
                f"{label!r} expected output is not non_carrying"
            )
            assert '"clearing_partner": null' in tail, (
                f"{label!r} expected output does not pin clearing_partner=null"
            )

    def test_k2ii_example_label_present(self, service: LlmParserService) -> None:
        """The (k)(2)(ii) introducing-broker example must be in the prompt
        so the model has a worked template for the most common cause of
        misclassification we saw in the 2026-05-28 audit (38 of 50
        mismatches were introducing brokers labeled Self-Clearing)."""
        prompt = service.build_prompt()
        assert K2II_EXAMPLE_LABEL in prompt, (
            f"missing (k)(2)(ii) example label: {K2II_EXAMPLE_LABEL!r}"
        )

    def test_k2ii_example_pins_fully_disclosed_null_partner(
        self, service: LlmParserService
    ) -> None:
        """The (k)(2)(ii) example must pin
        ``clearing_type='fully_disclosed'`` with ``clearing_partner=null``
        and ``confidence_score=0.5``. The downstream FINRA Form BD
        reconciler relies on the NULL-partner-with-fully_disclosed shape
        to know which rows it should fill in — collapsing this to
        ``self_clearing`` re-introduces the bug audited on 2026-05-28."""
        prompt = service.build_prompt()
        label_idx = prompt.index(K2II_EXAMPLE_LABEL)
        tail = prompt[label_idx:]
        for next_marker in ("\nExample ", "\nNow analyze"):
            cut = tail.find(next_marker, 1)
            if cut > 0:
                tail = tail[:cut]
                break
        assert '"clearing_type": "fully_disclosed"' in tail, (
            "(k)(2)(ii) example expected output is not fully_disclosed"
        )
        assert '"clearing_partner": null' in tail, (
            "(k)(2)(ii) example expected output does not pin clearing_partner=null"
        )
        assert '"confidence_score": 0.5' in tail, (
            "(k)(2)(ii) example expected output does not pin confidence_score=0.5"
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
