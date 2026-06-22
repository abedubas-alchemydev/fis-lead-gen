"""Unit tests for the fast/free pdfplumber text extractor.

Two tiers, all offline and deterministic:

* Pure helpers (``_parse_dollar_amount``, ``_extract_net_capital_from_text``,
  ``_extract_contact_from_text``) — exercised directly with synthetic
  strings, no mocks.
* ``extract_from_pdf`` routing branches — ``pdfplumber.open`` is
  monkeypatched to a fake context manager so no real PDF is ever read or
  downloaded, mirroring the pdfplumber-patching style in
  ``test_pdf_processor.py``.

No HTTP happens in this module, so ``respx`` is intentionally not used;
``monkeypatch`` is sufficient.

Every expected value below was confirmed against the real functions. One
is pinned as a *current-behavior* assertion (annotated inline): the amount
parser substitutes OCR ``l``/``I`` -> ``1`` mid-string.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from app.services import pdf_text_extractor as extractor_module
from app.services.pdf_text_extractor import (
    PdfTextExtractionResult,
    _extract_contact_from_text,
    _extract_net_capital_from_text,
    _parse_dollar_amount,
    extract_from_pdf,
)


# ---------------------------------------------------------------------------
# _parse_dollar_amount
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$24,860", 24860.0),
        ("$ 1,234,567.89", 1234567.89),
        ("(3,501,687)", -3501687.0),       # parens -> negative
        ("$1,000.", 1000.0),               # trailing dot cleaned off
    ],
)
def test_parse_dollar_amount_valid(raw: str, expected: float) -> None:
    assert _parse_dollar_amount(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$2l,000", 21000.0),  # lowercase OCR 'l' -> '1'
        ("$1I0", 110.0),       # uppercase OCR 'I' -> '1'
    ],
)
def test_parse_dollar_amount_ocr_digit_substitution(raw: str, expected: float) -> None:
    # NOTE pins current behavior: the parser blanket-replaces l/I with 1
    # anywhere in the string to recover from common OCR confusions.
    assert _parse_dollar_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "$", "abc", "()", "$()"])
def test_parse_dollar_amount_junk_returns_none(raw: str) -> None:
    assert _parse_dollar_amount(raw) is None


def test_parse_dollar_amount_unparseable_float_returns_none() -> None:
    # Survives the non-digit strip as "1.2.3" but float() raises ValueError,
    # which the helper swallows into None (covers the except branch).
    assert _parse_dollar_amount("$1.2.3") is None


# ---------------------------------------------------------------------------
# _extract_net_capital_from_text
# ---------------------------------------------------------------------------


def test_net_capital_narrative_pattern() -> None:
    text = "As of year end the Company had net capital of $24,860 in excess."
    net, excess = _extract_net_capital_from_text(text)
    assert net == 24860.0


def test_net_capital_adjusted_narrative_pattern() -> None:
    text = "The firm has adjusted net capital of $1,250,000 at the report date."
    net, _excess = _extract_net_capital_from_text(text)
    assert net == 1250000.0


def test_net_capital_schedule_fallback_when_no_narrative() -> None:
    # No narrative phrasing -> the COMPUTATION-section schedule fallback
    # must find the final "Net Capital" line and skip "before haircuts".
    text = (
        "COMPUTATION OF NET CAPITAL\n"
        "Total stockholders' equity 500,000\n"
        "Net Capital before haircuts $30,000\n"
        "Net Capital $24,860\n"
    )
    net, _excess = _extract_net_capital_from_text(text)
    assert net == 24860.0


def test_net_capital_schedule_skips_before_and_requirement_lines() -> None:
    # Only disallowed variants present -> no clean "Net Capital" value.
    text = (
        "COMPUTATION OF NET CAPITAL\n"
        "Net Capital before haircuts $30,000\n"
        "Net Capital requirement $5,000\n"
    )
    net, _excess = _extract_net_capital_from_text(text)
    assert net is None


def test_net_capital_excess_narrative_form_matches() -> None:
    # The "in excess of ... required net capital of $N" form is the branch
    # of _EXCESS_NET_CAPITAL that actually fires.
    text = (
        "The Company had net capital of $44,860, which was in excess of the "
        "required net capital of $19,860."
    )
    net, excess = _extract_net_capital_from_text(text)
    assert net == 44860.0
    assert excess == 19860.0


def test_net_capital_bare_excess_label_matches() -> None:
    # The bare "Excess Net Capital $N" schedule label is captured: the first
    # alternative of _EXCESS_NET_CAPITAL tolerates the whitespace before the
    # amount, so the "$N" form is read just like the narrative form.
    text = "Excess Net Capital $19,860"
    _net, excess = _extract_net_capital_from_text(text)
    assert excess == 19860.0


def test_net_capital_absent_returns_none_pair() -> None:
    text = "This filing contains no net-capital figures whatsoever."
    assert _extract_net_capital_from_text(text) == (None, None)


# ---------------------------------------------------------------------------
# _extract_contact_from_text
# ---------------------------------------------------------------------------


def test_contact_all_three_fields_on_one_line() -> None:
    text = (
        "FACING PAGE\n"
        "NAME AND TELEPHONE NUMBER OF PERSON TO CONTACT IN REGARD TO THIS FILING\n"
        "John Smith (212) 555-1234 john.smith@example.com\n"
        "FOR OFFICIAL USE ONLY\n"
    )
    name, phone, email = _extract_contact_from_text(text)
    assert name == "John Smith"
    assert phone == "(212) 555-1234"
    assert email == "john.smith@example.com"


def test_contact_phone_and_email_from_nearby_line_scan() -> None:
    # Name alone on the line after the header; phone/email land on following
    # lines and are recovered by the nearby-line fallback scan.
    text = (
        "PERSON TO CONTACT IN REGARD TO THIS FILING\n"
        "Jane Doe\n"
        "Phone: 415-555-9876\n"
        "Email: jane.doe@firm.com\n"
    )
    name, phone, email = _extract_contact_from_text(text)
    assert name == "Jane Doe"
    assert phone == "415-555-9876"
    assert email == "jane.doe@firm.com"


def test_contact_skips_parenthetical_name_label_line() -> None:
    # The "(Name and telephone)" form-label line directly under the header is
    # skipped; the real contact data on the following line is used instead.
    text = (
        "PERSON TO CONTACT IN REGARD TO THIS FILING\n"
        "(Name and telephone)\n"
        "Bob Jones 312-555-7000 bob@firm.com\n"
    )
    name, phone, email = _extract_contact_from_text(text)
    assert name == "Bob Jones"
    assert phone == "312-555-7000"
    assert email == "bob@firm.com"


def test_contact_recovers_only_missing_phone_via_nearby_scan() -> None:
    # Email is captured on the header-adjacent line; phone is absent there
    # and recovered from a nearby line by the partial-fallback scan.
    text = (
        "PERSON TO CONTACT IN REGARD TO THIS FILING\n"
        "Alice Roe alice@firm.com\n"
        "Direct: 646-555-3000\n"
    )
    name, phone, email = _extract_contact_from_text(text)
    assert name == "Alice Roe"
    assert phone == "646-555-3000"
    assert email == "alice@firm.com"


def test_contact_absent_header_returns_all_none() -> None:
    text = "Some unrelated document text with no contact header anywhere.\n"
    assert _extract_contact_from_text(text) == (None, None, None)


# ---------------------------------------------------------------------------
# extract_from_pdf — routing branches (pdfplumber.open monkeypatched)
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        return self._text


class _FakePdf:
    """Context-manager stand-in for the object pdfplumber.open returns."""

    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _patch_open(monkeypatch: pytest.MonkeyPatch, pages_or_exc) -> None:
    """Patch ``pdfplumber.open`` (imported inside extract_from_pdf).

    ``pages_or_exc`` is either a list of _FakePage (returned as a _FakePdf
    context manager) or an Exception instance (raised on open).
    """

    def _fake_open(_path):
        if isinstance(pages_or_exc, Exception):
            raise pages_or_exc
        return _FakePdf(pages_or_exc)

    monkeypatch.setattr(pdfplumber, "open", _fake_open)


def test_extract_from_pdf_missing_path_returns_empty_result() -> None:
    # Guaranteed-missing path -> early return, pdfplumber never touched.
    result = extract_from_pdf(Path("Z:/definitely/missing/focus-report.pdf"))
    assert isinstance(result, PdfTextExtractionResult)
    assert result.success is False
    assert result.contact_name is None
    assert result.net_capital is None


def test_extract_from_pdf_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the existence check past the early return, then feed a fake page
    # with > 100 chars carrying both a contact block and a net-capital line.
    monkeypatch.setattr(extractor_module.Path, "exists", lambda _self: True)

    page_text = (
        "FACING PAGE OF FORM X-17A-5\n"
        "NAME AND TELEPHONE NUMBER OF PERSON TO CONTACT IN REGARD TO THIS FILING\n"
        "John Smith (212) 555-1234 john.smith@example.com\n"
        "COMPUTATION OF NET CAPITAL\n"
        "The Company had net capital of $24,860 at the report date.\n"
        "Additional padding text to comfortably exceed the 100-character floor.\n"
    )
    _patch_open(monkeypatch, [_FakePage(page_text)])

    result = extract_from_pdf("/fake/path/report.pdf")

    assert result.success is True
    assert result.contact_name == "John Smith"
    assert result.contact_phone == "(212) 555-1234"
    assert result.contact_email == "john.smith@example.com"
    assert result.net_capital == 24860.0


def test_extract_from_pdf_zero_page_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extractor_module.Path, "exists", lambda _self: True)
    _patch_open(monkeypatch, [])  # no pages -> total text < 100 chars

    result = extract_from_pdf("/fake/path/scanned.pdf")

    # Scanned-image guard: under-the-floor text yields the default empty
    # result. Preserve this "no data found" semantic.
    assert result.success is False
    assert result.contact_name is None
    assert result.net_capital is None


def test_extract_from_pdf_short_text_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extractor_module.Path, "exists", lambda _self: True)
    _patch_open(monkeypatch, [_FakePage("too short")])  # < 100 chars

    result = extract_from_pdf("/fake/path/thin.pdf")
    assert result.success is False


def test_extract_from_pdf_page_returns_none_uses_or_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # extract_text() -> None must not raise (the ``or ""`` guard). With no
    # usable text the result is the empty/failure default.
    monkeypatch.setattr(extractor_module.Path, "exists", lambda _self: True)
    _patch_open(monkeypatch, [_FakePage(None)])

    result = extract_from_pdf("/fake/path/none-text.pdf")
    assert result.success is False


def test_extract_from_pdf_open_raises_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pdfplumber failure is caught and converted to an empty result — it
    # must NOT propagate to the caller.
    monkeypatch.setattr(extractor_module.Path, "exists", lambda _self: True)
    _patch_open(monkeypatch, RuntimeError("corrupt PDF stream"))

    result = extract_from_pdf("/fake/path/broken.pdf")
    assert isinstance(result, PdfTextExtractionResult)
    assert result.success is False
    assert result.contact_name is None
    assert result.net_capital is None
