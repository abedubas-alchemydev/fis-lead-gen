"""Verification: syntax + MX lookup is offloaded to a thread.

Tests monkey-patch ``email_validator.validate_email`` so DNS isn't actually
hit — keeps the suite hermetic.
"""

from __future__ import annotations

import pytest
from email_validator import EmailSyntaxError, EmailUndeliverableError

from app.services.email_extractor import verification


async def test_valid_syntax_and_mx_returns_all_true(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ok(email: str, **_: object) -> object:
        return object()

    monkeypatch.setattr(verification, "validate_email", _ok)

    syntax, mx, err = await verification.check_syntax_and_mx("user@example.com")
    assert (syntax, mx, err) == (True, True, None)


async def test_invalid_syntax_returns_syntax_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad_syntax(email: str, **_: object) -> object:
        raise EmailSyntaxError("not a valid local part")

    monkeypatch.setattr(verification, "validate_email", _bad_syntax)

    syntax, mx, err = await verification.check_syntax_and_mx("not-an-email")
    assert syntax is False
    assert mx is False
    assert err is not None and "valid local part" in err


async def test_mx_lookup_failure_keeps_syntax_true(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_mx(email: str, **_: object) -> object:
        raise EmailUndeliverableError("the domain does not exist")

    monkeypatch.setattr(verification, "validate_email", _no_mx)

    syntax, mx, err = await verification.check_syntax_and_mx("user@nonexistent.invalid")
    assert syntax is True
    assert mx is False
    assert err is not None and "domain does not exist" in err
