"""Snov.io provider tests.

All HTTP via respx — no real Snov calls. Settings monkey-patched per test for
credentials and limit. The provider's two-step flow (OAuth token fetch + domain
search) means most tests stub both endpoints.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from app.core import config
from app.services.email_extractor.snov import DOMAIN_EMAILS_URL, OAUTH_URL, Snov


def _set_creds(
    monkeypatch: pytest.MonkeyPatch,
    client_id: str | None,
    client_secret: str | None,
) -> None:
    monkeypatch.setattr(config.settings, "snov_client_id", client_id)
    monkeypatch.setattr(config.settings, "snov_client_secret", client_secret)


def _set_limit(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setattr(config.settings, "snov_limit", value)


def _mock_oauth_ok(token: str = "tok-abc") -> respx.Route:
    return respx.post(OAUTH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": token, "expires_in": 3599, "token_type": "Bearer"},
        )
    )


# --- Happy paths -----------------------------------------------------------


@respx.mock
async def test_happy_path_two_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "domain": "stripe.com",
                "emails": [
                    {
                        "email": "patrick@stripe.com",
                        "position": "CEO",
                        "type": "personal",
                        "probability": 92,
                        "status": "verified",
                        "sources": [{"url": "https://stripe.com/team"}],
                    },
                    {
                        "email": "info@stripe.com",
                        "position": None,
                        "type": "generic",
                        "probability": 50,
                        "sources": [],
                    },
                ],
            },
        )
    )

    result = await Snov().run("stripe.com")

    assert result.errors == []
    emails = sorted(d.email for d in result.emails)
    assert emails == ["info@stripe.com", "patrick@stripe.com"]
    by_email = {d.email: d for d in result.emails}
    assert by_email["patrick@stripe.com"].source == "snov"
    assert by_email["patrick@stripe.com"].confidence == 0.92
    assert "verified" in (by_email["patrick@stripe.com"].attribution or "")
    assert "stripe.com/team" in (by_email["patrick@stripe.com"].attribution or "")
    assert by_email["info@stripe.com"].confidence == 0.50


@respx.mock
async def test_free_tier_shape_produces_useful_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snov free tier ships {email, type, status} only — no position/probability/sources.
    The status field carries the useful signal (verified / notVerified)."""
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "emails": [
                    {"email": "ops@example.com", "type": "email", "status": "notVerified"},
                ],
            },
        )
    )

    result = await Snov().run("example.com")

    assert result.errors == []
    assert len(result.emails) == 1
    draft = result.emails[0]
    assert draft.email == "ops@example.com"
    assert draft.confidence is None
    assert draft.attribution is not None
    assert "notVerified" in draft.attribution
    assert "email" in draft.attribution


@respx.mock
async def test_invalid_email_entries_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "emails": [
                    {"email": None, "position": "x"},
                    {"email": 42, "position": "y"},
                    {"email": "", "position": "z"},
                    {"email": "no-at-sign", "position": "a"},
                    {"email": "valid@example.com", "position": "valid"},
                    "not-a-dict",
                ],
            },
        )
    )

    result = await Snov().run("stripe.com")

    assert result.errors == []
    assert [d.email for d in result.emails] == ["valid@example.com"]


# --- Credentials guard -----------------------------------------------------


@respx.mock
async def test_missing_credentials_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, None, "secret-1")
    _set_limit(monkeypatch, 100)
    oauth_route = _mock_oauth_ok()
    search_route = respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, json={}))

    result = await Snov().run("stripe.com")

    assert result.errors == ["credentials not configured"]
    assert oauth_route.call_count == 0
    assert search_route.call_count == 0


# --- OAuth error branches --------------------------------------------------


@respx.mock
async def test_oauth_invalid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(return_value=httpx.Response(401))
    search_route = respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, json={}))

    result = await Snov().run("stripe.com")

    assert result.errors == ["oauth: invalid credentials"]
    assert search_route.call_count == 0


@respx.mock
async def test_oauth_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(side_effect=httpx.TimeoutException("read timeout"))

    result = await Snov().run("stripe.com")

    assert result.errors == ["oauth: timeout"]


@respx.mock
async def test_oauth_no_access_token_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(return_value=httpx.Response(200, json={}))

    result = await Snov().run("stripe.com")

    assert result.errors == ["oauth: no access_token in response"]


@respx.mock
async def test_oauth_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(side_effect=httpx.ConnectError("mock"))

    result = await Snov().run("stripe.com")

    assert result.errors == ["oauth: ConnectError"]


# --- Search error branches -------------------------------------------------


@respx.mock
async def test_search_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(401))

    result = await Snov().run("stripe.com")

    assert result.errors == ["search: token rejected"]


@respx.mock
async def test_search_out_of_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(402))

    result = await Snov().run("stripe.com")

    assert result.errors == ["search: out of credits"]


@respx.mock
async def test_search_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(429))

    result = await Snov().run("stripe.com")

    assert result.errors == ["search: rate limited"]


@respx.mock
async def test_search_success_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "invalid_domain"})
    )

    result = await Snov().run("stripe.com")

    assert result.errors == ["search failed: invalid_domain"]


@respx.mock
async def test_search_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, content=b"not json{"))

    result = await Snov().run("stripe.com")

    assert result.errors == ["search: invalid json"]


@respx.mock
async def test_search_payload_not_a_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, json=["array", "at", "top"]))

    result = await Snov().run("stripe.com")

    assert result.errors == ["payload not a dict"]


# --- ADR 0002 contract: bare errors across every error fixture -------------


def _setup_missing_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, None, "secret-1")
    _set_limit(monkeypatch, 100)


def _setup_oauth_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(return_value=httpx.Response(401))


def _setup_oauth_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(side_effect=httpx.TimeoutException("timeout"))


def _setup_oauth_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(return_value=httpx.Response(200, json={}))


def _setup_oauth_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    respx.post(OAUTH_URL).mock(side_effect=httpx.ConnectError("mock"))


def _setup_search_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(401))


def _setup_search_out_of_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(402))


def _setup_search_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(429))


def _setup_search_success_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "invalid_domain"})
    )


def _setup_search_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, content=b"not json{"))


def _setup_search_payload_not_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_creds(monkeypatch, "id-1", "secret-1")
    _set_limit(monkeypatch, 100)
    _mock_oauth_ok()
    respx.get(DOMAIN_EMAILS_URL).mock(return_value=httpx.Response(200, json=["a", "b"]))


@pytest.mark.parametrize(
    "setup_fn",
    [
        _setup_missing_creds,
        _setup_oauth_invalid,
        _setup_oauth_timeout,
        _setup_oauth_no_token,
        _setup_oauth_generic,
        _setup_search_token_rejected,
        _setup_search_out_of_credits,
        _setup_search_rate_limited,
        _setup_search_success_false,
        _setup_search_invalid_json,
        _setup_search_payload_not_dict,
    ],
)
@respx.mock
async def test_bare_error_contract(
    monkeypatch: pytest.MonkeyPatch,
    setup_fn: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """ADR 0002: no error string from run() may start with 'snov:' or 'snov '."""
    setup_fn(monkeypatch)
    result = await Snov().run("stripe.com")
    assert len(result.errors) >= 1
    for err in result.errors:
        assert not err.lower().startswith("snov:"), f"bare-error contract violated: {err!r}"
        assert not err.lower().startswith("snov "), f"bare-error contract violated: {err!r}"
