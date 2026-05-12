"""Unit tests for the multi-MIME text extractor.

PDF / DOCX / PPTX / XLSX paths exercise their per-format libraries
indirectly through the dispatch table — we only assert the lightweight
TXT/MD/HTML/CSV/JSON/RTF paths here so the suite stays fast and
doesn't depend on round-trip fidelity of binary office formats. The
production paths are exercised in the integration smoke after deploy.
"""

from __future__ import annotations

import pytest

from app.services.vault_text_extraction import (
    SUPPORTED_MIME_TYPES,
    VaultExtractionError,
    extract_text,
    is_supported,
)


def test_is_supported_recognizes_canonical_mime_types() -> None:
    assert is_supported("application/pdf")
    assert is_supported("text/plain")
    assert is_supported("text/markdown")
    assert is_supported("APPLICATION/PDF")  # case-insensitive
    assert not is_supported("application/zip")
    assert not is_supported("application/x-msdownload")  # .exe


def test_supported_mime_set_is_frozen() -> None:
    # Defensive check — the set is module-level and we don't want a
    # caller mutating it.
    assert isinstance(SUPPORTED_MIME_TYPES, frozenset)
    assert "application/pdf" in SUPPORTED_MIME_TYPES


def test_plain_text_returned_verbatim() -> None:
    assert extract_text(b"hello world", "text/plain") == "hello world"
    assert extract_text(b"# Title\n\nbody", "text/markdown").startswith("# Title")


def test_html_strips_tags_and_decodes_entities() -> None:
    raw = b"<p>Hello <b>world</b> &amp; cheers</p>"
    assert extract_text(raw, "text/html") == "Hello world & cheers"


def test_html_drops_scripts_and_styles() -> None:
    raw = b"<style>x{}</style><p>visible</p><script>alert(1)</script>"
    out = extract_text(raw, "text/html")
    assert "visible" in out
    assert "alert" not in out
    assert "{}" not in out


def test_csv_emits_pipe_separated_rows() -> None:
    out = extract_text(b"a,b,c\n1,2,3", "text/csv")
    assert "a | b | c" in out
    assert "1 | 2 | 3" in out


def test_json_pretty_prints() -> None:
    import json

    out = extract_text(b'{"k":1,"l":[1,2]}', "application/json")
    assert json.loads(out) == {"k": 1, "l": [1, 2]}
    assert "\n" in out  # indented


def test_invalid_json_raises_extraction_error() -> None:
    with pytest.raises(VaultExtractionError):
        extract_text(b"{not json}", "application/json")


def test_unsupported_mime_raises_extraction_error() -> None:
    with pytest.raises(VaultExtractionError) as excinfo:
        extract_text(b"x", "application/x-msdownload")
    assert "Unsupported MIME" in str(excinfo.value)


def test_empty_input_raises_extraction_error_via_empty_guard() -> None:
    with pytest.raises(VaultExtractionError) as excinfo:
        extract_text(b"", "text/plain")
    assert "empty" in str(excinfo.value).lower()


def test_whitespace_only_input_raises_extraction_error() -> None:
    with pytest.raises(VaultExtractionError):
        extract_text(b"   \n\n  \t", "text/plain")


def test_rtf_extracts_to_plain_text() -> None:
    raw = b"{\\rtf1\\ansi\\deff0 hello rtf}"
    assert "hello rtf" in extract_text(raw, "application/rtf")
