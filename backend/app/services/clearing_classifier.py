"""Unified LLM-based clearing classifier.

Replaces the inverted regex top-level decision in
``services/classification.py::determine_clearing_classification`` and
unifies the two parallel classification systems documented in
``reports/clearing-classification-audit-2026-04-28.md``:

  - Stream A regex (FINRA firm_operations_text) -> broker_dealer.clearing_classification
  - Stream B LLM extraction (FOCUS PDF)         -> clearing_arrangements.clearing_type

Both feeds now flow through this single module. The LLM is prompted with
Deshorn King's exact canonical definitions verbatim (per the 2026-04-27
client meeting) and returns one of the four-value canonical labels plus
a confidence score. The caller writes ``needs_review`` when confidence
falls below ``settings.clearing_classification_min_confidence``.

DESHORN'S CANONICAL DEFINITIONS (used verbatim in the LLM prompt below):
  - Fully Disclosed -- firm reveals its clearing arrangement with a
    national service.
  - Self-Clearing  -- firm holds/maintains securities for other
    broker-dealers.
  - Omnibus        -- firm has multiple clearing arrangements AND clears
    for other companies; must also be self-clearing.

Failure-mode contract (review-queue semantics):
  - Both inputs null/empty -> {value: "unknown", confidence: 0.0}
    (no LLM call). Caller writes ``needs_review`` because confidence
    < threshold.
  - Provider error / network failure / malformed JSON -> sentinel
    result {value: "unknown", confidence: 0.0, reasoning: "<error>"}.
    Caller writes ``needs_review``. The classifier never raises into
    the pipeline -- a single firm's failure must not kill the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import settings
from app.services.gemini_responses import (
    GeminiClassificationExtraction,
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.openai_responses import (
    OpenAIClassificationExtraction,
    OpenAIConfigurationError,
    OpenAIExtractionError,
    OpenAIResponsesClient,
)

logger = logging.getLogger(__name__)

CANONICAL_VALUES = frozenset({"fully_disclosed", "self_clearing", "omnibus", "unknown"})


@dataclass(frozen=True, slots=True)
class ClearingClassificationResult:
    """Result of one classifier call.

    ``value`` is one of the four canonical labels. The ``needs_review``
    sentinel is the responsibility of the caller (it is not a real
    classification, just a low-confidence fallback at the persistence
    layer).
    """
    value: str
    confidence: float
    reasoning: str


class ClearingClassifierService:
    """Provider-swappable clearing classifier (Gemini default, OpenAI alt)."""

    _SYSTEM_PROMPT = (
        "You are classifying U.S. broker-dealer firms by their clearing "
        "arrangement using the following CANONICAL DEFINITIONS. Treat "
        "these as the only valid labels; do not invent new ones.\n\n"
        "  - Fully Disclosed -- firm reveals its clearing arrangement "
        "with a national service.\n"
        "  - Self-Clearing -- firm holds/maintains securities for other "
        "broker-dealers.\n"
        "  - Omnibus -- firm has multiple clearing arrangements AND "
        "clears for other companies; must also be self-clearing.\n"
        "  - Unknown -- the source text is silent or ambiguous.\n\n"
        "You will be given two source texts. Read both carefully before "
        "deciding. The FINRA BrokerCheck text describes the firm's "
        "self-declared business operations. The FOCUS report excerpt is "
        "from the firm's annual X-17A-5 audit and typically names the "
        "clearing partner explicitly. If the two sources conflict, "
        "prefer the FOCUS report (it is audited; FINRA text is "
        "self-declared).\n\n"
        "Return strict JSON matching the schema: classification "
        "(one of fully_disclosed | self_clearing | omnibus | unknown), "
        "confidence_score (0..1), rationale (one or two sentences "
        "explaining the decision and citing specific phrases from the "
        "source texts).\n\n"
        "Common pitfalls to avoid:\n"
        "  - A firm that says it does NOT hold/maintain securities is "
        "the OPPOSITE of self-clearing. Do not flip the sign.\n"
        "  - A firm that introduces customers to another broker on a "
        "fully-disclosed basis is Fully Disclosed, not Self-Clearing, "
        "even if the named clearing partner is itself a self-clearing firm.\n"
        "  - Omnibus is rare. Only use it when the firm clearly clears "
        "for OTHER firms in addition to its own customers."
    )

    def __init__(self) -> None:
        self.gemini_client = GeminiResponsesClient()
        self.openai_client = OpenAIResponsesClient()

    def build_prompt(
        self,
        firm_operations_text: str | None,
        focus_report_text: str | None,
    ) -> str:
        finra_block = (firm_operations_text or "").strip() or "(no FINRA text available)"
        focus_block = (focus_report_text or "").strip() or "(no FOCUS report excerpt available)"
        return (
            f"{self._SYSTEM_PROMPT}\n\n"
            f"--- FINRA BROKERCHECK FIRM OPERATIONS TEXT ---\n{finra_block}\n\n"
            f"--- FOCUS REPORT CLEARING-RELATED EXCERPT ---\n{focus_block}\n"
        )

    async def classify(
        self,
        firm_operations_text: str | None,
        focus_report_text: str | None,
    ) -> ClearingClassificationResult:
        finra_present = bool((firm_operations_text or "").strip())
        focus_present = bool((focus_report_text or "").strip())
        if not finra_present and not focus_present:
            return ClearingClassificationResult(
                value="unknown",
                confidence=0.0,
                reasoning="No source text available.",
            )

        prompt = self.build_prompt(firm_operations_text, focus_report_text)

        if settings.llm_provider == "gemini":
            return await self._classify_via_gemini(prompt)
        if settings.llm_provider == "openai":
            return await self._classify_via_openai(prompt)
        return self._sentinel(
            f"Unsupported LLM provider '{settings.llm_provider}'. "
            "Configure LLM_PROVIDER=gemini or openai."
        )

    async def _classify_via_gemini(self, prompt: str) -> ClearingClassificationResult:
        try:
            extraction = await self.gemini_client.extract_classification_data(prompt=prompt)
        except (GeminiConfigurationError, GeminiExtractionError) as exc:
            logger.warning("Gemini classification failed: %s", exc)
            return self._sentinel(f"Gemini classification failed: {exc}")
        return self._from_gemini(extraction)

    async def _classify_via_openai(self, prompt: str) -> ClearingClassificationResult:
        try:
            extraction = await self.openai_client.extract_classification_data(prompt=prompt)
        except (OpenAIConfigurationError, OpenAIExtractionError) as exc:
            logger.warning("OpenAI classification failed: %s", exc)
            return self._sentinel(f"OpenAI classification failed: {exc}")
        return self._from_openai(extraction)

    @staticmethod
    def _from_gemini(extraction: GeminiClassificationExtraction) -> ClearingClassificationResult:
        return ClearingClassificationResult(
            value=extraction.classification,
            confidence=float(extraction.confidence_score),
            reasoning=extraction.rationale,
        )

    @staticmethod
    def _from_openai(extraction: OpenAIClassificationExtraction) -> ClearingClassificationResult:
        return ClearingClassificationResult(
            value=extraction.classification,
            confidence=float(extraction.confidence_score),
            reasoning=extraction.rationale,
        )

    @staticmethod
    def _sentinel(reason: str) -> ClearingClassificationResult:
        return ClearingClassificationResult(value="unknown", confidence=0.0, reasoning=reason)


async def classify(
    firm_operations_text: str | None,
    focus_report_text: str | None,
) -> ClearingClassificationResult:
    """Module-level convenience entrypoint.

    Constructs a fresh service per call. The provider clients are cheap
    to instantiate (httpx clients are created per-request inside their
    own ``_post_with_retries`` paths), so there is no benefit to a
    process-wide singleton.
    """
    service = ClearingClassifierService()
    return await service.classify(firm_operations_text, focus_report_text)
