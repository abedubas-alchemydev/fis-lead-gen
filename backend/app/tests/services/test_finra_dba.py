"""Tests for ``FinraBrokerDealerService._parse_dba_names``.

Locks the parser shape so DBA / "doing business as" trade names from
FINRA's ``firm_other_names`` payload field land as a list on
``broker_dealers.dba_names`` (rather than getting shoe-horned into
``business_type`` like the pre-2026-05-07 implementation).

The website resolver downstream uses these DBAs to build a multi-token
anchor set, so a firm registered as ``303 ALTERNATIVES, LLC`` operating
at ``303capitalmarkets.com`` (DBA "303Capital Markets") can still admit
its candidate URL even though the legal-name token doesn't match the
brand domain.
"""

from __future__ import annotations

from app.services.finra import FinraService


_LEGAL = "303 ALTERNATIVES, LLC"


def _parse(raw: object, *, legal: str = _LEGAL) -> list[str] | None:
    return FinraService._parse_dba_names(raw, legal_name=legal)


def test_none_returns_none() -> None:
    assert _parse(None) is None


def test_empty_string_returns_none() -> None:
    assert _parse("") is None
    assert _parse("   ") is None


def test_single_dba_returns_singleton_list() -> None:
    assert _parse("303 CAPITAL MARKETS, LLC") == ["303 CAPITAL MARKETS, LLC"]


def test_semicolon_delimited_splits() -> None:
    assert _parse("FOO LLC; BAR INC") == ["FOO LLC", "BAR INC"]


def test_newline_delimited_splits() -> None:
    assert _parse("FOO LLC\nBAR INC") == ["FOO LLC", "BAR INC"]
    assert _parse("FOO LLC\r\nBAR INC") == ["FOO LLC", "BAR INC"]


def test_dba_prefix_stripped() -> None:
    """``d/b/a`` and ``DBA`` markers prefixing a name are stripped so
    only the trade name itself lands in the list."""
    assert _parse("d/b/a 303Capital Markets") == ["303Capital Markets"]
    assert _parse("DBA Acme Capital") == ["Acme Capital"]
    # Markers are case-insensitive and only stripped at the start.
    assert _parse("D/B/A 303Capital Markets") == ["303Capital Markets"]


def test_legal_name_dedup_dropped() -> None:
    """When FINRA echoes the firm's own legal name into
    ``firm_other_names`` it isn't a DBA — drop it so the resolver's
    token set doesn't have a useless duplicate."""
    assert _parse("303 ALTERNATIVES, LLC") is None
    assert _parse("303 alternatives, llc") is None  # case-insensitive
    assert _parse("  303 ALTERNATIVES, LLC  ") is None  # whitespace-insensitive


def test_dedupe_within_input() -> None:
    assert _parse("FOO LLC; FOO LLC; BAR INC") == ["FOO LLC", "BAR INC"]


def test_comma_in_name_not_split() -> None:
    """Comma inside an LLC suffix is NOT a delimiter — semicolons and
    newlines are the only delimiters. ``ACME, LLC`` stays as a single
    name; if FINRA wants to deliver multiple DBAs it does so via
    semicolons or newlines."""
    assert _parse("ACME, LLC; BETA, INC") == ["ACME, LLC", "BETA, INC"]
    # Comma alone (no semicolon) is treated as part of one name.
    assert _parse("ACME, LLC, BETA, INC") == ["ACME, LLC, BETA, INC"]


def test_list_input_passes_through() -> None:
    """The detail endpoint surfaces ``otherNames`` already as a list of
    strings (one entry per name). Pass-through preserves the list and
    drops the legal-name match + dedupes."""
    assert _parse(["303 ALTERNATIVES, LLC", "303 CAPITAL MARKETS, LLC"]) == [
        "303 CAPITAL MARKETS, LLC"
    ]
    assert _parse(["FOO LLC", "FOO LLC", "BAR INC"]) == ["FOO LLC", "BAR INC"]
    assert _parse([]) is None


# ─────────────────── extract_dba_names_from_detail ────────────────────


def test_extract_dba_from_detail_nested_content() -> None:
    """Real-world detail-endpoint shape (CRD 166675, 303 ALTERNATIVES,
    LLC): DBA names live inside a JSON-encoded ``content`` string at
    ``basicInformation.otherNames``."""
    import json
    detail = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "content": json.dumps({
                            "basicInformation": {
                                "firmName": "303 ALTERNATIVES, LLC",
                                "otherNames": [
                                    "303 ALTERNATIVES, LLC",
                                    "303 CAPITAL MARKETS, LLC",
                                ],
                            },
                        }),
                    },
                },
            ],
        },
    }
    dba = FinraService.extract_dba_names_from_detail(detail, legal_name=_LEGAL)
    assert dba == ["303 CAPITAL MARKETS, LLC"]


def test_extract_dba_from_detail_returns_none_when_path_missing() -> None:
    """Empty / missing / unparseable paths return None cleanly."""
    assert FinraService.extract_dba_names_from_detail(None, legal_name=_LEGAL) is None
    assert FinraService.extract_dba_names_from_detail({}, legal_name=_LEGAL) is None
    # Missing content
    assert FinraService.extract_dba_names_from_detail(
        {"hits": {"hits": [{"_source": {}}]}}, legal_name=_LEGAL,
    ) is None
    # Bad JSON in content
    assert FinraService.extract_dba_names_from_detail(
        {"hits": {"hits": [{"_source": {"content": "not-json"}}]}},
        legal_name=_LEGAL,
    ) is None
    # No otherNames key
    import json
    detail = {
        "hits": {"hits": [{"_source": {"content": json.dumps({"basicInformation": {}})}}]},
    }
    assert FinraService.extract_dba_names_from_detail(detail, legal_name=_LEGAL) is None
