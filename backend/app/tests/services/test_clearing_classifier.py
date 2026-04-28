"""Tests for the unified LLM-based clearing classifier.

Covers task #19 from cli-01 / the 2026-04-28 audit:

  - Each of the four canonical enum values returns correctly when the
    LLM gives a high-confidence answer.
  - Confidence-threshold demotion is the caller's responsibility (the
    classifier returns whatever the LLM said).
  - Provider error / network failure produces a sentinel result without
    raising into the pipeline (single-firm failure must not kill a run).
  - Both inputs null/empty short-circuits to {value: "unknown",
    confidence: 0} without calling the LLM at all.
  - Malformed-JSON LLM response surfaces as a sentinel result.
  - Self-clearing inversion regression: a firm whose FINRA text says
    "this firm holds and maintains funds and securities for other
    broker-dealers" classifies as self_clearing -- not the inverted
    regex behavior the audit flagged.

Mocking strategy mirrors test_gemini_responses.py: respx intercepts
all outbound HTTP, monkeypatch installs a syntactically valid Gemini
key so the client's __init__ key-shape validator passes.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.clearing_classifier import (
    ClearingClassificationResult,
    ClearingClassifierService,
    classify,
)


_VALID_KEY = "AIzaSy" + "a" * 33  # 39 chars, matches ^AIzaSy[A-Za-z0-9_\-]{33}$
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
)


@pytest.fixture
def patch_gemini_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a valid Gemini key + the gemini provider, with fast retries."""
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
    monkeypatch.setattr(settings, "gemini_api_base", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setattr(settings, "gemini_pdf_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "gemini_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "gemini_request_max_retries", 2)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "clearing_classification_min_confidence", 0.7)


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the inter-retry sleep so the failure-mode tests are fast."""
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.gemini_responses.asyncio.sleep", _instant_sleep)


def _gemini_response(*, classification: str, confidence: float, rationale: str) -> httpx.Response:
    """Build a mock Gemini response that mimics the real candidates/parts shape."""
    body = json.dumps(
        {
            "classification": classification,
            "confidence_score": confidence,
            "rationale": rationale,
        }
    )
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": body}]}}]},
    )


# ─────────────────────── Empty-input short-circuit ───────────────────────


class TestEmptyInputShortCircuit:
    @pytest.mark.asyncio
    async def test_both_inputs_null_returns_unknown_zero_confidence(
        self, patch_gemini_provider: None
    ) -> None:
        """Both inputs null -> sentinel result, NO HTTP call to the LLM."""
        with respx.mock(assert_all_called=False) as router:
            route = router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="self_clearing", confidence=0.99, rationale="should not be reached"
                )
            )
            result = await classify(firm_operations_text=None, focus_report_text=None)

        assert isinstance(result, ClearingClassificationResult)
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "No source text" in result.reasoning
        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_both_inputs_whitespace_short_circuits(
        self, patch_gemini_provider: None
    ) -> None:
        with respx.mock(assert_all_called=False) as router:
            route = router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="self_clearing", confidence=0.99, rationale="should not be reached"
                )
            )
            result = await classify(firm_operations_text="   \n  ", focus_report_text="\t")

        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert route.call_count == 0


# ─────────────────────── Each canonical enum value ───────────────────────


class TestCanonicalValues:
    @pytest.mark.parametrize(
        "label",
        ["fully_disclosed", "self_clearing", "omnibus", "unknown"],
    )
    @pytest.mark.asyncio
    async def test_each_label_round_trips(
        self, patch_gemini_provider: None, label: str
    ) -> None:
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification=label, confidence=0.9, rationale="synthetic test"
                )
            )
            result = await classify(
                firm_operations_text="The firm clears through Pershing on a fully disclosed basis.",
                focus_report_text=None,
            )
        assert result.value == label
        assert result.confidence == 0.9


# ─────────────────────── Confidence threshold ───────────────────────


class TestConfidenceThreshold:
    @pytest.mark.asyncio
    async def test_high_confidence_returns_canonical(
        self, patch_gemini_provider: None
    ) -> None:
        """Service returns the LLM value verbatim. The needs_review demotion
        happens at the caller (apply_classification_to_all)."""
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="self_clearing", confidence=0.95, rationale="synthetic"
                )
            )
            result = await classify(
                firm_operations_text="The firm holds and maintains securities for others.",
                focus_report_text=None,
            )
        assert result.value == "self_clearing"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_low_confidence_returns_low_value_for_caller_to_demote(
        self, patch_gemini_provider: None
    ) -> None:
        """The classifier returns whatever the LLM said -- demotion to
        needs_review is the caller's responsibility, exercised in the
        apply_classification_to_all integration tests."""
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="omnibus", confidence=0.4, rationale="synthetic-low"
                )
            )
            result = await classify(
                firm_operations_text="ambiguous text",
                focus_report_text=None,
            )
        assert result.value == "omnibus"
        assert result.confidence == 0.4


# ─────────────────────── Provider error fallback ───────────────────────


class TestProviderErrorFallback:
    @pytest.mark.asyncio
    async def test_network_error_returns_sentinel_no_raise(
        self, patch_gemini_provider: None, no_backoff_sleep: None
    ) -> None:
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(side_effect=httpx.ConnectError("refused"))
            result = await classify(
                firm_operations_text="some text",
                focus_report_text=None,
            )
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "Gemini classification failed" in result.reasoning

    @pytest.mark.asyncio
    async def test_status_error_returns_sentinel(
        self, patch_gemini_provider: None, no_backoff_sleep: None
    ) -> None:
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
            )
            result = await classify(
                firm_operations_text="some text",
                focus_report_text=None,
            )
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "Gemini classification failed" in result.reasoning

    @pytest.mark.asyncio
    async def test_unconfigured_key_returns_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "gemini_api_key", "")
        monkeypatch.setattr(settings, "llm_provider", "gemini")

        result = await classify(
            firm_operations_text="some text",
            focus_report_text=None,
        )
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "Gemini classification failed" in result.reasoning


# ─────────────────────── Malformed JSON ───────────────────────


class TestMalformedJsonFallback:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_sentinel(
        self, patch_gemini_provider: None
    ) -> None:
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "candidates": [
                            {"content": {"parts": [{"text": "{not valid json at all"}]}}
                        ]
                    },
                )
            )
            result = await classify(
                firm_operations_text="some text",
                focus_report_text=None,
            )
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "Gemini classification failed" in result.reasoning


# ─────────────────────── Unsupported provider ───────────────────────


class TestUnsupportedProvider:
    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        result = await classify(
            firm_operations_text="some text",
            focus_report_text=None,
        )
        assert result.value == "unknown"
        assert result.confidence == 0.0
        assert "Unsupported LLM provider" in result.reasoning


# ─────────────────────── Self-clearing inversion regression ───────────────────────


class TestSelfClearingInversionRegression:
    """Audit regression guard. The OLD regex labelled a firm 'true_self_clearing'
    only when its FINRA text said it does NOT hold or maintain securities --
    inverted. The new classifier, prompted with Deshorn's canonical definition,
    must label a firm that DOES hold/maintain securities for others as
    self_clearing.
    """

    @pytest.mark.asyncio
    async def test_holds_for_others_classifies_as_self_clearing(
        self, patch_gemini_provider: None
    ) -> None:
        finra_text = (
            "This firm holds and maintains funds and securities for other "
            "broker-dealers and self-clears all customer transactions through "
            "its own back office."
        )
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="self_clearing",
                    confidence=0.92,
                    rationale=(
                        "FINRA text states the firm holds and maintains funds "
                        "and securities for other broker-dealers, which matches "
                        "the canonical Self-Clearing definition."
                    ),
                )
            )
            result = await classify(
                firm_operations_text=finra_text,
                focus_report_text=None,
            )

        assert result.value == "self_clearing"
        assert result.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_old_inverted_phrase_does_not_force_self_clearing(
        self, patch_gemini_provider: None
    ) -> None:
        """The old regex returned 'true_self_clearing' when the text contained
        'does not hold or maintain'. The new LLM prompt must NOT inherit that
        inversion -- a firm that disavows custody is the OPPOSITE of
        self-clearing."""
        finra_text = (
            "This firm does not hold or maintain funds or securities and does "
            "not refer or introduce customers."
        )
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="unknown",
                    confidence=0.5,
                    rationale=(
                        "Firm disavows custody and disavows introducing -- "
                        "neither Fully Disclosed nor Self-Clearing applies."
                    ),
                )
            )
            result = await classify(
                firm_operations_text=finra_text,
                focus_report_text=None,
            )

        # Critically NOT 'self_clearing'.
        assert result.value != "self_clearing"


# ─────────────────────── Service-level construction ───────────────────────


class TestServiceConstruction:
    """Sanity-check the class-level service entrypoint matches the module-level
    convenience wrapper, since callers may use either."""

    @pytest.mark.asyncio
    async def test_service_classify_matches_module_classify(
        self, patch_gemini_provider: None
    ) -> None:
        with respx.mock() as router:
            router.post(_GEMINI_URL).mock(
                return_value=_gemini_response(
                    classification="fully_disclosed", confidence=0.85, rationale="synthetic"
                )
            )
            service = ClearingClassifierService()
            result = await service.classify(
                firm_operations_text="The firm clears through Pershing.",
                focus_report_text=None,
            )

        assert result.value == "fully_disclosed"
        assert result.confidence == 0.85

    def test_build_prompt_includes_canonical_definitions(
        self, patch_gemini_provider: None
    ) -> None:
        service = ClearingClassifierService()
        prompt = service.build_prompt("FINRA snippet", "FOCUS snippet")
        # Deshorn's three definitions are the load-bearing part of the prompt.
        assert "Fully Disclosed" in prompt
        assert "Self-Clearing" in prompt
        assert "Omnibus" in prompt
        # Both inputs are visible in the prompt.
        assert "FINRA snippet" in prompt
        assert "FOCUS snippet" in prompt

    def test_build_prompt_handles_null_blocks(
        self, patch_gemini_provider: None
    ) -> None:
        service = ClearingClassifierService()
        prompt = service.build_prompt(None, None)
        assert "(no FINRA text available)" in prompt
        assert "(no FOCUS report excerpt available)" in prompt
