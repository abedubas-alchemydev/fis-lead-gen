"""Unit tests for ``check_smtp`` — never hits real SMTP.

The library boundary is ``_smtp_validate_email`` (re-exported from
``py3-validate-email`` as ``validate_email``); we monkeypatch it on the
``verification`` module so the wrapper's mapping logic is exercised without
any network I/O.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core import config
from app.models.email_verification import SmtpStatus
from app.services.email_extractor import verification


def _patch_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: object | type[BaseException] | BaseException,
) -> dict[str, Any]:
    """Replace ``_smtp_validate_email`` with a fake. Captures kwargs the helper
    was called with for later assertions. If ``return_value`` is an exception
    class or instance, the fake raises it; otherwise it returns it.
    """
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> object:
        captured.update(kwargs)
        if isinstance(return_value, type) and issubclass(return_value, BaseException):
            raise return_value("mock")
        if isinstance(return_value, BaseException):
            raise return_value
        return return_value

    monkeypatch.setattr(verification, "_smtp_validate_email", fake)
    return captured


async def test_deliverable_maps_to_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, return_value=True)
    smtp_status, message = await verification.check_smtp("alice@example.com")
    assert smtp_status is SmtpStatus.deliverable
    assert message is None


async def test_undeliverable_maps_to_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, return_value=False)
    smtp_status, message = await verification.check_smtp("ghost@example.com")
    assert smtp_status is SmtpStatus.undeliverable
    assert message is None


async def test_inconclusive_maps_to_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, return_value=None)
    smtp_status, message = await verification.check_smtp("greylist@example.com")
    assert smtp_status is SmtpStatus.inconclusive
    assert message is None


async def test_exception_maps_to_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, return_value=ConnectionRefusedError)
    smtp_status, message = await verification.check_smtp("rejected@example.com")
    assert smtp_status is SmtpStatus.blocked
    assert message == "ConnectionRefusedError"


async def test_exception_message_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error message must not start with 'smtp:' or 'verification:' — bare per ADR 0002 spirit."""
    _patch_probe(monkeypatch, return_value=ConnectionRefusedError)
    _, message = await verification.check_smtp("rejected@example.com")
    assert message is not None
    assert not message.lower().startswith("smtp:")
    assert not message.lower().startswith("verification:")


async def test_timeout_exception_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, return_value=TimeoutError)
    smtp_status, message = await verification.check_smtp("slow@example.com")
    assert smtp_status is SmtpStatus.blocked
    assert message == "TimeoutError"


async def test_respects_config_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "smtp_verify_timeout_seconds", 42)
    captured = _patch_probe(monkeypatch, return_value=True)
    await verification.check_smtp("alice@example.com")
    assert captured["smtp_timeout"] == 42
    assert captured["dns_timeout"] == 42
