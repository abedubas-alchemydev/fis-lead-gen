"""Tests for the inline Form BD PDF extractor.

Two surfaces under test:

* ``_parse_form_bd_pdf`` — the in-memory parser. Asserted against the
  vendored fixture set in ``brokercheck_extractor/fixtures/`` (a small
  modern firm and a legacy terminated firm). The fixtures aren't in the
  Docker context so these tests are local-only — they don't run in the
  production image, which is fine because the parser is exercised at
  runtime by ``enrich_with_detail`` against the live FINRA endpoint.

* ``fetch_form_bd_detail`` — the public async entrypoint. Tests cover the
  PDF-not-found path (FINRA returns 404 → returns None) and the
  fetch-error path (transient upstream → propagates ``FinraPdfFetchError``).
  The happy path is implicitly covered by the parser tests; an
  end-to-end happy path against live FINRA is not exercised here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    FormBdDetail,
    _parse_form_bd_pdf,
    fetch_form_bd_detail,
)


# Fixture PDFs live at the repo root, alongside backend/. CI and local
# pytest both run from backend/ (per backend/pytest.ini ``testpaths =
# app/tests`` and the GHA ``working-directory: backend``), so this path
# resolves through the test file's location: app/tests/services/<this>.py
# ``parents[4]`` walks ``services -> tests -> app -> backend -> repo-root``.
_FIXTURES = Path(__file__).resolve().parents[4] / "brokercheck_extractor" / "fixtures"


def _fixture_bytes(name: str) -> bytes:
    path = _FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture not present: {path}")
    return path.read_bytes()


# ----- Parser: modern populated firm (Schwab, CRD 5393) -----

def test_parser_extracts_types_of_business_from_modern_firm() -> None:
    """The Charles Schwab fixture is a known-good fully-populated report.
    The Types of Business section should yield exactly the six entries
    Schwab files (verified against the brokercheck_extractor parser's
    docstring + spot-check)."""
    detail = _parse_form_bd_pdf("5393", _fixture_bytes("firm_5393_schwab.pdf"))

    assert detail.crd == "5393"
    assert len(detail.types_of_business) == 6
    assert "Investment advisory services" in detail.types_of_business
    assert "Mutual fund retailer" in detail.types_of_business
    # No business type should be the section preamble or boilerplate
    for entry in detail.types_of_business:
        assert "This section provides" not in entry
        assert not entry.startswith("This firm currently conducts")


def test_parser_extracts_executive_officers_from_modern_firm() -> None:
    """Each officer entry must carry at minimum a ``name`` key. The
    Schwab fixture has 8 officers/owners across two pages, so the parser
    must correctly traverse the section continuation."""
    detail = _parse_form_bd_pdf("5393", _fixture_bytes("firm_5393_schwab.pdf"))

    assert len(detail.executive_officers) >= 5
    for officer in detail.executive_officers:
        assert "name" in officer
        assert officer["name"]
        # No officer should be an empty/garbled name (e.g. "(continued)")
        assert not officer["name"].startswith("(continued)")

    # Spot-check the first entry — the corporate parent at 75%+
    first = detail.executive_officers[0]
    assert "SCHWAB HOLDINGS" in first["name"]
    assert first.get("ownership_pct") == "75% or more"


def test_parser_extracts_firm_operations_text() -> None:
    """The clearing classifier downstream reads ``firm_operations_text``
    for the canonical "does/does not hold or maintain funds or
    securities" sentence. Schwab is self-clearing — assert the
    affirmative variant is what we capture."""
    detail = _parse_form_bd_pdf("5393", _fixture_bytes("firm_5393_schwab.pdf"))

    assert detail.firm_operations_text is not None
    assert "hold or maintain funds or securities" in detail.firm_operations_text
    # Negation is absent (Schwab IS self-clearing)
    assert "does not hold" not in detail.firm_operations_text


def test_parser_returns_empty_for_information_not_available_section() -> None:
    """The R H Securities fixture is a 1985 firm whose sections all carry
    "Information not available". The parser must not return prose /
    explanation text — it must return empty lists / None."""
    detail = _parse_form_bd_pdf(
        "10997", _fixture_bytes("firm_10997_rhsecurities.pdf")
    )

    assert detail.crd == "10997"
    assert detail.types_of_business == []
    assert detail.executive_officers == []
    # Operations text is "Informationnotavailable—seeSummaryPage" → None
    assert detail.firm_operations_text is None


def test_parser_web_address_is_none_for_typical_form_bd_pdf() -> None:
    """The Form BD Detailed Report PDF doesn't carry the firm's web
    address (that's a Form ADV thing). Both fixtures should report None
    for ``web_address``. The Apollo fallback is what populates
    ``broker_dealer.website`` for the bulk of firms."""
    schwab = _parse_form_bd_pdf("5393", _fixture_bytes("firm_5393_schwab.pdf"))
    rh = _parse_form_bd_pdf(
        "10997", _fixture_bytes("firm_10997_rhsecurities.pdf")
    )

    assert schwab.web_address is None
    assert rh.web_address is None


def test_parser_returns_frozen_dataclass() -> None:
    """``FormBdDetail`` is frozen so consumers can't mutate fields after
    extraction (would mask bugs in the merge layer). Sanity-assert."""
    detail = _parse_form_bd_pdf("5393", _fixture_bytes("firm_5393_schwab.pdf"))
    with pytest.raises((AttributeError, Exception)):
        detail.crd = "different"  # type: ignore[misc]


# ----- fetch_form_bd_detail: high-level entrypoint -----

async def test_fetch_form_bd_detail_returns_none_on_404() -> None:
    """A 404 from FINRA means the firm has no Detailed Report on file.
    The adapter swallows ``FinraPdfNotFound`` and returns None so the
    caller can leave the record alone instead of treating it as an
    error to mark for review."""
    with patch(
        "app.services.brokercheck_pdf.fetch_brokercheck_pdf",
        side_effect=FinraPdfNotFound("no PDF for CRD 999999"),
    ):
        result = await fetch_form_bd_detail("999999")

    assert result is None


async def test_fetch_form_bd_detail_propagates_fetch_error() -> None:
    """Transient upstream failures (network / 5xx / non-PDF body)
    surface as ``FinraPdfFetchError`` and propagate to the caller —
    which logs + leaves the record untouched. We deliberately don't
    swallow these as None because that would let a transient outage
    look like "firm not on file" and downstream code might react
    differently."""
    with patch(
        "app.services.brokercheck_pdf.fetch_brokercheck_pdf",
        side_effect=FinraPdfFetchError("network: ConnectError"),
    ):
        with pytest.raises(FinraPdfFetchError):
            await fetch_form_bd_detail("123456")


async def test_fetch_form_bd_detail_parses_pdf_when_fetch_succeeds() -> None:
    """End-to-end: when the fetcher returns PDF bytes, the adapter parses
    them through the inline extractor and returns ``FormBdDetail``."""
    pdf_bytes = _fixture_bytes("firm_5393_schwab.pdf")

    with patch(
        "app.services.brokercheck_pdf.fetch_brokercheck_pdf",
        return_value=pdf_bytes,
    ):
        detail = await fetch_form_bd_detail("5393")

    assert detail is not None
    assert isinstance(detail, FormBdDetail)
    assert detail.crd == "5393"
    assert len(detail.types_of_business) == 6
    assert detail.firm_operations_text is not None
