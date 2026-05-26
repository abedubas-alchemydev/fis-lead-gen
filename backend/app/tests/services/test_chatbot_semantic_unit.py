"""Unit tests for ``app.services.chatbot_semantic`` — content building +
hash determinism.

Repo + embedding-API behaviour live in the integration suite
(``test_chatbot_semantic_integration.py``); this file exercises the
pure-functional helpers so the schema-drift risk (renaming a BD column)
is caught on every default pytest run, not just under ``-m integration``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.chatbot_semantic import (
    _build_broker_dealer_content,
    _content_hash,
    _MAX_CONTENT_CHARS,
)


def _bd_stub(**overrides: Any) -> Any:
    """Lightweight stand-in for a BrokerDealer ORM row.

    Mirrors the attributes ``_build_broker_dealer_content`` reads. A
    namespace object is enough — the function never calls ORM-only
    behaviour.
    """
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Acme Securities LLC",
        "city": "New York",
        "state": "NY",
        "status": "active",
        "business_type": "broker",
        "lead_priority": "warm",
        "current_clearing_partner": "Apex Clearing",
        "clearing_classification": "fully_disclosed",
        "types_of_business": ["broker_dealer", "introducing"],
        "firm_operations_text": "Retail-focused introducing BD serving HNW.",
        "dba_names": ["Acme Markets"],
        "registration_date": date(2010, 1, 1),
        "last_filing_date": date(2026, 1, 15),
        "created_at": datetime(2024, 1, 1),
    }
    defaults.update(overrides)

    class _Obj:
        pass

    o = _Obj()
    for k, v in defaults.items():
        setattr(o, k, v)
    return o


def test_build_content_includes_core_identifying_fields() -> None:
    content = _build_broker_dealer_content(_bd_stub())
    # The embedding model latches onto labelled fields, so each must
    # appear by name.
    assert "Firm: Acme Securities LLC" in content
    assert "Acme Markets" in content  # DBA in the firm line
    assert "Location: New York, NY" in content
    assert "Status: active" in content
    assert "Business type: broker" in content
    assert "Lead priority: warm" in content
    assert "Clearing partner: Apex Clearing" in content
    assert "Clearing classification: fully_disclosed" in content
    assert "Lines of business: broker_dealer, introducing" in content
    assert "Operations: Retail-focused" in content


def test_build_content_handles_minimal_bd() -> None:
    """A BD missing most optional fields should still produce a usable
    embedding input — never an empty string."""
    bd = _bd_stub(
        city=None,
        state=None,
        status="",
        business_type=None,
        lead_priority=None,
        current_clearing_partner=None,
        clearing_classification=None,
        types_of_business=None,
        firm_operations_text=None,
        dba_names=None,
    )
    content = _build_broker_dealer_content(bd)
    assert content.startswith("Firm: Acme Securities LLC")
    # Only the name line survives.
    assert "\n" not in content


def test_build_content_truncates_long_firm_operations() -> None:
    long_blob = "x" * (_MAX_CONTENT_CHARS * 2)
    content = _build_broker_dealer_content(
        _bd_stub(firm_operations_text=long_blob)
    )
    assert len(content) <= _MAX_CONTENT_CHARS
    assert content.endswith("…")


def test_build_content_ignores_non_string_aliases() -> None:
    """``dba_names`` is a JSONB array — a stray non-string element
    shouldn't crash the embedder."""
    content = _build_broker_dealer_content(
        _bd_stub(dba_names=["Real Alias", None, 42, "Another Alias"])
    )
    assert "Real Alias" in content
    assert "Another Alias" in content
    # No "None" or "42" should leak in as alias text.
    assert "None" not in content.split("\n", 1)[0]


def test_build_content_caps_alias_list_at_five() -> None:
    """A firm with hundreds of historical names shouldn't fill the whole
    embedding budget with aliases."""
    aliases = [f"alias_{i}" for i in range(20)]
    content = _build_broker_dealer_content(_bd_stub(dba_names=aliases))
    # Only first 5 are surfaced.
    assert "alias_0" in content
    assert "alias_4" in content
    assert "alias_5" not in content


def test_content_hash_is_deterministic() -> None:
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest length.


def test_content_hash_differs_on_input_change() -> None:
    assert _content_hash("a") != _content_hash("b")
