"""Schema-level tests for the email-extractor request models.

Pydantic-only; no DB. The mutex validator on ``ScanCreateRequest`` is the
only piece of new logic that lives in the schema layer, so it's worth a
unit-level test independent of the integration-marked round-trip suite
(which requires Docker + Postgres).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.email_extractor import ScanCreateRequest


def test_scan_create_request_accepts_bd_id_only() -> None:
    req = ScanCreateRequest(domain="example.com", bd_id=42)
    assert req.bd_id == 42
    assert req.advisor_id is None


def test_scan_create_request_accepts_advisor_id_only() -> None:
    req = ScanCreateRequest(domain="example.com", advisor_id=7)
    assert req.advisor_id == 7
    assert req.bd_id is None


def test_scan_create_request_accepts_neither() -> None:
    """Standalone /email-extractor scans omit both FKs — must remain valid."""
    req = ScanCreateRequest(domain="example.com")
    assert req.bd_id is None
    assert req.advisor_id is None


def test_scan_create_request_rejects_both_bd_and_advisor_id() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ScanCreateRequest(domain="example.com", bd_id=1, advisor_id=2)
    assert "mutually exclusive" in str(exc_info.value)
