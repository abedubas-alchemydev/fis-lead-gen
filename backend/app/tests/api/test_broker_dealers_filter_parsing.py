"""Regression tests for master-list query-param parsing on ``GET /broker-dealers``.

Bug: the endpoint piped ``types_of_business`` through ``_parse_states``, which
splits every value on commas (correct for short enums like ``state=NY,CA`` but
wrong here). FINRA type-of-business labels legitimately contain commas, e.g.
"Broker or dealer involved in a networking, kiosk or similar arrangment with a:
bank, savings bank or association, or ...". Splitting fragmented the label and
the JSONB ``?|`` filter matched nothing, so selecting such a type returned zero
rows (35 of 39 comma-containing options were fully broken on staging).

These drive the real route through the ASGI app with auth + db dependencies
overridden and ``repository.list_broker_dealers`` mocked, asserting the value
reaches the service intact (one element, commas preserved). No database
required, so they run in the default (non-integration) suite.
"""

from __future__ import annotations

from datetime import datetime

import httpx

import app.api.v1.endpoints.broker_dealers as bd_endpoints
from app.db.session import get_db_session
from app.main import app
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import BrokerDealerListMeta, BrokerDealerListResponse
from app.services.auth import get_current_user

# A real FINRA Form BD "Types of Business" category that contains commas — the
# exact shape that ``_parse_states`` used to shatter.
COMMA_TYPE = (
    "Broker or dealer involved in a networking, kiosk or similar arrangment "
    "with a: bank, savings bank or association, or credit union"
)


def _override_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="test-user",
        name="Test User",
        email="test-user@example.com",
        role="viewer",
        feature_permissions=["master_list"],
        session_expires_at=datetime(2099, 1, 1),
    )


def _empty_response() -> BrokerDealerListResponse:
    return BrokerDealerListResponse(
        items=[],
        meta=BrokerDealerListMeta(page=1, limit=25, total=0, total_pages=0),
    )


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _call_with_captured_kwargs(
    monkeypatch, params: dict | list
) -> dict[str, object]:
    """GET /broker-dealers with ``params``; return the kwargs the endpoint
    handed to ``repository.list_broker_dealers``."""
    captured: dict[str, object] = {}

    async def _capture(_db, **kwargs):
        captured.update(kwargs)
        return _empty_response()

    monkeypatch.setattr(bd_endpoints.repository, "list_broker_dealers", _capture)
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db_session] = lambda: None
    try:
        async with _client() as client:
            response = await client.get("/api/v1/broker-dealers", params=params)
        assert response.status_code == 200, response.text
        return captured
    finally:
        app.dependency_overrides.clear()


async def test_comma_containing_type_reaches_service_unsplit(monkeypatch) -> None:
    """A single comma-laden type must arrive as one intact element."""
    captured = await _call_with_captured_kwargs(
        monkeypatch, {"types_of_business": COMMA_TYPE}
    )
    assert captured["types_of_business"] == [COMMA_TYPE], (
        "comma-containing type must reach the repository as a single intact "
        f"value, not split on commas; got {captured.get('types_of_business')!r}"
    )


async def test_multiple_types_each_pass_through_intact(monkeypatch) -> None:
    """Repeated params (multi-select) are preserved 1:1, commas and all."""
    captured = await _call_with_captured_kwargs(
        monkeypatch,
        # httpx serialises a list value into repeated query params, exactly
        # like the FE's URLSearchParams.append does.
        [("types_of_business", "Mutual fund retailer"), ("types_of_business", COMMA_TYPE)],
    )
    assert captured["types_of_business"] == ["Mutual fund retailer", COMMA_TYPE]


async def test_blank_types_are_dropped(monkeypatch) -> None:
    """Empty / whitespace-only entries are filtered out (parity with the old
    ``_parse_states`` behaviour) without disturbing real values."""
    captured = await _call_with_captured_kwargs(
        monkeypatch,
        [("types_of_business", ""), ("types_of_business", "   "), ("types_of_business", COMMA_TYPE)],
    )
    assert captured["types_of_business"] == [COMMA_TYPE]


async def test_state_filter_still_comma_splits(monkeypatch) -> None:
    """Guard the other direction: short enum params like ``state`` must keep
    the comma-join convention so ``state=NY,CA`` still expands to two states."""
    captured = await _call_with_captured_kwargs(monkeypatch, {"state": "NY,CA"})
    assert captured["states"] == ["NY", "CA"]
