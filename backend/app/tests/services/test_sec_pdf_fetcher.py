"""Unit tests for ``app.services.sec_pdf_fetcher``.

Focused on the Phase 3.1 addition: ``fetch_filing_pdf_bytes`` resolves
an EDGAR filing-index URL to a PDF document inside the package before
downloading. The direct ``fetch_sec_pdf_bytes`` primitive is exercised
indirectly by the broker-dealer summarise flow — covered there.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import sec_pdf_fetcher
from app.services.sec_pdf_fetcher import (
    SecPdfFetchError,
    _parse_edgar_filing_path,
    fetch_filing_pdf_bytes,
)


# ── URL parser ──────────────────────────────────────────────────────────


class TestParseEdgarFilingPath:
    def test_folder_url(self) -> None:
        assert _parse_edgar_filing_path(
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/"
        ) == ("1234567", "000123456725000001")

    def test_index_htm_url(self) -> None:
        # Form 4 watcher writes URLs that end with ``{accession}-index.htm`` —
        # the parser still extracts cik + accession_no_dashes from the prefix.
        url = (
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/"
            "0001234567-25-000001-index.htm"
        )
        assert _parse_edgar_filing_path(url) == ("1234567", "000123456725000001")

    def test_url_with_primary_doc(self) -> None:
        url = (
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/"
            "primary_document.pdf"
        )
        assert _parse_edgar_filing_path(url) == ("1234567", "000123456725000001")

    def test_non_edgar_url_returns_none(self) -> None:
        assert _parse_edgar_filing_path("https://example.com/foo.pdf") is None

    def test_malformed_path_returns_none(self) -> None:
        assert _parse_edgar_filing_path(
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        ) is None


# ── fetch_filing_pdf_bytes ──────────────────────────────────────────────


class TestFetchFilingPdfBytes:
    async def test_direct_pdf_url_short_circuits_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the caller already has a ``.pdf`` URL we skip the
        ``index.json`` walk and go straight to the streaming
        download — saves one round-trip."""
        called_resolver = AsyncMock()
        monkeypatch.setattr(
            sec_pdf_fetcher, "resolve_filing_pdf_url", called_resolver
        )

        async def fake_direct_fetch(url: str) -> bytes:
            assert url.endswith(".pdf")
            return b"%PDF-1.4 direct"

        monkeypatch.setattr(
            sec_pdf_fetcher, "fetch_sec_pdf_bytes", fake_direct_fetch
        )

        out = await fetch_filing_pdf_bytes(
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/brochure.pdf"
        )
        assert out == b"%PDF-1.4 direct"
        called_resolver.assert_not_called()

    async def test_html_index_url_resolves_then_downloads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An HTML-index URL triggers the EDGAR ``index.json`` walk.
        The resolver returns the chosen PDF URL; we then download it
        with ``fetch_sec_pdf_bytes``."""

        async def fake_resolve(
            *, cik: str, accession_number: str, primary_document: Any, form_type: Any
        ) -> str:
            assert cik == "1234567"
            assert accession_number == "000123456725000001"
            assert form_type == "Form ADV"
            return (
                "https://www.sec.gov/Archives/edgar/data/1234567/"
                "000123456725000001/part2a_brochure.pdf"
            )

        monkeypatch.setattr(sec_pdf_fetcher, "resolve_filing_pdf_url", fake_resolve)

        async def fake_direct_fetch(url: str) -> bytes:
            assert "part2a_brochure.pdf" in url
            return b"%PDF-1.4 brochure"

        monkeypatch.setattr(
            sec_pdf_fetcher, "fetch_sec_pdf_bytes", fake_direct_fetch
        )

        out = await fetch_filing_pdf_bytes(
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/"
            "0001234567-25-000001-index.htm",
            form_type="Form ADV",
        )
        assert out == b"%PDF-1.4 brochure"

    async def test_resolver_none_means_no_pdf_in_package(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Form 4 / most 13F-HR are XML-only — the resolver returns
        None and the fetcher raises a clean error so the chat tool
        can offer the source URL instead."""

        async def fake_resolve(**_kwargs: Any) -> None:
            return None

        monkeypatch.setattr(sec_pdf_fetcher, "resolve_filing_pdf_url", fake_resolve)

        with pytest.raises(SecPdfFetchError) as excinfo:
            await fetch_filing_pdf_bytes(
                "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/"
                "0001234567-25-000001-index.htm"
            )
        assert "no PDF in filing package" in str(excinfo.value)

    async def test_malformed_url_raises_clean_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_resolver = AsyncMock()
        monkeypatch.setattr(
            sec_pdf_fetcher, "resolve_filing_pdf_url", called_resolver
        )

        with pytest.raises(SecPdfFetchError) as excinfo:
            await fetch_filing_pdf_bytes("https://example.com/something.htm")
        assert "isn't an EDGAR filing path" in str(excinfo.value)
        called_resolver.assert_not_called()
