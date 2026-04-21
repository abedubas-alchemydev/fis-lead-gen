"""Hunter provider tests.

All HTTP via respx — no real Hunter calls. Settings are monkey-patched per
test so any real HUNTER_API_KEY in the dev env doesn't leak into the respx
assertions.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core import config
from app.services.email_extractor.hunter import DOMAIN_SEARCH_URL, Hunter


def _set_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(config.settings, "hunter_api_key", value)


def _payload(emails: list[dict[str, object]]) -> dict[str, object]:
    return {"data": {"domain": "example.com", "emails": emails}}


@respx.mock
async def test_happy_path_two_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    {
                        "value": "alice@example.com",
                        "type": "personal",
                        "confidence": 87,
                        "position": "CEO",
                        "verification": {"status": "valid"},
                        "sources": [{"uri": "https://example.com/team"}],
                    },
                    {
                        "value": "info@example.com",
                        "type": "generic",
                        "confidence": 50,
                        "position": None,
                        "verification": None,
                        "sources": [],
                    },
                ]
            ),
        )
    )

    result = await Hunter().run("example.com")
    emails = sorted(d.email for d in result.emails)
    assert emails == ["alice@example.com", "info@example.com"]
    by_email = {d.email: d for d in result.emails}
    assert by_email["alice@example.com"].confidence == 0.87
    assert by_email["info@example.com"].confidence == 0.50
    assert by_email["alice@example.com"].source == "hunter"
    assert "CEO" in (by_email["alice@example.com"].attribution or "")
    assert "verified=valid" in (by_email["alice@example.com"].attribution or "")
    assert "verified=unknown" in (by_email["info@example.com"].attribution or "")
    assert result.errors == []


@respx.mock
async def test_missing_key_skips_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, None)
    route = respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(200, json=_payload([])))

    result = await Hunter().run("example.com")
    assert result.emails == []
    assert result.errors == ["api_key not configured"]
    assert route.call_count == 0


@respx.mock
async def test_empty_string_key_also_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "")
    route = respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(200, json=_payload([])))

    result = await Hunter().run("example.com")
    assert result.errors == ["api_key not configured"]
    assert route.call_count == 0


@respx.mock
async def test_401_invalid_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(401))

    result = await Hunter().run("example.com")
    assert result.emails == []
    assert result.errors == ["invalid api key"]


@respx.mock
async def test_402_out_of_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(402))

    result = await Hunter().run("example.com")
    assert result.errors == ["out of credits"]


@respx.mock
async def test_429_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(429))

    result = await Hunter().run("example.com")
    assert result.errors == ["rate limited"]


@respx.mock
async def test_500_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(return_value=httpx.Response(500))

    result = await Hunter().run("example.com")
    assert result.errors == ["upstream error 500"]


@respx.mock
async def test_timeout_translates_to_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(side_effect=httpx.TimeoutException("read timeout"))

    result = await Hunter().run("example.com")
    assert result.emails == []
    assert result.errors == ["timeout"]


@respx.mock
async def test_missing_confidence_field_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "test-key")
    respx.get(DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_payload([{"value": "noconf@example.com", "type": "generic"}]),
        )
    )

    result = await Hunter().run("example.com")
    assert len(result.emails) == 1
    assert result.emails[0].confidence is None


@respx.mock
async def test_hunter_plan_limit_400_yields_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hunter free tier returns 400 with a `pagination_error` body when limit > plan max."""
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(config.settings, "hunter_limit", 10)
    respx.get(DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "errors": [
                    {
                        "id": "pagination_error",
                        "code": 400,
                        "details": "You're limited to 10 email addresses on your current plan",
                    }
                ]
            },
        )
    )

    result = await Hunter().run("example.com")
    assert result.emails == []
    assert result.errors == ["free-tier plan limit exceeded (configured limit=10)"]


@respx.mock
async def test_hunter_uses_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """`settings.hunter_limit` flows through to the outgoing URL's `limit` query param."""
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(config.settings, "hunter_limit", 5)
    route = respx.get(DOMAIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_payload([{"value": "x@example.com", "confidence": 90, "type": "personal"}]),
        )
    )

    result = await Hunter().run("example.com")
    assert route.called
    request_url = str(respx.calls[0].request.url)
    assert "limit=5" in request_url
    assert len(result.emails) == 1
    assert result.emails[0].email == "x@example.com"
