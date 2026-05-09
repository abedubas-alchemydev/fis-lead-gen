"""Tests for the LLM-driven firm-alias enricher.

Covers the path that lets short-acronym firms (BOFA / TD / RBC / etc.)
escape the validator's domain-anchor blind spot:

  - Happy path: Gemini returns a list of brand/parent aliases; the
    enricher cleans dupes / whitespace / legal-name matches and
    returns them.
  - Legal-name-equivalent aliases are filtered (case + whitespace +
    punctuation insensitive) so they don't pollute the token pool with
    no-new-signal entries.
  - Empty Gemini result is preserved as ``[]`` and persists, so the
    enricher doesn't re-fire on the next page-mount for firms that
    genuinely have no widely-used alternates.
  - Gemini configuration / extraction errors surface as ``None`` from
    ``enrich_firm_aliases`` so the caller leaves ``resolver_aliases``
    NULL for retry.

Gemini is mocked at the ``GeminiResponsesClient.extract_firm_aliases``
boundary via an injected client; no respx is needed because we don't
exercise the HTTP layer here (that's covered in test_gemini_responses).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.firm_alias_enricher import (
    FirmAliasResult,
    _clean_aliases,
    enrich_firm_aliases,
)
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiFirmAliasExtraction,
)


def _extraction(
    aliases: list[str],
    *,
    confidence: float = 0.85,
    rationale: str = "Common parent-brand variants of the firm.",
) -> GeminiFirmAliasExtraction:
    return GeminiFirmAliasExtraction(
        aliases=aliases,
        confidence_score=confidence,
        rationale=rationale,
    )


# ─────────────────────── _clean_aliases unit ────────────────────────


class TestCleanAliases:
    def test_drops_empty_and_whitespace_only(self) -> None:
        result = _clean_aliases(
            ["", "   ", "Bank of America Securities", "\t"],
            legal_name="BOFA SECURITIES, INC.",
        )
        assert result == ["Bank of America Securities"]

    def test_collapses_internal_whitespace(self) -> None:
        result = _clean_aliases(
            ["Bank   of   America\tSecurities"],
            legal_name="BOFA SECURITIES, INC.",
        )
        assert result == ["Bank of America Securities"]

    def test_filters_legal_name_dupe_case_insensitive(self) -> None:
        result = _clean_aliases(
            [
                "BOFA Securities, Inc.",
                "bofa securities inc",
                "Bank of America Securities",
            ],
            legal_name="BOFA SECURITIES, INC.",
        )
        assert result == ["Bank of America Securities"]

    def test_dedupes_case_and_whitespace_insensitive(self) -> None:
        result = _clean_aliases(
            [
                "Bank of America Securities",
                "BANK OF AMERICA SECURITIES",
                "  Bank of America  Securities  ",
            ],
            legal_name="BOFA SECURITIES, INC.",
        )
        # Keeps the first-seen casing.
        assert result == ["Bank of America Securities"]

    def test_drops_non_string_entries_defensively(self) -> None:
        # Pydantic should already reject these, but the cleaner is
        # belt-and-braces against future schema drift.
        result = _clean_aliases(
            ["Bank of America Securities", None, 42],  # type: ignore[list-item]
            legal_name="BOFA SECURITIES, INC.",
        )
        assert result == ["Bank of America Securities"]


# ─────────────────────── enrich_firm_aliases happy path ────────────────────


class TestEnrichFirmAliases:
    @pytest.mark.asyncio
    async def test_returns_cleaned_aliases_on_success(self) -> None:
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(
            return_value=_extraction(
                ["Bank of America Securities", "Bank of America"],
                confidence=0.9,
            )
        )

        result = await enrich_firm_aliases(
            "BOFA SECURITIES, INC.", "283942", client=client
        )

        assert isinstance(result, FirmAliasResult)
        assert result.aliases == ["Bank of America Securities", "Bank of America"]
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_passes_crd_into_prompt_when_provided(self) -> None:
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(return_value=_extraction([]))

        await enrich_firm_aliases("Acme Securities LLC", "1234", client=client)

        # The single positional kwarg of the call carries the prompt;
        # we want CRD threaded in for disambiguation on shared-name firms.
        call_kwargs = client.extract_firm_aliases.call_args.kwargs
        assert "FINRA CRD: 1234" in call_kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_passes_no_crd_marker_when_missing(self) -> None:
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(return_value=_extraction([]))

        await enrich_firm_aliases("Acme Securities LLC", None, client=client)

        call_kwargs = client.extract_firm_aliases.call_args.kwargs
        assert "FINRA CRD: (not provided)" in call_kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_filters_legal_name_aliases(self) -> None:
        """Gemini sometimes echoes the legal name back as an "alias";
        the cleaner drops it because it adds no new tokens."""
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(
            return_value=_extraction(
                [
                    "BOFA SECURITIES, INC.",  # echo of the legal name
                    "Bank of America Securities",
                ]
            )
        )

        result = await enrich_firm_aliases(
            "BOFA SECURITIES, INC.", "283942", client=client
        )

        assert result is not None
        assert result.aliases == ["Bank of America Securities"]

    @pytest.mark.asyncio
    async def test_empty_gemini_result_preserves_empty_list(self) -> None:
        """A successful Gemini call that returns no aliases is a
        legitimate outcome — many small firms genuinely have no
        widely-known alternate names. The result is ``aliases=[]``,
        not ``None``, so the caller persists ``[]`` to skip retry."""
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(return_value=_extraction([]))

        result = await enrich_firm_aliases(
            "JANE STREET CAPITAL, LLC", "32132", client=client
        )

        assert result is not None
        assert result.aliases == []

    @pytest.mark.asyncio
    async def test_empty_firm_name_short_circuits_without_calling_gemini(self) -> None:
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(
            return_value=_extraction(["should not be returned"])
        )

        result = await enrich_firm_aliases("", "1234", client=client)

        assert result is not None
        assert result.aliases == []
        client.extract_firm_aliases.assert_not_called()


# ─────────────────────── enrich_firm_aliases failure paths ────────────────


class TestEnrichFirmAliasesFailure:
    @pytest.mark.asyncio
    async def test_gemini_configuration_error_returns_none(self) -> None:
        """Caller leaves the column NULL for retry on a future request.
        We deliberately don't raise — alias enrichment is a best-effort
        augmentation, not a critical path."""
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(
            side_effect=GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        )

        result = await enrich_firm_aliases(
            "BOFA SECURITIES, INC.", "283942", client=client
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_gemini_extraction_error_returns_none(self) -> None:
        client = AsyncMock()
        client.extract_firm_aliases = AsyncMock(
            side_effect=GeminiExtractionError("Gemini returned invalid JSON.")
        )

        result = await enrich_firm_aliases(
            "BOFA SECURITIES, INC.", "283942", client=client
        )

        assert result is None
