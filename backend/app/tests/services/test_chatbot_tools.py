"""Unit tests for ``app.services.chatbot_tools``.

Repo dependencies are stubbed via ``monkeypatch.setattr`` on the module-
level ``_bd_repo`` / ``_ia_repo`` singletons, so the AsyncSession argument
is purely ceremonial (the stubs ignore it). This keeps the suite fast and
keeps the assertions focused on the tool wrapper's responsibilities:
auth gating, argument validation + clamping, projection shape, and the
never-raise error-dict contract.

The schema-drift guard at the bottom asserts every projected key exists as
a real attribute on the underlying Pydantic schema; that catches an
accidental rename on ``BrokerDealerListItem`` / ``InvestmentAdvisorListItem``
before it manifests as a broken Doxie reply in production.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.feature_permissions import (
    ALERTS,
    EMAIL_EXTRACTOR,
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    INVESTORS,
    MASTER_LIST,
    MY_FAVORITES,
    SENT_OUTREACH,
    VAULT,
)
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import BrokerDealerListItem
from app.schemas.institutional_investor import InstitutionalInvestorListItem
from app.schemas.investment_advisor import InvestmentAdvisorListItem
from app.services import chatbot_tools


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_user(*, role: str = "viewer", features: list[str] | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"tool-test-{secrets.token_hex(4)}",
        name="Tool Tester",
        email="tools-test@example.com",
        role=role,
        feature_permissions=features or [],
        session_expires_at=datetime(2099, 1, 1),
    )


@pytest.fixture
def bd_user() -> AuthenticatedUser:
    return _make_user(features=[MASTER_LIST])


@pytest.fixture
def ia_user() -> AuthenticatedUser:
    return _make_user(features=[INVESTMENT_ADVISORS])


@pytest.fixture
def ii_user() -> AuthenticatedUser:
    return _make_user(features=[INSTITUTIONAL_INVESTORS])


@pytest.fixture
def investors_user() -> AuthenticatedUser:
    return _make_user(features=[INVESTORS])


@pytest.fixture
def alerts_user() -> AuthenticatedUser:
    return _make_user(features=[ALERTS])


@pytest.fixture
def vault_user() -> AuthenticatedUser:
    return _make_user(features=[VAULT])


@pytest.fixture
def outreach_user() -> AuthenticatedUser:
    return _make_user(features=[SENT_OUTREACH])


@pytest.fixture
def favorites_user() -> AuthenticatedUser:
    return _make_user(features=[MY_FAVORITES])


@pytest.fixture
def no_access_user() -> AuthenticatedUser:
    return _make_user(features=[])


@pytest.fixture
def db_stub() -> object:
    """Sentinel passed through to the (mocked) repo. Never actually touched."""
    return object()


@dataclass
class _ListMetaStub:
    total: int
    page: int = 1
    limit: int = 5
    total_pages: int = 1
    pipeline_refreshed_at: datetime | None = None


@dataclass
class _ListResponseStub:
    items: list[Any]
    meta: _ListMetaStub


def _make_bd_orm(**overrides: Any) -> Any:
    """Plain object with attrs that satisfy BrokerDealerListItem.model_validate.

    A SimpleNamespace-style shim is simpler than spinning up an ORM instance
    just to feed Pydantic's ``from_attributes`` path.
    """
    defaults: dict[str, Any] = {
        "id": 42,
        "cik": "0001234567",
        "crd_number": "12345",
        "sec_file_number": "8-99999",
        "name": "Acme Securities LLC",
        "city": "New York",
        "state": "NY",
        "status": "active",
        "branch_count": 3,
        "business_type": "broker",
        "registration_date": date(2010, 1, 1),
        "matched_source": "finra",
        "last_filing_date": date(2026, 1, 15),
        "filings_index_url": "https://example.com/edgar",
        "required_min_capital": Decimal("250000"),
        "latest_net_capital": Decimal("1500000"),
        "latest_excess_net_capital": Decimal("1250000"),
        "latest_total_assets": Decimal("5000000"),
        "yoy_growth": 0.12,
        "three_year_cagr": 0.08,
        "health_status": "ok",
        "is_deficient": False,
        "latest_deficiency_filed_at": None,
        "lead_score": 0.72,
        "lead_priority": "warm",
        "current_clearing_partner": "Apex Clearing",
        "current_clearing_type": "fully_disclosed",
        "current_clearing_is_competitor": False,
        "current_clearing_source_filing_url": None,
        "current_clearing_extraction_confidence": 0.9,
        "last_audit_report_date": None,
        "website": "acme.example.com",
        "website_source": "finra",
        "types_of_business": ["broker_dealer"],
        "direct_owners": None,
        "executive_officers": None,
        "firm_operations_text": None,
        "clearing_classification": "fully_disclosed",
        "clearing_raw_text": None,
        "is_niche_restricted": False,
        "formation_date": None,
        "total_assets_yoy": 0.10,
        "types_of_business_total": 1,
        "types_of_business_other": None,
        "dba_names": None,
        "last_enrich_attempt_at": None,
        "created_at": datetime(2024, 1, 1),
        "member_agencies": [],
        "clearing_membership_checked_at": None,
        "current_clearing_unknown_reason": None,
        "financial_unknown_reason": None,
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_ii_orm(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": 88,
        "cik": "0001112223",
        "advisor_id": None,
        "name": "Gamma Capital Management",
        "legal_name": "Gamma Capital Management LP",
        "city": "Greenwich",
        "state": "CT",
        "status": "active",
        "matched_source": "edgar",
        "website": "gamma.example.com",
        "website_source": "finra",
        "latest_13f_filing_date": date(2026, 2, 15),
        "total_aum": Decimal("12000000000"),
        "holdings_count": 250,
        "filings_index_url": "https://example.com/edgar/gamma",
        "last_enrich_attempt_at": None,
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 6, 1),
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_ia_orm(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": 77,
        "cik": "0007654321",
        "crd_number": "98765",
        "sec_file_number": "801-12345",
        "name": "Beta Advisors LP",
        "legal_name": "Beta Advisors L.P.",
        "city": "Boston",
        "state": "MA",
        "status": "active",
        "matched_source": "iapd",
        "registration_date": date(2012, 6, 15),
        "formation_date": date(2011, 1, 1),
        "last_filing_date": date(2026, 3, 31),
        "filings_index_url": "https://example.com/iapd",
        "website": "beta.example.com",
        "website_source": "finra",
        "regulatory_aum": Decimal("8000000000"),
        "discretionary_aum": Decimal("7500000000"),
        "non_discretionary_aum": Decimal("500000000"),
        "total_clients": 142,
        "advisory_activities": ["portfolio_management"],
        "client_types": ["pooled_investment_vehicles"],
        "client_counts": None,
        "direct_owners": None,
        "indirect_owners": None,
        "executive_officers": None,
        "firm_operations_text": None,
        "files_13f": True,
        "latest_13f_filing_date": date(2026, 2, 15),
        "last_enrich_attempt_at": None,
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 6, 1),
        "member_agencies": [],
        "clearing_membership_checked_at": None,
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ── _jsonable helper ────────────────────────────────────────────────────


def test_jsonable_coerces_decimal_date_and_datetime() -> None:
    out = chatbot_tools._jsonable(
        {
            "amount": Decimal("1.50"),
            "as_of": date(2026, 5, 1),
            "stamp": datetime(2026, 5, 1, 12, 30),
            "nested": [Decimal("2"), {"d": date(2025, 1, 1)}],
            "plain": "x",
        }
    )
    assert out == {
        "amount": 1.5,
        "as_of": "2026-05-01",
        "stamp": "2026-05-01T12:30:00",
        "nested": [2.0, {"d": "2025-01-01"}],
        "plain": "x",
    }


# ── search_broker_dealers ───────────────────────────────────────────────


class TestSearchBrokerDealers:
    async def test_happy_path_returns_projected_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        orm_row = _make_bd_orm(name="Apex Clearing Corporation")
        list_item = BrokerDealerListItem.model_validate(orm_row)
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "list_broker_dealers",
            AsyncMock(return_value=_ListResponseStub(items=[list_item], meta=_ListMetaStub(total=1))),
        )

        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        result = await tool.execute(bd_user, db_stub, {"query": "Apex"})

        assert result["total_matched"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        # Summary keys all present (plus the new ``link`` field added in
        # Part A — checked separately below).
        assert set(chatbot_tools._BD_SUMMARY_KEYS).issubset(item.keys())
        assert item["name"] == "Apex Clearing Corporation"
        # Decimal must have been coerced to float by _jsonable.
        assert isinstance(item["latest_net_capital"], float)
        # Deep-link to the firm detail page (Part A).
        assert item["link"] == f"/master-list/{item['id']}"
        # Wrapping response gets a list-link that re-applies the same query.
        assert result["list_link"] == "/master-list?q=Apex"

    async def test_403_returns_no_access_dict_does_not_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        # Repo must not be called.
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", called)

        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        result = await tool.execute(no_access_user, db_stub, {"query": "anything"})

        assert result["error"] == "no_access"
        assert MASTER_LIST in result["message"]
        called.assert_not_called()

    async def test_empty_query_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        result = await tool.execute(bd_user, db_stub, {"query": "  "})
        assert result["error"] == "invalid_args"

    async def test_missing_query_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        result = await tool.execute(bd_user, db_stub, {})
        assert result["error"] == "invalid_args"

    async def test_limit_is_clamped_to_max(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", fake_list)

        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        await tool.execute(bd_user, db_stub, {"query": "Acme", "limit": 999})
        assert captured["limit"] == chatbot_tools.SEARCH_RESULT_LIMIT_MAX

        captured.clear()
        await tool.execute(bd_user, db_stub, {"query": "Acme", "limit": 0})
        assert captured["limit"] == 1

    async def test_repo_exception_returns_tool_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def boom(_db: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("repo blew up")

        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", boom)

        tool = chatbot_tools.TOOL_REGISTRY["search_broker_dealers"]
        result = await tool.execute(bd_user, db_stub, {"query": "Acme"})
        assert result["error"] == "tool_error"


# ── get_broker_dealer_profile ───────────────────────────────────────────


class TestGetBrokerDealerProfile:
    async def test_happy_path_includes_financials_and_arrangements(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        orm_bd = _make_bd_orm()

        class _FinOrm:
            def __init__(self, **kw: Any) -> None:
                self.id = 1
                self.bd_id = 42
                self.report_date = date(2026, 3, 31)
                self.net_capital = Decimal("1500000")
                self.excess_net_capital = Decimal("1250000")
                self.total_assets = Decimal("5000000")
                self.required_min_capital = Decimal("250000")
                self.source_filing_url = None
                self.extraction_status = "parsed"
                self.created_at = datetime(2024, 1, 1)
                self.unknown_reason = None
                self.__dict__.update(kw)

        class _ArrOrm:
            def __init__(self, **kw: Any) -> None:
                self.id = 1
                self.bd_id = 42
                self.filing_year = 2025
                self.report_date = date(2025, 12, 31)
                self.source_filing_url = None
                self.source_pdf_url = None
                self.clearing_partner = "Apex"
                self.clearing_type = "fully_disclosed"
                self.agreement_date = None
                self.extraction_confidence = 0.9
                self.extraction_status = "parsed"
                self.extraction_notes = None
                self.is_competitor = False
                self.is_verified = False
                self.extracted_at = datetime(2025, 12, 31)
                self.created_at = datetime(2025, 12, 31)
                self.unknown_reason = None
                self.__dict__.update(kw)

        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=orm_bd),
        )
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_financial_metrics",
            AsyncMock(return_value=[_FinOrm(), _FinOrm(), _FinOrm(), _FinOrm()]),
        )
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "list_clearing_arrangements",
            AsyncMock(return_value=[_ArrOrm(filing_year=2024), _ArrOrm(filing_year=2023)]),
        )

        tool = chatbot_tools.TOOL_REGISTRY["get_broker_dealer_profile"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})

        # Summary + profile-extra keys all present.
        for k in (*chatbot_tools._BD_SUMMARY_KEYS, *chatbot_tools._BD_PROFILE_EXTRA_KEYS):
            assert k in result
        # Top-level deep-link to this firm (Part A).
        assert result["link"] == "/master-list/42"
        # Financials capped at PROFILE_FINANCIALS_LIMIT (3) even though 4 returned.
        assert len(result["latest_financials"]) == chatbot_tools.PROFILE_FINANCIALS_LIMIT
        assert len(result["clearing_arrangements"]) == 2
        # All Decimals/dates JSON-friendly.
        f = result["latest_financials"][0]
        assert isinstance(f["net_capital"], float)
        assert isinstance(f["report_date"], str)

    async def test_not_found_returns_structured_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["get_broker_dealer_profile"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 9999})
        assert result["error"] == "not_found"

    async def test_invalid_id_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_broker_dealer_profile"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": "abc"})
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._bd_repo, "get_broker_dealer", called)

        tool = chatbot_tools.TOOL_REGISTRY["get_broker_dealer_profile"]
        result = await tool.execute(no_access_user, db_stub, {"broker_dealer_id": 42})
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── search_investment_advisors ──────────────────────────────────────────


class TestSearchInvestmentAdvisors:
    async def test_happy_path_uses_files_13f_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", fake_list)

        tool = chatbot_tools.TOOL_REGISTRY["search_investment_advisors"]
        await tool.execute(ia_user, db_stub, {"query": "Vanguard"})

        # files_13f=None disables the hard 13F scope the master-list
        # endpoint defaults to — search should reach every advisor.
        assert captured["files_13f"] is None
        assert captured["search"] == "Vanguard"

    async def test_returns_summary_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        item = InvestmentAdvisorListItem.model_validate(_make_ia_orm())
        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "list_investment_advisors",
            AsyncMock(return_value=_ListResponseStub(items=[item], meta=_ListMetaStub(total=1))),
        )
        tool = chatbot_tools.TOOL_REGISTRY["search_investment_advisors"]
        result = await tool.execute(ia_user, db_stub, {"query": "Beta"})
        item = result["items"][0]
        assert set(chatbot_tools._IA_SUMMARY_KEYS).issubset(item.keys())
        # Deep-link to the advisor detail page (Part A).
        assert item["link"] == f"/advisor-list/{item['id']}"
        # The search tool disables the hard 13F scope, so the link does
        # too via ``files_13f=all`` — otherwise the FE would silently
        # re-filter the user's destination view.
        assert result["list_link"] == "/advisor-list?q=Beta&files_13f=all"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", called)
        tool = chatbot_tools.TOOL_REGISTRY["search_investment_advisors"]
        result = await tool.execute(no_access_user, db_stub, {"query": "Beta"})
        assert result["error"] == "no_access"
        called.assert_not_called()

    async def test_repo_exception_returns_tool_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def boom(_db: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", boom)
        tool = chatbot_tools.TOOL_REGISTRY["search_investment_advisors"]
        result = await tool.execute(ia_user, db_stub, {"query": "Beta"})
        assert result["error"] == "tool_error"


# ── get_investment_advisor_profile ──────────────────────────────────────


class TestGetInvestmentAdvisorProfile:
    async def test_happy_path_caps_advisory_and_client_lists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        long_activities = [f"activity_{i}" for i in range(50)]
        long_client_types = [f"client_{i}" for i in range(50)]
        orm = _make_ia_orm(
            advisory_activities=long_activities,
            client_types=long_client_types,
        )
        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "get_investment_advisor",
            AsyncMock(return_value=orm),
        )
        tool = chatbot_tools.TOOL_REGISTRY["get_investment_advisor_profile"]
        result = await tool.execute(ia_user, db_stub, {"advisor_id": 77})

        for k in (*chatbot_tools._IA_SUMMARY_KEYS, *chatbot_tools._IA_PROFILE_EXTRA_KEYS):
            assert k in result
        assert result["link"] == "/advisor-list/77"
        assert len(result["advisory_activities"]) == chatbot_tools.ADVISORY_LIST_CAP
        assert len(result["client_types"]) == chatbot_tools.CLIENT_TYPE_LIST_CAP

    async def test_not_found_returns_structured_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "get_investment_advisor",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["get_investment_advisor_profile"]
        result = await tool.execute(ia_user, db_stub, {"advisor_id": 9999})
        assert result["error"] == "not_found"

    async def test_invalid_id_returns_invalid_args(
        self,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_investment_advisor_profile"]
        result = await tool.execute(ia_user, db_stub, {"advisor_id": None})
        assert result["error"] == "invalid_args"


# ── Admin bypass ────────────────────────────────────────────────────────


# ── semantic_firm_search ────────────────────────────────────────────────


class TestSemanticFirmSearch:
    async def test_happy_path_returns_hits_with_similarity_and_snippet(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from app.services.chatbot_semantic import (
            ENTITY_TYPE_BROKER_DEALER,
            SemanticSearchHit,
        )

        async def fake_search(
            _db: Any,
            *,
            query: str,
            entity_types: Any,
            limit: int,
        ) -> list[Any]:
            assert query == "small introducing brokers"
            assert list(entity_types) == [ENTITY_TYPE_BROKER_DEALER]
            return [
                SemanticSearchHit(
                    entity_type=ENTITY_TYPE_BROKER_DEALER,
                    entity_id=42,
                    content="Firm: Acme Securities LLC\nLocation: NYC, NY",
                    similarity=0.91,
                )
            ]

        monkeypatch.setattr(chatbot_tools._semantic_service, "search", fake_search)
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm(id=42, name="Acme Securities LLC")),
        )

        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(
            bd_user, db_stub, {"query": "small introducing brokers"}
        )

        assert result["total_matched"] == 1
        item = result["items"][0]
        assert item["id"] == 42
        assert item["name"] == "Acme Securities LLC"
        assert item["similarity"] == 0.91
        assert "Acme Securities LLC" in item["match_snippet"]
        # Each hit carries a per-firm deep-link; the wrapper carries a
        # list-link that deep-links to exactly the cited firms by id (a
        # name/q search can't reproduce an embedding result set).
        assert item["link"] == "/master-list/42"
        assert result["list_link"] == "/master-list?ids=42"

    async def test_empty_hits_returns_helpful_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def fake_search(_db: Any, **_kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(chatbot_tools._semantic_service, "search", fake_search)
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(bd_user, db_stub, {"query": "something niche"})
        assert result["items"] == []
        assert result["total_matched"] == 0
        # Surfaces an explanation so Doxie doesn't just say "nothing
        # found" when the real issue is that the index isn't populated.
        assert "index" in result["note"].lower()

    async def test_stale_hit_pointing_at_missing_bd_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """An embedding row pointing at a deleted BD shouldn't crash the
        tool — it just drops the stale hit and returns the remaining
        ones."""
        from app.services.chatbot_semantic import (
            ENTITY_TYPE_BROKER_DEALER,
            SemanticSearchHit,
        )

        async def fake_search(_db: Any, **_kwargs: Any) -> list[Any]:
            return [
                SemanticSearchHit(
                    entity_type=ENTITY_TYPE_BROKER_DEALER,
                    entity_id=999,
                    content="stale",
                    similarity=0.5,
                ),
                SemanticSearchHit(
                    entity_type=ENTITY_TYPE_BROKER_DEALER,
                    entity_id=42,
                    content="ok",
                    similarity=0.4,
                ),
            ]

        async def fake_get_bd(_db: Any, bd_id: int) -> Any:
            if bd_id == 999:
                return None
            return _make_bd_orm(id=42)

        monkeypatch.setattr(chatbot_tools._semantic_service, "search", fake_search)
        monkeypatch.setattr(
            chatbot_tools._bd_repo, "get_broker_dealer", fake_get_bd
        )

        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(bd_user, db_stub, {"query": "anything"})
        # Only the live BD makes it into items, but the stale hit still
        # counts toward candidates_considered so Doxie can mention
        # truncation.
        assert [it["id"] for it in result["items"]] == [42]
        assert result["candidates_considered"] == 2
        # The deep-link only cites firms that actually rendered — the stale
        # id 999 is excluded, so the user never lands on a missing firm.
        assert result["list_link"] == "/master-list?ids=42"

    async def test_empty_query_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(bd_user, db_stub, {"query": ""})
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access_does_not_call_service(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        sentinel = AsyncMock()
        monkeypatch.setattr(chatbot_tools._semantic_service, "search", sentinel)
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(no_access_user, db_stub, {"query": "x"})
        assert result["error"] == "no_access"
        sentinel.assert_not_called()

    async def test_service_exception_returns_tool_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def boom(_db: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("index unhealthy")

        monkeypatch.setattr(chatbot_tools._semantic_service, "search", boom)
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(bd_user, db_stub, {"query": "x"})
        assert result["error"] == "tool_error"
        # Surfaces the "may not be populated yet" hint so Doxie
        # can fall back to a name-based search.
        assert "populated" in result["message"].lower()

    async def test_limit_clamped_to_semantic_max(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_search(_db: Any, **kwargs: Any) -> list[Any]:
            captured.update(kwargs)
            return []

        monkeypatch.setattr(chatbot_tools._semantic_service, "search", fake_search)
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        await tool.execute(bd_user, db_stub, {"query": "x", "limit": 9999})
        assert captured["limit"] == chatbot_tools.SEMANTIC_RESULT_LIMIT_MAX

    async def test_rejects_bad_entity_type(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(
            bd_user, db_stub, {"query": "x", "entity_type": "hedge_fund"}
        )
        assert result["error"] == "invalid_args"

    async def test_entity_types_scoped_to_permissions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Default 'any' searches only the registries the caller can see."""
        from app.services.chatbot_semantic import (
            ENTITY_TYPE_BROKER_DEALER,
            ENTITY_TYPE_INVESTMENT_ADVISOR,
        )

        captured: dict[str, Any] = {}

        async def fake_search(_db: Any, **kwargs: Any) -> list[Any]:
            captured.update(kwargs)
            return []

        monkeypatch.setattr(
            chatbot_tools._semantic_service, "search", fake_search
        )
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]

        await tool.execute(bd_user, db_stub, {"query": "retail firms"})
        assert list(captured["entity_types"]) == [ENTITY_TYPE_BROKER_DEALER]

        await tool.execute(ia_user, db_stub, {"query": "retail firms"})
        assert list(captured["entity_types"]) == [
            ENTITY_TYPE_INVESTMENT_ADVISOR
        ]

    async def test_entity_type_filter_intersects_permissions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """A MASTER_LIST-only user explicitly asking for advisors is denied
        without the service ever being called."""
        sentinel = AsyncMock()
        monkeypatch.setattr(chatbot_tools._semantic_service, "search", sentinel)
        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(
            bd_user,
            db_stub,
            {"query": "x", "entity_type": "investment_advisor"},
        )
        assert result["error"] == "no_access"
        sentinel.assert_not_called()

    async def test_mixed_hits_project_both_types_without_list_link(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_stub: object,
    ) -> None:
        """BD + IA hits each project with their own detail link; the BD
        id-set list_link is omitted because the IA list has no ids param."""
        from app.services.chatbot_semantic import (
            ENTITY_TYPE_BROKER_DEALER,
            ENTITY_TYPE_INVESTMENT_ADVISOR,
            SemanticSearchHit,
        )

        admin = _make_user(role="admin", features=[])
        hits = [
            SemanticSearchHit(
                entity_type=ENTITY_TYPE_BROKER_DEALER,
                entity_id=42,
                content="bd summary",
                similarity=0.91,
            ),
            SemanticSearchHit(
                entity_type=ENTITY_TYPE_INVESTMENT_ADVISOR,
                entity_id=77,
                content="ia summary",
                similarity=0.88,
            ),
        ]
        monkeypatch.setattr(
            chatbot_tools._semantic_service,
            "search",
            AsyncMock(return_value=hits),
        )
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm(id=42)),
        )
        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "get_investment_advisor",
            AsyncMock(return_value=_make_ia_orm(id=77)),
        )

        tool = chatbot_tools.TOOL_REGISTRY["semantic_firm_search"]
        result = await tool.execute(admin, db_stub, {"query": "anything"})

        assert [it["entity_type"] for it in result["items"]] == [
            ENTITY_TYPE_BROKER_DEALER,
            ENTITY_TYPE_INVESTMENT_ADVISOR,
        ]
        assert result["items"][0]["link"] == "/master-list/42"
        assert result["items"][1]["link"] == "/advisor-list/77"
        assert "list_link" not in result
        assert result["candidates_considered"] == 2


async def test_admin_bypasses_feature_gate_on_every_tool(
    monkeypatch: pytest.MonkeyPatch,
    db_stub: object,
) -> None:
    """Admins implicitly bypass ``ensure_feature``; tools should reach the repo
    for every name in the registry without 403'ing."""
    admin = _make_user(role="admin", features=[])

    monkeypatch.setattr(
        chatbot_tools._bd_repo,
        "list_broker_dealers",
        AsyncMock(return_value=_ListResponseStub(items=[], meta=_ListMetaStub(total=0))),
    )
    monkeypatch.setattr(
        chatbot_tools._bd_repo,
        "get_broker_dealer",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chatbot_tools._ia_repo,
        "list_investment_advisors",
        AsyncMock(return_value=_ListResponseStub(items=[], meta=_ListMetaStub(total=0))),
    )
    monkeypatch.setattr(
        chatbot_tools._ia_repo,
        "get_investment_advisor",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chatbot_tools._ii_repo,
        "list_institutional_investors",
        AsyncMock(return_value=_ListResponseStub(items=[], meta=_ListMetaStub(total=0))),
    )
    monkeypatch.setattr(
        chatbot_tools._ii_repo,
        "get_institutional_investor",
        AsyncMock(return_value=None),
    )

    async def fake_search(_db: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(chatbot_tools._semantic_service, "search", fake_search)

    # Stubs for the Part B additions (Form 4 / filings / alerts). Each
    # returns a benign empty result so the admin-bypass path reaches
    # the projection rather than crashing on a missing repo.
    async def fake_form4_list(
        _db: Any, **_kwargs: Any
    ) -> tuple[list[Any], int]:
        return [], 0

    monkeypatch.setattr(
        chatbot_tools._form4_repo, "list_consolidated_persons", fake_form4_list
    )
    monkeypatch.setattr(
        chatbot_tools._alerts_repo,
        "get_filing_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        chatbot_tools._ia_repo,
        "list_advisor_filings",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        chatbot_tools._ii_repo,
        "list_investor_filings",
        AsyncMock(return_value=[]),
    )
    # AlertRepository.list_alerts returns an AlertListResponse; for the
    # admin smoke we don't care about the shape, just that no_access
    # isn't returned.
    class _AlertsMetaStub:
        page = 1
        limit = 6
        total = 0
        total_pages = 1

    class _AlertsRespStub:
        items: list[Any] = []
        meta = _AlertsMetaStub()

    monkeypatch.setattr(
        chatbot_tools._alerts_repo,
        "list_alerts",
        AsyncMock(return_value=_AlertsRespStub()),
    )

    # Each call should reach a non-403 outcome.
    r1 = await chatbot_tools.TOOL_REGISTRY["search_broker_dealers"].execute(
        admin, db_stub, {"query": "x"}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["get_broker_dealer_profile"].execute(
        admin, db_stub, {"broker_dealer_id": 1}
    )
    r3 = await chatbot_tools.TOOL_REGISTRY["search_investment_advisors"].execute(
        admin, db_stub, {"query": "x"}
    )
    r4 = await chatbot_tools.TOOL_REGISTRY["get_investment_advisor_profile"].execute(
        admin, db_stub, {"advisor_id": 1}
    )
    r5 = await chatbot_tools.TOOL_REGISTRY["search_institutional_investors"].execute(
        admin, db_stub, {"query": "x"}
    )
    r6 = await chatbot_tools.TOOL_REGISTRY[
        "get_institutional_investor_profile"
    ].execute(admin, db_stub, {"investor_id": 1})
    r7 = await chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"].execute(
        admin, db_stub, {"state": "NY"}
    )
    r8 = await chatbot_tools.TOOL_REGISTRY[
        "list_investment_advisors_by_filter"
    ].execute(admin, db_stub, {"state": "NY"})
    r9 = await chatbot_tools.TOOL_REGISTRY["semantic_firm_search"].execute(
        admin, db_stub, {"query": "firms like Acme"}
    )
    r10 = await chatbot_tools.TOOL_REGISTRY["search_form4_filings"].execute(
        admin, db_stub, {"query": "John Smith"}
    )
    r11 = await chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"].execute(
        admin, db_stub, {"firm_type": "bd", "firm_id": 1}
    )
    r12 = await chatbot_tools.TOOL_REGISTRY["get_recent_alerts"].execute(
        admin, db_stub, {}
    )
    for r in (r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12):
        assert r.get("error") != "no_access"


# ── Action tools (write-capable) ────────────────────────────────────────
#
# These tests cover the gate + arg-validation paths only — both short-circuit
# before any DB access, so the ``db_stub`` sentinel is never touched. The
# happy / not-found paths hit the live session and belong to integration.


async def test_run_email_extractor_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["run_email_extractor"].execute(
        no_access_user, db_stub, {"domain": "acme.com"}
    )
    assert result["error"] == "no_access"


async def test_run_email_extractor_rejects_missing_domain(db_stub) -> None:
    user = _make_user(features=[EMAIL_EXTRACTOR])
    result = await chatbot_tools.TOOL_REGISTRY["run_email_extractor"].execute(
        user, db_stub, {}
    )
    assert result["error"] == "invalid_args"


async def test_draft_outreach_email_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["draft_outreach_email"].execute(
        no_access_user,
        db_stub,
        {"broker_dealer_id": 1, "contact_id": 2, "folder_id": 3},
    )
    assert result["error"] == "no_access"


async def test_draft_outreach_email_rejects_bad_ids(vault_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["draft_outreach_email"].execute(
        vault_user, db_stub, {"broker_dealer_id": "not-an-int"}
    )
    assert result["error"] == "invalid_args"


async def test_draft_outreach_email_rejects_bad_entity_type(
    vault_user, db_stub
) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["draft_outreach_email"].execute(
        vault_user,
        db_stub,
        {"entity_type": "hedge_fund", "firm_id": 1, "contact_id": 2, "folder_id": 3},
    )
    assert result["error"] == "invalid_args"


async def test_draft_outreach_email_requires_some_firm_id(
    vault_user, db_stub
) -> None:
    """Neither firm_id nor the broker_dealer_id alias → invalid_args."""
    result = await chatbot_tools.TOOL_REGISTRY["draft_outreach_email"].execute(
        vault_user, db_stub, {"contact_id": 2, "folder_id": 3}
    )
    assert result["error"] == "invalid_args"


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _QueuedDb:
    """Fake AsyncSession: each execute() pops the next queued scalar."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)

    async def execute(self, stmt: Any) -> _ScalarResult:  # noqa: ARG002
        return _ScalarResult(self._results.pop(0))


async def test_draft_outreach_email_ia_branch_builds_advisor_context(
    vault_user, monkeypatch
) -> None:
    """The investment_advisor branch mirrors POST /outreach/advisor-draft:
    no clearing partner, and operations text falls back to the Form ADV
    advisory-activities blurb."""
    folder = SimpleNamespace(
        id=3, name="Custody Services", description="", outreach_instructions=""
    )
    advisor = SimpleNamespace(
        name="Acme Advisors",
        city="Austin",
        state="TX",
        firm_operations_text=None,
        advisory_activities=["Portfolio management"],
    )
    contact = SimpleNamespace(
        name="Sarah Lee", title="COO", email="sarah@acme.com"
    )
    fake_db = _QueuedDb([folder, advisor, contact])

    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(subject="Subj", body="Body")

    monkeypatch.setattr(chatbot_tools, "generate_outreach_draft", _fake_generate)
    monkeypatch.setattr(
        chatbot_tools, "retrieve_chunks", AsyncMock(return_value=[])
    )

    result = await chatbot_tools.TOOL_REGISTRY["draft_outreach_email"].execute(
        vault_user,
        fake_db,
        {
            "entity_type": "investment_advisor",
            "firm_id": 9,
            "contact_id": 2,
            "folder_id": 3,
        },
    )

    assert result.get("error") is None
    assert result["entity_type"] == "investment_advisor"
    assert result["to_email"] == "sarah@acme.com"
    assert result["subject"] == "Subj"
    firm_ctx = captured["firm"]
    assert firm_ctx.current_clearing_partner is None
    assert firm_ctx.firm_operations_text == (
        "Advisory activities: Portfolio management"
    )


# ── Outreach copilot tools ──────────────────────────────────────────────


async def test_list_firm_contacts_rejects_bad_entity_type(
    bd_user, db_stub
) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["list_firm_contacts"].execute(
        bd_user, db_stub, {"entity_type": "fund", "firm_id": 1}
    )
    assert result["error"] == "invalid_args"


async def test_list_firm_contacts_gates_per_entity(bd_user, ia_user, db_stub) -> None:
    """A MASTER_LIST-only user can't list advisor contacts, and vice versa."""
    r1 = await chatbot_tools.TOOL_REGISTRY["list_firm_contacts"].execute(
        bd_user, db_stub, {"entity_type": "investment_advisor", "firm_id": 1}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["list_firm_contacts"].execute(
        ia_user, db_stub, {"entity_type": "broker_dealer", "firm_id": 1}
    )
    assert r1["error"] == "no_access"
    assert r2["error"] == "no_access"


async def test_list_firm_contacts_rejects_bad_firm_id(bd_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["list_firm_contacts"].execute(
        bd_user, db_stub, {"entity_type": "broker_dealer", "firm_id": "x"}
    )
    assert result["error"] == "invalid_args"


async def test_save_outreach_draft_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["save_outreach_draft"].execute(
        no_access_user,
        db_stub,
        {"subject": "s", "body": "b", "to_email": "a@b.com"},
    )
    assert result["error"] == "no_access"


async def test_save_outreach_draft_rejects_missing_fields(
    outreach_user, db_stub
) -> None:
    r1 = await chatbot_tools.TOOL_REGISTRY["save_outreach_draft"].execute(
        outreach_user, db_stub, {}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["save_outreach_draft"].execute(
        outreach_user,
        db_stub,
        {"subject": "s", "body": "b", "to_email": "not-an-address"},
    )
    assert r1["error"] == "invalid_args"
    assert r2["error"] == "invalid_args"


async def test_save_outreach_draft_rejects_bad_folder_id(
    outreach_user, db_stub
) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["save_outreach_draft"].execute(
        outreach_user,
        db_stub,
        {"subject": "s", "body": "b", "to_email": "a@b.com", "folder_id": "x"},
    )
    assert result["error"] == "invalid_args"


async def test_list_outreach_drafts_requires_feature(
    no_access_user, db_stub
) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["list_outreach_drafts"].execute(
        no_access_user, db_stub, {}
    )
    assert result["error"] == "no_access"


async def test_get_outreach_draft_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["get_outreach_draft"].execute(
        no_access_user, db_stub, {"draft_id": 1}
    )
    assert result["error"] == "no_access"


async def test_get_outreach_draft_rejects_bad_id(outreach_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["get_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": "x"}
    )
    assert result["error"] == "invalid_args"


def _draft_stub(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": 11,
        "user_id": "u",
        "subject": "Quick intro",
        "body": "Hi,\n\nValue.\n\n- Me",
        "to_recipients": [{"email": "to@example.com", "name": "Toni"}],
        "cc_emails": ["cc@example.com"],
        "bcc_emails": None,
        "folder_id": None,
        "sender_account_id": None,
        "source": "doxie",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_send_outreach_draft_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        no_access_user, db_stub, {"draft_id": 1, "confirm": True}
    )
    assert result["error"] == "no_access"


async def test_send_outreach_draft_rejects_bad_id(outreach_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": "x", "confirm": True}
    )
    assert result["error"] == "invalid_args"


async def test_send_outreach_draft_requires_confirmation(
    outreach_user, db_stub, monkeypatch
) -> None:
    """confirm absent or falsy → refused BEFORE the draft is even loaded."""
    loader = AsyncMock()
    monkeypatch.setattr(chatbot_tools, "_load_owned_draft_row", loader)

    r1 = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11, "confirm": False}
    )
    r3 = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11, "confirm": "yes"}
    )

    assert r1["error"] == "confirmation_required"
    assert r2["error"] == "confirmation_required"
    # Truthy-but-not-boolean must not count as consent.
    assert r3["error"] == "confirmation_required"
    assert loader.await_count == 0


async def test_send_outreach_draft_unknown_draft(
    outreach_user, db_stub, monkeypatch
) -> None:
    monkeypatch.setattr(
        chatbot_tools, "_load_owned_draft_row", AsyncMock(return_value=None)
    )
    result = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 404, "confirm": True}
    )
    assert result["error"] == "not_found"


async def test_send_outreach_draft_incomplete_draft(
    outreach_user, db_stub, monkeypatch
) -> None:
    for stub in (
        _draft_stub(subject=""),
        _draft_stub(body="  "),
        _draft_stub(to_recipients=[]),
        _draft_stub(to_recipients=[{"email": "not-an-address"}]),
    ):
        monkeypatch.setattr(
            chatbot_tools, "_load_owned_draft_row", AsyncMock(return_value=stub)
        )
        result = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
            outreach_user, db_stub, {"draft_id": 11, "confirm": True}
        )
        assert result["error"] == "draft_incomplete"


async def test_send_outreach_draft_sends_exactly_the_saved_draft(
    outreach_user, db_stub, monkeypatch
) -> None:
    """The invariant behind the draft_id indirection: what goes to the
    provider is byte-for-byte the saved draft — subject, body, and the
    full recipient set."""
    stub = _draft_stub()
    monkeypatch.setattr(
        chatbot_tools, "_load_owned_draft_row", AsyncMock(return_value=stub)
    )
    monkeypatch.setattr(
        chatbot_tools,
        "resolve_sender_account",
        AsyncMock(return_value=SimpleNamespace(provider_id="google", id="acct-1")),
    )
    sender = AsyncMock(return_value=SimpleNamespace(id=77))
    monkeypatch.setattr(chatbot_tools, "provider_send_and_record", sender)

    result = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11, "confirm": True}
    )

    sender.assert_awaited_once()
    kwargs = sender.await_args.kwargs
    assert kwargs["subject"] == stub.subject
    assert kwargs["body"] == stub.body
    assert kwargs["to_emails"] == ["to@example.com"]
    assert kwargs["cc_emails"] == ["cc@example.com"]
    assert kwargs["bcc_emails"] is None
    audit = kwargs["audit"]
    assert audit.recipient_email == "to@example.com"
    assert audit.recipient_name == "Toni"
    assert result["sent"] is True
    assert result["send_id"] == 77
    assert result["subject"] == stub.subject
    # db_stub can't run the DELETE — the tool must degrade, not fail.
    assert result["draft_deleted"] is False


async def test_send_outreach_draft_maps_provider_412s(
    outreach_user, db_stub, monkeypatch
) -> None:
    monkeypatch.setattr(
        chatbot_tools,
        "_load_owned_draft_row",
        AsyncMock(return_value=_draft_stub()),
    )

    monkeypatch.setattr(
        chatbot_tools,
        "resolve_sender_account",
        AsyncMock(side_effect=HTTPException(412, "google_account_not_linked")),
    )
    r1 = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11, "confirm": True}
    )

    monkeypatch.setattr(
        chatbot_tools,
        "resolve_sender_account",
        AsyncMock(side_effect=HTTPException(412, "gmail_scope_required")),
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["send_outreach_draft"].execute(
        outreach_user, db_stub, {"draft_id": 11, "confirm": True}
    )

    assert r1["error"] == "no_linked_account"
    assert r2["error"] == "send_permission_needed"


# ── Status & quick-action tools ─────────────────────────────────────────


async def test_list_email_scans_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["list_email_scans"].execute(
        no_access_user, db_stub, {}
    )
    assert result["error"] == "no_access"


async def test_get_email_scan_results_requires_feature(
    no_access_user, db_stub
) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["get_email_scan_results"].execute(
        no_access_user, db_stub, {"scan_id": 1}
    )
    assert result["error"] == "no_access"


async def test_get_email_scan_results_rejects_bad_id(db_stub) -> None:
    user = _make_user(features=[EMAIL_EXTRACTOR])
    result = await chatbot_tools.TOOL_REGISTRY["get_email_scan_results"].execute(
        user, db_stub, {"scan_id": "x"}
    )
    assert result["error"] == "invalid_args"


def test_stall_threshold_tiers() -> None:
    """populate_all / initial_load get hours; the reaped refresh family
    inherits STALE_REFRESH_RUN_AGE; watchers get the flat hour."""
    from datetime import timedelta

    from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE

    assert chatbot_tools._stall_threshold("populate_all") == timedelta(hours=2)
    assert chatbot_tools._stall_threshold("initial_load_advisors") == timedelta(
        hours=2
    )
    assert (
        chatbot_tools._stall_threshold("broker_dealer_refresh_all")
        == STALE_REFRESH_RUN_AGE
    )
    assert (
        chatbot_tools._stall_threshold("investment_advisor_gap_fill")
        == STALE_REFRESH_RUN_AGE
    )
    assert chatbot_tools._stall_threshold("registration_watcher") == timedelta(
        hours=1
    )


async def test_get_data_freshness_has_no_permission_gate(
    no_access_user, db_stub
) -> None:
    """Informational tool — a zero-permission user is never denied. (The
    db stub can't run the query, so the result is a tool_error — the
    assertion is only that the gate didn't fire.)"""
    result = await chatbot_tools.TOOL_REGISTRY["get_data_freshness"].execute(
        no_access_user, db_stub, {}
    )
    assert result.get("error") != "no_access"


async def test_favorite_firm_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["favorite_firm"].execute(
        no_access_user, db_stub, {"entity_type": "broker_dealer", "firm_id": 1}
    )
    assert result["error"] == "no_access"


async def test_favorite_firm_rejects_bad_args(favorites_user, db_stub) -> None:
    r1 = await chatbot_tools.TOOL_REGISTRY["favorite_firm"].execute(
        favorites_user, db_stub, {"entity_type": "fund", "firm_id": 1}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["favorite_firm"].execute(
        favorites_user, db_stub, {"entity_type": "broker_dealer", "firm_id": "x"}
    )
    assert r1["error"] == "invalid_args"
    assert r2["error"] == "invalid_args"


async def test_favorite_firm_bd_calls_user_lists(
    favorites_user, monkeypatch
) -> None:
    fake_db = _QueuedDb(["Acme Securities LLC"])
    add = AsyncMock()
    monkeypatch.setattr(chatbot_tools.user_lists, "add_favorite", add)

    result = await chatbot_tools.TOOL_REGISTRY["favorite_firm"].execute(
        favorites_user, fake_db, {"entity_type": "broker_dealer", "firm_id": 7}
    )

    add.assert_awaited_once_with(fake_db, favorites_user.id, 7)
    assert result["favorited"] is True
    assert result["firm_name"] == "Acme Securities LLC"
    assert result["link"] == "/my-favorites"


async def test_unfavorite_firm_ia_calls_advisor_mirror(
    favorites_user, monkeypatch
) -> None:
    fake_db = _QueuedDb(["Beta Advisors LP"])
    remove = AsyncMock()
    monkeypatch.setattr(
        chatbot_tools.user_lists, "remove_advisor_favorite", remove
    )

    result = await chatbot_tools.TOOL_REGISTRY["unfavorite_firm"].execute(
        favorites_user,
        fake_db,
        {"entity_type": "investment_advisor", "firm_id": 9},
    )

    remove.assert_awaited_once_with(fake_db, favorites_user.id, 9)
    assert result["unfavorited"] is True


async def test_favorite_firm_unknown_firm(favorites_user) -> None:
    fake_db = _QueuedDb([None])
    result = await chatbot_tools.TOOL_REGISTRY["favorite_firm"].execute(
        favorites_user, fake_db, {"entity_type": "broker_dealer", "firm_id": 404}
    )
    assert result["error"] == "not_found"


async def test_list_my_favorites_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["list_my_favorites"].execute(
        no_access_user, db_stub, {}
    )
    assert result["error"] == "no_access"


async def test_mark_alerts_read_requires_feature(no_access_user, db_stub) -> None:
    result = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        no_access_user, db_stub, {"all": True}
    )
    assert result["error"] == "no_access"


async def test_mark_alerts_read_requires_exactly_one_mode(
    alerts_user, db_stub
) -> None:
    r1 = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        alerts_user, db_stub, {}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        alerts_user, db_stub, {"alert_ids": [1], "all": True}
    )
    r3 = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        alerts_user, db_stub, {"alert_ids": []}
    )
    assert r1["error"] == "invalid_args"
    assert r2["error"] == "invalid_args"
    assert r3["error"] == "invalid_args"


async def test_mark_alerts_read_by_ids_counts_and_reports_missing(
    alerts_user, db_stub, monkeypatch
) -> None:
    async def _fake_mark(db, alert_id, *, is_read=True):  # noqa: ARG001
        return SimpleNamespace(id=alert_id) if alert_id != 99 else None

    repo = SimpleNamespace(
        mark_alert_read=_fake_mark, mark_all_read=AsyncMock()
    )
    monkeypatch.setattr(chatbot_tools, "_alerts_repo", repo)

    result = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        alerts_user, db_stub, {"alert_ids": [1, 2, 99]}
    )

    assert result["updated_count"] == 2
    assert result["not_found_ids"] == [99]
    assert "shared" in result["note"]


async def test_mark_alerts_read_all_uses_repo_bulk(
    alerts_user, db_stub, monkeypatch
) -> None:
    repo = SimpleNamespace(
        mark_alert_read=AsyncMock(), mark_all_read=AsyncMock(return_value=5)
    )
    monkeypatch.setattr(chatbot_tools, "_alerts_repo", repo)

    result = await chatbot_tools.TOOL_REGISTRY["mark_alerts_read"].execute(
        alerts_user, db_stub, {"all": True}
    )

    repo.mark_all_read.assert_awaited_once()
    assert result["updated_count"] == 5


def test_normalize_contact_domain() -> None:
    assert (
        chatbot_tools._normalize_contact_domain("https://www.Acme.com/about")
        == "acme.com"
    )
    assert chatbot_tools._normalize_contact_domain("acme.com:8080") == "acme.com"
    assert chatbot_tools._normalize_contact_domain("  ACME.com ") == "acme.com"


async def test_find_contact_by_email_rejects_bad_address(db_stub) -> None:
    user = _make_user(features=[])
    result = await chatbot_tools.TOOL_REGISTRY["find_contact_by_email"].execute(
        user, db_stub, {"email": "not-an-address"}
    )
    assert result["error"] == "invalid_args"


async def test_find_contacts_by_domain_rejects_bad_domain(db_stub) -> None:
    user = _make_user(features=[])
    r1 = await chatbot_tools.TOOL_REGISTRY["find_contacts_by_domain"].execute(
        user, db_stub, {}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["find_contacts_by_domain"].execute(
        user, db_stub, {"domain": "localhost"}
    )
    assert r1["error"] == "invalid_args"
    assert r2["error"] == "invalid_args"


async def test_contact_lookup_tools_have_no_permission_gate(
    no_access_user, db_stub
) -> None:
    """Mirrors the ungated /contacts endpoints — the db stub turns the
    query into tool_error, but the gate must not fire."""
    r1 = await chatbot_tools.TOOL_REGISTRY["find_contact_by_email"].execute(
        no_access_user, db_stub, {"email": "a@b.com"}
    )
    r2 = await chatbot_tools.TOOL_REGISTRY["find_contacts_by_domain"].execute(
        no_access_user, db_stub, {"domain": "acme.com"}
    )
    assert r1.get("error") != "no_access"
    assert r2.get("error") != "no_access"


# ── Schema-drift guard ──────────────────────────────────────────────────


def test_bd_projection_keys_exist_on_schema() -> None:
    """Every key the BD projections emit must be a real field on
    ``BrokerDealerListItem``. If this fails, someone renamed a schema
    field — update the projection in ``chatbot_tools._BD_SUMMARY_KEYS`` or
    ``_BD_PROFILE_EXTRA_KEYS`` in lockstep."""
    fields = set(BrokerDealerListItem.model_fields.keys())
    for key in (*chatbot_tools._BD_SUMMARY_KEYS, *chatbot_tools._BD_PROFILE_EXTRA_KEYS):
        assert key in fields, f"BD projection key {key!r} missing from BrokerDealerListItem"


def test_ia_projection_keys_exist_on_schema() -> None:
    fields = set(InvestmentAdvisorListItem.model_fields.keys())
    for key in (*chatbot_tools._IA_SUMMARY_KEYS, *chatbot_tools._IA_PROFILE_EXTRA_KEYS):
        assert key in fields, f"IA projection key {key!r} missing from InvestmentAdvisorListItem"


def test_tool_registry_has_expected_names() -> None:
    """Lock down the public tool surface — adding a new tool should be a
    deliberate change that also updates the iteration tests."""
    assert set(chatbot_tools.TOOL_REGISTRY.keys()) == {
        "search_broker_dealers",
        "get_broker_dealer_profile",
        "search_investment_advisors",
        "get_investment_advisor_profile",
        "search_institutional_investors",
        "get_institutional_investor_profile",
        "list_broker_dealers_by_filter",
        "list_investment_advisors_by_filter",
        "semantic_firm_search",
        # Part B additions (DB-coverage expansion).
        "search_form4_filings",
        "list_filings_for_firm",
        "get_recent_alerts",
        # Part C additions (PDF summarisation + Vault RAG).
        "summarize_broker_dealer_filing",
        "summarize_brokercheck_pdf",
        "summarize_investment_advisor_filing",
        "summarize_institutional_investor_filing",
        "summarize_form4_filing",
        "ask_vault",
        # Full-vault read access — browse folders/files + read a whole file.
        "list_vault_folders",
        "list_vault_files",
        "get_vault_file",
        # Phase 4 PR #1 — app-knowledge tool.
        "get_app_help",
        # Doxie web-research / learned-term glossary tool.
        "research_term",
        # Doxie BD<->IA dual-registration tool.
        "find_dual_registered_firms",
        # Doxie action tools (write-capable).
        "run_email_extractor",
        "draft_outreach_email",
        # Outreach copilot — contacts, saved drafts, confirmed send.
        "list_firm_contacts",
        "save_outreach_draft",
        "list_outreach_drafts",
        "get_outreach_draft",
        "send_outreach_draft",
        # Status & quick-action tools.
        "list_email_scans",
        "get_email_scan_results",
        "get_data_freshness",
        "favorite_firm",
        "unfavorite_firm",
        "list_my_favorites",
        "mark_alerts_read",
        "find_contact_by_email",
        "find_contacts_by_domain",
    }


def test_pdf_tools_opt_out_of_cache_and_extend_timeout() -> None:
    """Tools that actually download a PDF + call Gemini share two
    non-default ``Tool`` fields:

    - ``timeout_s = PDF_TOOL_TIMEOUT_S`` — the 5s default would chop most
      PDF round-trips off mid-Gemini-call.
    - ``cacheable = False`` — prose summaries are too long + too
      per-question to be worth keeping in the per-process LRU.

    This test guards against a copy-paste mistake where a future PDF
    tool forgets one of those fields.

    Excluded by design:

    - ``ask_vault`` — Vault retrieval is fast (one embedding + one
      pg query); re-uses of the same query within a chat ARE worth
      caching.
    - ``summarize_form4_filing`` — Phase 3.1 switched this to a DB-only
      structured summary because Form 4 filings are XML-only in EDGAR
      (no PDF to download). It correctly uses default timeout + cache.
    """
    pdf_tool_names = {
        "summarize_broker_dealer_filing",
        "summarize_brokercheck_pdf",
        "summarize_investment_advisor_filing",
        "summarize_institutional_investor_filing",
    }
    for name in pdf_tool_names:
        tool = chatbot_tools.TOOL_REGISTRY[name]
        assert tool.timeout_s == chatbot_tools.PDF_TOOL_TIMEOUT_S, (
            f"tool {name!r} should opt into the PDF timeout"
        )
        assert tool.cacheable is False, (
            f"tool {name!r} should opt out of the LRU cache"
        )

    # Form 4 is intentionally NOT a PDF tool — DB-only summary.
    form4_tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
    assert form4_tool.timeout_s is None, (
        "summarize_form4_filing is DB-only and should use the default timeout"
    )
    assert form4_tool.cacheable is True, (
        "summarize_form4_filing should be cacheable — deterministic output "
        "from a structured row"
    )


def test_ii_projection_keys_exist_on_schema() -> None:
    fields = set(InstitutionalInvestorListItem.model_fields.keys())
    for key in (*chatbot_tools._II_SUMMARY_KEYS, *chatbot_tools._II_PROFILE_EXTRA_KEYS):
        assert key in fields, (
            f"II projection key {key!r} missing from InstitutionalInvestorListItem"
        )


# ── search_institutional_investors ──────────────────────────────────────


class TestSearchInstitutionalInvestors:
    async def test_happy_path_returns_summary_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        item = InstitutionalInvestorListItem.model_validate(_make_ii_orm())
        monkeypatch.setattr(
            chatbot_tools._ii_repo,
            "list_institutional_investors",
            AsyncMock(return_value=_ListResponseStub(items=[item], meta=_ListMetaStub(total=1))),
        )

        tool = chatbot_tools.TOOL_REGISTRY["search_institutional_investors"]
        result = await tool.execute(ii_user, db_stub, {"query": "Gamma"})

        assert result["total_matched"] == 1
        item = result["items"][0]
        assert set(chatbot_tools._II_SUMMARY_KEYS).issubset(item.keys())
        # total_aum (Decimal) coerced to float by _jsonable.
        assert isinstance(item["total_aum"], float)
        # Per-item detail link (Part A).
        assert item["link"] == f"/institutional-investors/{item['id']}"
        # II list page was restored in #552 — the wrapper now stamps
        # ``list_link`` mirroring the query, same shape as BD/IA.
        assert result["list_link"] == "/institutional-investors?q=Gamma"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._ii_repo, "list_institutional_investors", called)
        tool = chatbot_tools.TOOL_REGISTRY["search_institutional_investors"]
        result = await tool.execute(no_access_user, db_stub, {"query": "Gamma"})
        assert result["error"] == "no_access"
        called.assert_not_called()

    async def test_empty_query_returns_invalid_args(
        self,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["search_institutional_investors"]
        result = await tool.execute(ii_user, db_stub, {"query": ""})
        assert result["error"] == "invalid_args"

    async def test_repo_exception_returns_tool_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def boom(_db: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(chatbot_tools._ii_repo, "list_institutional_investors", boom)
        tool = chatbot_tools.TOOL_REGISTRY["search_institutional_investors"]
        result = await tool.execute(ii_user, db_stub, {"query": "Gamma"})
        assert result["error"] == "tool_error"


# ── get_institutional_investor_profile ──────────────────────────────────


class TestGetInstitutionalInvestorProfile:
    async def test_happy_path_includes_profile_extras(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._ii_repo,
            "get_institutional_investor",
            AsyncMock(return_value=_make_ii_orm()),
        )
        tool = chatbot_tools.TOOL_REGISTRY["get_institutional_investor_profile"]
        result = await tool.execute(ii_user, db_stub, {"investor_id": 88})

        for k in (*chatbot_tools._II_SUMMARY_KEYS, *chatbot_tools._II_PROFILE_EXTRA_KEYS):
            assert k in result
        assert result["link"] == "/institutional-investors/88"
        # latest_13f_filing_date (date) coerced to isoformat string.
        assert isinstance(result["latest_13f_filing_date"], str)

    async def test_not_found_returns_structured_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._ii_repo,
            "get_institutional_investor",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["get_institutional_investor_profile"]
        result = await tool.execute(ii_user, db_stub, {"investor_id": 9999})
        assert result["error"] == "not_found"

    async def test_invalid_id_returns_invalid_args(
        self,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_institutional_investor_profile"]
        result = await tool.execute(ii_user, db_stub, {"investor_id": "abc"})
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._ii_repo, "get_institutional_investor", called)
        tool = chatbot_tools.TOOL_REGISTRY["get_institutional_investor_profile"]
        result = await tool.execute(no_access_user, db_stub, {"investor_id": 88})
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── list_broker_dealers_by_filter ───────────────────────────────────────


class TestListBrokerDealersByFilter:
    async def test_at_least_one_filter_required(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", called)
        tool = chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"]
        result = await tool.execute(bd_user, db_stub, {})
        assert result["error"] == "invalid_args"
        called.assert_not_called()

    async def test_state_filter_threads_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"]
        await tool.execute(bd_user, db_stub, {"state": "NY"})
        assert captured["states"] == ["NY"]
        assert captured["search"] is None
        assert captured["list_mode"] == "all"

    async def test_net_capital_band_and_clearing_partner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"]
        result = await tool.execute(
            bd_user,
            db_stub,
            {
                "min_net_capital": 5_000_000,
                "max_net_capital": 100_000_000,
                "clearing_partner": "Pershing",
            },
        )
        assert captured["min_net_capital"] == 5_000_000.0
        assert captured["max_net_capital"] == 100_000_000.0
        assert captured["clearing_partners"] == ["Pershing"]
        # list_link mirrors the filters back into the URL so the user
        # lands on a pre-filtered master list.
        assert "clearing_partner=Pershing" in result["list_link"]
        assert "min_net_capital=5000000" in result["list_link"]
        assert "max_net_capital=100000000" in result["list_link"]

    async def test_limit_clamped_to_filter_max(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"]
        await tool.execute(bd_user, db_stub, {"state": "CA", "limit": 9999})
        assert captured["limit"] == chatbot_tools.LIST_FILTER_LIMIT_MAX

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._bd_repo, "list_broker_dealers", called)
        tool = chatbot_tools.TOOL_REGISTRY["list_broker_dealers_by_filter"]
        result = await tool.execute(no_access_user, db_stub, {"state": "NY"})
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── list_investment_advisors_by_filter ──────────────────────────────────


class TestListInvestmentAdvisorsByFilter:
    async def test_at_least_one_filter_required(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", called)
        tool = chatbot_tools.TOOL_REGISTRY["list_investment_advisors_by_filter"]
        result = await tool.execute(ia_user, db_stub, {})
        assert result["error"] == "invalid_args"
        called.assert_not_called()

    async def test_files_13f_bool_threads_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["list_investment_advisors_by_filter"]
        await tool.execute(ia_user, db_stub, {"files_13f": True})
        assert captured["files_13f"] is True

    async def test_aum_band_and_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _ListResponseStub(items=[], meta=_ListMetaStub(total=0))

        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["list_investment_advisors_by_filter"]
        result = await tool.execute(
            ia_user,
            db_stub,
            {"state": "NY", "min_regulatory_aum": 1_000_000_000},
        )
        assert captured["states"] == ["NY"]
        assert captured["min_regulatory_aum"] == 1_000_000_000.0
        # list_link mirrors the filters back to the advisor list URL.
        assert "state=NY" in result["list_link"]
        assert "min_regulatory_aum=1000000000" in result["list_link"]

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._ia_repo, "list_investment_advisors", called)
        tool = chatbot_tools.TOOL_REGISTRY["list_investment_advisors_by_filter"]
        result = await tool.execute(no_access_user, db_stub, {"state": "NY"})
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── search_form4_filings ─────────────────────────────────────────────────


def _make_form4_row(**overrides: Any) -> Any:
    """SimpleNamespace-style shim mimicking a ConsolidatedPersonRow.

    The real dataclass has 27 fields; only the projection-relevant ones
    matter here. Defaults model a typical insider buy (ad_code='A') with
    aggregate shares + value populated.
    """
    defaults: dict[str, Any] = {
        "id": 1,
        "reporting_owner_name": "JOHN A. SMITH",
        "reporting_owner_cik": "0001234567",
        "reporting_owner_title": "Chief Executive Officer",
        "reporting_owner_state": "CA",
        "reporting_owner_is_director": True,
        "reporting_owner_is_officer": True,
        "reporting_owner_is_ten_pct": False,
        "issuer_name": "Acme Corp.",
        "issuer_ticker": "ACME",
        "ad_code": "A",
        "security_title": "Common Stock",
        "transaction_date": date(2026, 5, 10),
        "shares": 10000.0,
        "transaction_value": 250000.0,
        "txn_count": 2,
        "filed_at": datetime(2026, 5, 12),
        "source_filing_url": "https://www.sec.gov/Archives/...",
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


class TestSearchForm4Filings:
    async def test_happy_path_projects_with_link_and_list_link(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        async def fake_list(_db: Any, **_kwargs: Any) -> tuple[list[Any], int]:
            return [_make_form4_row()], 1

        monkeypatch.setattr(
            chatbot_tools._form4_repo, "list_consolidated_persons", fake_list
        )

        tool = chatbot_tools.TOOL_REGISTRY["search_form4_filings"]
        result = await tool.execute(
            investors_user, db_stub, {"query": "John Smith"}
        )

        assert result["total_matched"] == 1
        item = result["items"][0]
        # Friendly transaction_kind label (so Doxie doesn't need to
        # remember the A/D convention).
        assert item["transaction_kind"] == "acquired"
        # Ticker-scoped deep-link. ``tab=buyers`` is the FE default so
        # it gets stripped (landing on a bare ?ticker= URL is what the
        # workspace itself emits when only the ticker is filtered).
        assert item["link"] == "/investors?ticker=ACME"
        # The list_link mirrors the query so the user lands on the
        # same view. ``tab=buyers`` is also stripped here (default).
        assert "q=John+Smith" in result["list_link"]
        assert result["list_link"].startswith("/investors?")

    async def test_ad_code_d_maps_to_sellers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> tuple[list[Any], int]:
            captured.update(kwargs)
            return [], 0

        monkeypatch.setattr(
            chatbot_tools._form4_repo, "list_consolidated_persons", fake_list
        )
        tool = chatbot_tools.TOOL_REGISTRY["search_form4_filings"]
        result = await tool.execute(
            investors_user, db_stub, {"ad_code": "D", "ticker": "AAPL"}
        )

        assert captured["ad_code"] == "D"
        assert captured["ticker"] == "AAPL"
        assert "tab=sellers" in result["list_link"]
        assert "ticker=AAPL" in result["list_link"]

    async def test_invalid_ad_code_returns_invalid_args(
        self,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["search_form4_filings"]
        result = await tool.execute(
            investors_user, db_stub, {"query": "x", "ad_code": "X"}
        )
        assert result["error"] == "invalid_args"

    async def test_no_filters_returns_invalid_args(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        # Repo must NOT be called — an unfiltered query would dump the
        # whole table. Guard mirrors the existing list_*_by_filter rule.
        called = AsyncMock()
        monkeypatch.setattr(
            chatbot_tools._form4_repo, "list_consolidated_persons", called
        )
        tool = chatbot_tools.TOOL_REGISTRY["search_form4_filings"]
        result = await tool.execute(investors_user, db_stub, {})
        assert result["error"] == "invalid_args"
        called.assert_not_called()

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(
            chatbot_tools._form4_repo, "list_consolidated_persons", called
        )
        tool = chatbot_tools.TOOL_REGISTRY["search_form4_filings"]
        result = await tool.execute(no_access_user, db_stub, {"query": "x"})
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── list_filings_for_firm ────────────────────────────────────────────────


def _make_filing(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": 99,
        "form_type": "Form X-17A-5",
        "priority": "medium",
        "filed_at": datetime(2026, 4, 15),
        "summary": "Annual audited financial statements filed.",
        "source_filing_url": "https://www.sec.gov/Archives/edgar/...",
        "is_read": False,
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


class TestListFilingsForFirm:
    async def test_bd_dispatches_to_alerts_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._alerts_repo,
            "get_filing_history",
            AsyncMock(return_value=[_make_filing(), _make_filing(id=100)]),
        )

        tool = chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"]
        result = await tool.execute(
            bd_user, db_stub, {"firm_type": "bd", "firm_id": 42}
        )

        assert result["total_matched"] == 2
        # filing_id is the handle Part C's PDF tool will accept.
        assert result["items"][0]["filing_id"] == 99
        # Deep-link is to the firm detail page, not the filing itself.
        assert result["items"][0]["link"] == "/master-list/42"

    async def test_ia_dispatches_to_advisor_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "list_advisor_filings",
            AsyncMock(return_value=[_make_filing(form_type="Form ADV")]),
        )

        tool = chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"]
        result = await tool.execute(
            ia_user, db_stub, {"firm_type": "ia", "firm_id": 77}
        )
        assert result["items"][0]["form_type"] == "Form ADV"
        assert result["items"][0]["link"] == "/advisor-list/77"

    async def test_form_type_filter_applied_client_side(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._alerts_repo,
            "get_filing_history",
            AsyncMock(
                return_value=[
                    _make_filing(form_type="Form X-17A-5"),
                    _make_filing(form_type="Form BD"),
                    _make_filing(form_type="Form X-17A-5"),
                ]
            ),
        )
        tool = chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"]
        result = await tool.execute(
            bd_user,
            db_stub,
            {"firm_type": "bd", "firm_id": 42, "form_type": "Form X-17A-5"},
        )
        assert result["total_matched"] == 2
        assert all(it["form_type"] == "Form X-17A-5" for it in result["items"])

    async def test_invalid_firm_type_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"]
        result = await tool.execute(
            bd_user, db_stub, {"firm_type": "potato", "firm_id": 42}
        )
        assert result["error"] == "invalid_args"

    async def test_403_when_bd_user_lacks_master_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        # Each firm_type gates on a different feature key — confirm
        # the bd path returns no_access for a user without MASTER_LIST.
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._alerts_repo, "get_filing_history", called)
        tool = chatbot_tools.TOOL_REGISTRY["list_filings_for_firm"]
        result = await tool.execute(
            no_access_user, db_stub, {"firm_type": "bd", "firm_id": 42}
        )
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── get_recent_alerts ────────────────────────────────────────────────────


def _make_alert_list_item(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": 11,
        "bd_id": 42,
        "firm_name": "Acme Securities LLC",
        "form_type": "Form X-17A-5",
        "priority": "medium",
        "filed_at": datetime(2026, 5, 20),
        "summary": "Annual filing accepted.",
        "source_filing_url": None,
        "is_read": False,
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


class TestGetRecentAlerts:
    async def test_happy_path_projects_with_firm_link(
        self,
        monkeypatch: pytest.MonkeyPatch,
        alerts_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        class _Resp:
            items = [_make_alert_list_item()]

            class meta:  # noqa: D401, N801
                total = 1

        monkeypatch.setattr(
            chatbot_tools._alerts_repo,
            "list_alerts",
            AsyncMock(return_value=_Resp()),
        )

        tool = chatbot_tools.TOOL_REGISTRY["get_recent_alerts"]
        result = await tool.execute(alerts_user, db_stub, {})

        assert result["total_matched"] == 1
        item = result["items"][0]
        assert item["firm_name"] == "Acme Securities LLC"
        # Deep-link to the firm whose filing triggered the alert.
        assert item["link"] == "/master-list/42"
        # The /alerts page has no URL-state for filters today, so the
        # list_link is the bare /alerts URL.
        assert result["list_link"] == "/alerts"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._alerts_repo, "list_alerts", called)
        tool = chatbot_tools.TOOL_REGISTRY["get_recent_alerts"]
        result = await tool.execute(no_access_user, db_stub, {})
        assert result["error"] == "no_access"
        called.assert_not_called()

    async def test_filter_threads_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        alerts_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_list(_db: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)

            class _Resp:
                items: list[Any] = []

                class meta:  # noqa: D401, N801
                    total = 0

            return _Resp()

        monkeypatch.setattr(chatbot_tools._alerts_repo, "list_alerts", fake_list)
        tool = chatbot_tools.TOOL_REGISTRY["get_recent_alerts"]
        await tool.execute(
            alerts_user,
            db_stub,
            {"form_type": "Form X-17A-5", "is_read": False},
        )
        assert captured["form_types"] == ["Form X-17A-5"]
        assert captured["is_read"] is False


# ── summarize_broker_dealer_filing ────────────────────────────────────────


class _Pdf:
    """SimpleNamespace shim for ``DownloadedPdfRecord``.

    The real dataclass has ~15 fields; only a few matter to the BD
    summariser projection so we keep the shim small.
    """

    def __init__(
        self,
        *,
        bytes_base64: str = "",
        local_document_path: str = "",
        source_filing_url: str | None = "https://www.sec.gov/Archives/edgar/data/42/0001234567.pdf",
        filing_year: int | None = 2025,
        report_date: Any = date(2025, 12, 31),
    ) -> None:
        self.bytes_base64 = bytes_base64
        self.local_document_path = local_document_path
        self.source_filing_url = source_filing_url
        self.filing_year = filing_year
        self.report_date = report_date
        # Fields the dataclass also exposes but nothing in the projection
        # currently reads. Listed for fidelity in case a future
        # assertion grows.
        self.bd_id = 42
        self.source_pdf_url = None


class TestSummarizeBrokerDealerFiling:
    async def test_happy_path_returns_summary_and_link(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        orm = _make_bd_orm(name="Apex Clearing Corporation")
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=orm),
        )
        # The real downloader uses ``with pdf_tempdir() as dest_dir`` —
        # we don't need to fake that because the mock replaces the
        # downloader's method, not the contextmanager.
        monkeypatch.setattr(
            chatbot_tools._pdf_downloader,
            "download_latest_x17a5_pdf",
            AsyncMock(return_value=_Pdf(bytes_base64="UERG")),  # b"PDF" b64
        )
        captured: dict[str, Any] = {}

        async def fake_summarize(*, pdf_bytes_base64: str, prompt: str, **_: Any) -> str:
            captured["pdf_bytes_base64"] = pdf_bytes_base64
            captured["prompt"] = prompt
            return "Apex Clearing posted net capital of $1.5M, up 12% YoY..."

        monkeypatch.setattr(
            chatbot_tools._gemini_client, "summarize_pdf", fake_summarize
        )

        tool = chatbot_tools.TOOL_REGISTRY["summarize_broker_dealer_filing"]
        result = await tool.execute(
            bd_user, db_stub, {"broker_dealer_id": 42, "question": "What's the trend?"}
        )

        assert "net capital" in result["summary"]
        assert result["link"] == "/master-list/42"
        assert result["firm_name"] == "Apex Clearing Corporation"
        # The question was woven into the prompt so the model has it.
        assert "What's the trend?" in captured["prompt"]
        # The bytes passed through unmodified.
        assert captured["pdf_bytes_base64"] == "UERG"

    async def test_no_pdf_available_returns_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm()),
        )
        # The downloader returns None when EDGAR has nothing for this BD.
        monkeypatch.setattr(
            chatbot_tools._pdf_downloader,
            "download_latest_x17a5_pdf",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_broker_dealer_filing"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})
        assert result["error"] == "not_found"
        assert "X-17A-5" in result["message"]

    async def test_unparseable_pdf_returns_friendly_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """summarize_pdf returns None for zero-page docs — surface as a
        clean 'unparseable' error rather than empty prose."""
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm()),
        )
        monkeypatch.setattr(
            chatbot_tools._pdf_downloader,
            "download_latest_x17a5_pdf",
            AsyncMock(return_value=_Pdf(bytes_base64="UERG")),
        )
        monkeypatch.setattr(
            chatbot_tools._gemini_client,
            "summarize_pdf",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_broker_dealer_filing"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})
        assert result["error"] == "unparseable"
        assert result["source_filing_url"]

    async def test_not_found_when_bd_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_broker_dealer_filing"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 9999})
        assert result["error"] == "not_found"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._bd_repo, "get_broker_dealer", called)
        tool = chatbot_tools.TOOL_REGISTRY["summarize_broker_dealer_filing"]
        result = await tool.execute(
            no_access_user, db_stub, {"broker_dealer_id": 42}
        )
        assert result["error"] == "no_access"
        called.assert_not_called()


# ── summarize_brokercheck_pdf ────────────────────────────────────────────


class TestSummarizeBrokerCheckPdf:
    async def test_happy_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm(crd_number="12345")),
        )
        monkeypatch.setattr(
            chatbot_tools,
            "fetch_brokercheck_pdf",
            AsyncMock(return_value=b"%PDF-1.4 ..."),
        )
        monkeypatch.setattr(
            chatbot_tools._gemini_client,
            "summarize_pdf",
            AsyncMock(return_value="No regulatory actions in the past 5 years."),
        )

        tool = chatbot_tools.TOOL_REGISTRY["summarize_brokercheck_pdf"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})
        assert "regulatory actions" in result["summary"]
        assert result["crd_number"] == "12345"
        assert result["link"] == "/master-list/42"
        # Source URL points at the BrokerCheck firm-summary page (the
        # PDF is at a different URL but firm-summary is what users
        # actually click through to).
        assert "brokercheck.finra.org" in result["source_filing_url"]

    async def test_finra_404_returns_clean_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from app.services.finra_pdf_service import FinraPdfNotFound

        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm(crd_number="12345")),
        )

        async def boom(*_args: Any, **_kwargs: Any) -> bytes:
            raise FinraPdfNotFound("no PDF for CRD 12345")

        monkeypatch.setattr(chatbot_tools, "fetch_brokercheck_pdf", boom)
        tool = chatbot_tools.TOOL_REGISTRY["summarize_brokercheck_pdf"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})
        assert result["error"] == "not_found"
        # Still links the user to the firm detail page as a fallback.
        assert result["link"] == "/master-list/42"

    async def test_bd_without_crd_returns_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._bd_repo,
            "get_broker_dealer",
            AsyncMock(return_value=_make_bd_orm(crd_number=None)),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_brokercheck_pdf"]
        result = await tool.execute(bd_user, db_stub, {"broker_dealer_id": 42})
        assert result["error"] == "not_found"
        assert "CRD" in result["message"]


# ── ask_vault ────────────────────────────────────────────────────────────


def _make_vault_folder(**overrides: Any) -> Any:
    """SimpleNamespace shim for a ``VaultFolder`` ORM row."""
    defaults: dict[str, Any] = {
        "id": 1,
        "user_id": "owner",
        "name": "Compliance",
        "description": "Playbooks and internal memos.",
        "outreach_instructions": "",
        "default_sender_account_id": None,
        "created_at": datetime(2026, 5, 1, 9, 0),
        "updated_at": datetime(2026, 6, 1, 12, 0),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_vault_file(**overrides: Any) -> Any:
    """SimpleNamespace shim for a ``VaultFolderFile`` ORM row."""
    defaults: dict[str, Any] = {
        "id": 10,
        "folder_id": 1,
        "original_filename": "compliance_playbook.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12345,
        "processing_status": "ready",
        "extracted_text": "Step 1: confirm clearing partner before outreach.",
        "created_at": datetime(2026, 5, 2, 10, 0),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestAskVault:
    async def test_happy_path_single_folder(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        # folder_id given → one owner-check execute, then retrieval scoped
        # to exactly that folder.
        from app.services.vault_retrieval import RetrievedVaultChunk

        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = vault_user.id
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        async def fake_retrieve(
            *, folder_ids: Any, query: str, db: Any, top_k: int
        ) -> list[Any]:
            assert folder_ids == [7]
            assert "playbook" in query
            return [
                RetrievedVaultChunk(
                    chunk_id=1,
                    file_id=10,
                    folder_id=7,
                    folder_name="Compliance",
                    chunk_index=0,
                    text="Step 1: confirm clearing partner before outreach.",
                    original_filename="compliance_playbook.pdf",
                    similarity=0.87,
                ),
            ]

        monkeypatch.setattr(
            chatbot_tools, "retrieve_chunks_for_folders", fake_retrieve
        )

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user,
            db_mock,
            {"query": "what's our outreach playbook?", "folder_id": 7},
        )
        assert result["total_matched"] == 1
        assert result["scope"] == 7
        item = result["items"][0]
        assert item["original_filename"] == "compliance_playbook.pdf"
        assert item["folder_name"] == "Compliance"
        assert item["similarity"] == 0.87
        # Vault link is the bare /vault page (no per-folder route yet).
        assert result["link"] == "/vault"

    async def test_default_searches_all_owned_folders(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """No folder_id → resolve the caller's folder ids and search them
        all (the cross-folder default)."""
        from app.services.vault_retrieval import RetrievedVaultChunk

        ids_result = MagicMock()
        ids_result.scalars.return_value.all.return_value = [1, 2, 3]
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=ids_result)

        async def fake_retrieve(
            *, folder_ids: Any, query: str, db: Any, top_k: int
        ) -> list[Any]:
            assert folder_ids == [1, 2, 3]
            return [
                RetrievedVaultChunk(
                    chunk_id=2,
                    file_id=20,
                    folder_id=2,
                    folder_name="Stock Loan",
                    chunk_index=1,
                    text="Rates updated quarterly.",
                    original_filename="rate_sheet.xlsx",
                    similarity=0.5,
                ),
            ]

        monkeypatch.setattr(
            chatbot_tools, "retrieve_chunks_for_folders", fake_retrieve
        )

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user, db_mock, {"query": "securities lending rates"}
        )
        assert result["scope"] == "all_folders"
        assert result["total_matched"] == 1
        assert result["items"][0]["folder_name"] == "Stock Loan"

    async def test_no_folders_returns_empty_with_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """A user with an empty vault gets an explicit empty result (not an
        error) and retrieval is never attempted."""
        ids_result = MagicMock()
        ids_result.scalars.return_value.all.return_value = []
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=ids_result)

        called = AsyncMock()
        monkeypatch.setattr(
            chatbot_tools, "retrieve_chunks_for_folders", called
        )

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(vault_user, db_mock, {"query": "anything"})
        assert result["total_matched"] == 0
        assert result["items"] == []
        assert "note" in result
        called.assert_not_called()

    async def test_non_owner_returns_opaque_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Cross-tenant attempt looks identical to 'folder doesn't exist'
        — never reveal the existence of someone else's folder."""
        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = "some-other-user"
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        called = AsyncMock()
        monkeypatch.setattr(
            chatbot_tools, "retrieve_chunks_for_folders", called
        )

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user, db_mock, {"query": "x", "folder_id": 7}
        )
        assert result["error"] == "not_found"
        called.assert_not_called()

    async def test_missing_folder_returns_not_found(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = None
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user, db_mock, {"query": "x", "folder_id": 9999}
        )
        assert result["error"] == "not_found"

    async def test_empty_query_returns_invalid_args(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(vault_user, db_stub, {"query": "  "})
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(no_access_user, db_stub, {"query": "x"})
        assert result["error"] == "no_access"


class TestListVaultFolders:
    async def test_lists_folders_with_file_counts(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        folders_result = MagicMock()
        folders_result.scalars.return_value.all.return_value = [
            _make_vault_folder(id=1, name="Compliance"),
            _make_vault_folder(id=2, name="Stock Loan"),
        ]
        counts_result = MagicMock()
        counts_result.all.return_value = [
            SimpleNamespace(folder_id=1, total=3, ready=2),
            SimpleNamespace(folder_id=2, total=1, ready=1),
        ]
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(side_effect=[folders_result, counts_result])

        tool = chatbot_tools.TOOL_REGISTRY["list_vault_folders"]
        result = await tool.execute(vault_user, db_mock, {})
        assert result["total"] == 2
        by_id = {it["folder_id"]: it for it in result["items"]}
        assert by_id[1]["name"] == "Compliance"
        assert by_id[1]["file_count"] == 3
        assert by_id[1]["ready_file_count"] == 2
        assert by_id[2]["file_count"] == 1
        assert result["link"] == "/vault"

    async def test_empty_vault_returns_no_items(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        folders_result = MagicMock()
        folders_result.scalars.return_value.all.return_value = []
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=folders_result)

        tool = chatbot_tools.TOOL_REGISTRY["list_vault_folders"]
        result = await tool.execute(vault_user, db_mock, {})
        assert result["items"] == []
        assert result["total"] == 0

    async def test_403_returns_no_access(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["list_vault_folders"]
        result = await tool.execute(no_access_user, db_stub, {})
        assert result["error"] == "no_access"


class TestListVaultFiles:
    async def test_all_files_default(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        files_result = MagicMock()
        files_result.all.return_value = [
            (_make_vault_file(id=10, folder_id=1), "Compliance"),
            (
                _make_vault_file(
                    id=11, folder_id=2, original_filename="rates.xlsx"
                ),
                "Stock Loan",
            ),
        ]
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=files_result)

        tool = chatbot_tools.TOOL_REGISTRY["list_vault_files"]
        result = await tool.execute(vault_user, db_mock, {})
        assert result["scope"] == "all_folders"
        assert result["total"] == 2
        item = result["items"][0]
        assert item["file_id"] == 10
        assert item["folder_name"] == "Compliance"
        assert item["processing_status"] == "ready"

    async def test_folder_scoped_checks_ownership(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = vault_user.id
        files_result = MagicMock()
        files_result.all.return_value = [
            (_make_vault_file(id=10, folder_id=7), "Custody"),
        ]
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(side_effect=[owner_result, files_result])

        tool = chatbot_tools.TOOL_REGISTRY["list_vault_files"]
        result = await tool.execute(vault_user, db_mock, {"folder_id": 7})
        assert result["scope"] == 7
        assert result["total"] == 1
        assert result["items"][0]["folder_name"] == "Custody"

    async def test_folder_scoped_non_owner_not_found(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = "other-user"
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        tool = chatbot_tools.TOOL_REGISTRY["list_vault_files"]
        result = await tool.execute(vault_user, db_mock, {"folder_id": 7})
        assert result["error"] == "not_found"

    async def test_403_returns_no_access(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["list_vault_files"]
        result = await tool.execute(no_access_user, db_stub, {})
        assert result["error"] == "no_access"


class TestGetVaultFile:
    async def test_reads_full_text(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        file_obj = _make_vault_file(
            id=10, folder_id=1, extracted_text="Full document body here."
        )
        row_result = MagicMock()
        row_result.first.return_value = (file_obj, vault_user.id, "Compliance")
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=row_result)

        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_mock, {"file_id": 10})
        assert result["file_id"] == 10
        assert result["folder_name"] == "Compliance"
        assert result["extracted_text"] == "Full document body here."
        assert result["text_truncated"] is False
        assert "note" not in result

    async def test_long_text_is_truncated_and_flagged(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        cap = chatbot_tools.VAULT_FILE_TEXT_MAX_CHARS
        file_obj = _make_vault_file(extracted_text="x" * (cap + 500))
        row_result = MagicMock()
        row_result.first.return_value = (file_obj, vault_user.id, "Compliance")
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=row_result)

        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_mock, {"file_id": 10})
        assert result["text_truncated"] is True
        assert len(result["extracted_text"]) == cap

    async def test_not_ready_file_includes_note(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        file_obj = _make_vault_file(
            processing_status="extracting", extracted_text=None
        )
        row_result = MagicMock()
        row_result.first.return_value = (file_obj, vault_user.id, "Compliance")
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=row_result)

        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_mock, {"file_id": 10})
        assert "note" in result
        assert result["extracted_text"] == ""

    async def test_non_owner_not_found(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        file_obj = _make_vault_file()
        row_result = MagicMock()
        row_result.first.return_value = (file_obj, "other-user", "Compliance")
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=row_result)

        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_mock, {"file_id": 10})
        assert result["error"] == "not_found"

    async def test_missing_file_returns_not_found(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        row_result = MagicMock()
        row_result.first.return_value = None
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=row_result)

        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_mock, {"file_id": 9999})
        assert result["error"] == "not_found"

    async def test_missing_file_id_returns_invalid_args(
        self,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(vault_user, db_stub, {})
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_vault_file"]
        result = await tool.execute(no_access_user, db_stub, {"file_id": 10})
        assert result["error"] == "no_access"


# ── summarize_form4_filing (Phase 3.1: DB-only summary) ─────────────────


def _make_form4_txn(**overrides: Any) -> Any:
    """SimpleNamespace shim mimicking a ``Form4Transaction`` ORM row.

    Only the fields the summariser reads need to be set; the projection
    falls through ``None`` for anything missing.
    """
    defaults: dict[str, Any] = {
        "id": 99,
        "accession_number": "0001234567-26-000001",
        "is_derivative": False,
        "issuer_cik": "0001234567",
        "issuer_name": "Acme Corp.",
        "issuer_ticker": "ACME",
        "reporting_owner_cik": "0007654321",
        "reporting_owner_name": "JOHN A. SMITH",
        "reporting_owner_title": "Chief Executive Officer",
        "reporting_owner_is_director": True,
        "reporting_owner_is_officer": True,
        "reporting_owner_is_ten_pct": False,
        "security_title": "Common Stock",
        "transaction_date": date(2026, 5, 10),
        "transaction_code": "P",
        "ad_code": "A",
        "shares": Decimal("10000"),
        "price_per_share": Decimal("25.00"),
        "transaction_value": Decimal("250000"),
        "source_filing_url": (
            "https://www.sec.gov/Archives/edgar/data/1234567/"
            "000123456726000001/0001234567-26-000001-index.htm"
        ),
        "filed_at": datetime(2026, 5, 12, 14, 30),
    }
    defaults.update(overrides)

    class _Obj:
        pass

    obj = _Obj()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


class TestSummarizeForm4FilingDbOnly:
    """Phase 3.1 rewrite: this tool no longer fetches anything from SEC.
    Form 4 filings are XML-only in EDGAR; the watcher already parsed the
    transaction into normalised columns, so the summary is built
    deterministically from the DB row."""

    async def test_happy_path_no_http_fetch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Smoke check: the tool returns a structured summary AND never
        touches the SEC HTTP path. The fetcher stubs would explode if
        called — proves the DB-only path is taken."""
        monkeypatch.setattr(
            chatbot_tools._form4_repo,
            "get",
            AsyncMock(return_value=_make_form4_txn()),
        )

        async def _boom_filing_fetch(*_a: Any, **_kw: Any) -> bytes:
            raise AssertionError(
                "Form 4 tool must not hit fetch_filing_pdf_bytes — it's DB-only"
            )

        async def _boom_direct_fetch(*_a: Any, **_kw: Any) -> bytes:
            raise AssertionError(
                "Form 4 tool must not hit fetch_sec_pdf_bytes — it's DB-only"
            )

        monkeypatch.setattr(
            chatbot_tools, "fetch_filing_pdf_bytes", _boom_filing_fetch
        )
        monkeypatch.setattr(chatbot_tools, "fetch_sec_pdf_bytes", _boom_direct_fetch)

        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            investors_user, db_stub, {"form4_transaction_id": 99}
        )

        # Summary cites the structured data verbatim.
        assert "JOHN A. SMITH" in result["summary"]
        assert "Acme Corp." in result["summary"]
        assert "ACME" in result["summary"]
        assert "10,000 shares" in result["summary"]
        assert "$250,000.00" in result["summary"]
        assert "acquired" in result["summary"]
        # Mentions the XML-only nature so Doxie can relay it.
        assert "XML-only" in result["summary"]
        # Structured extras for the model to cite.
        assert result["reporting_owner_name"] == "JOHN A. SMITH"
        assert result["ad_code"] == "A"
        # Link to the /investors page filtered by ticker + buyers tab.
        # ``tab=buyers`` is the FE default and gets stripped, leaving
        # just the ticker filter.
        assert result["link"] == "/investors?ticker=ACME"
        # Helpful debug flag — distinguishes this tool from PDF
        # summaries in logs / chat history.
        assert result["data_source"] == "db_structured_form4_row"

    async def test_disposal_uses_disposed_verb_and_sellers_tab(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._form4_repo,
            "get",
            AsyncMock(return_value=_make_form4_txn(ad_code="D")),
        )

        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            investors_user, db_stub, {"form4_transaction_id": 99}
        )
        assert "disposed of" in result["summary"]
        # Sellers tab in the deep-link.
        assert "tab=sellers" in result["link"]

    async def test_derivative_security_called_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._form4_repo,
            "get",
            AsyncMock(
                return_value=_make_form4_txn(
                    is_derivative=True,
                    security_title="Employee Stock Option",
                )
            ),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            investors_user, db_stub, {"form4_transaction_id": 99}
        )
        assert "Employee Stock Option" in result["summary"]
        assert "derivative security" in result["summary"]

    async def test_not_found_returns_structured_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools._form4_repo,
            "get",
            AsyncMock(return_value=None),
        )
        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            investors_user, db_stub, {"form4_transaction_id": 9999}
        )
        assert result["error"] == "not_found"

    async def test_403_returns_no_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools._form4_repo, "get", called)
        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            no_access_user, db_stub, {"form4_transaction_id": 99}
        )
        assert result["error"] == "no_access"
        called.assert_not_called()

    async def test_invalid_id_returns_invalid_args(
        self,
        investors_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["summarize_form4_filing"]
        result = await tool.execute(
            investors_user, db_stub, {"form4_transaction_id": "abc"}
        )
        assert result["error"] == "invalid_args"


# ── summarize_investment_advisor_filing routes through the new fetcher ──


class TestSummarizeInvestmentAdvisorFilingForm4Refactor:
    """Phase 3.1 rewires the IA / II tools to use
    ``fetch_filing_pdf_bytes`` (which resolves an HTML-index URL to the
    actual PDF inside via the EDGAR ``index.json`` walk) instead of the
    naive ``fetch_sec_pdf_bytes``. This test guards the wiring — the
    fetcher must receive the filing's ``form_type`` so the resolver can
    bias toward the Part 2A brochure for ADV filings."""

    async def test_passes_form_type_through_to_filing_fetcher(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ia_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from unittest.mock import MagicMock

        # Stub the AdvisorFiling lookup.
        filing = _make_filing(form_type="Form ADV")
        filing.advisor_id = 77
        filing.source_filing_url = (
            "https://www.sec.gov/Archives/edgar/data/1234567/"
            "000123456726000001/0001234567-26-000001-index.htm"
        )

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = filing

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=execute_result)

        monkeypatch.setattr(
            chatbot_tools._ia_repo,
            "get_investment_advisor",
            AsyncMock(return_value=_make_ia_orm(name="Beta Advisors")),
        )

        captured: dict[str, Any] = {}

        async def fake_fetch(
            source_url: str, *, form_type: str | None = None
        ) -> bytes:
            captured["source_url"] = source_url
            captured["form_type"] = form_type
            return b"%PDF-1.4 brochure"

        monkeypatch.setattr(chatbot_tools, "fetch_filing_pdf_bytes", fake_fetch)
        monkeypatch.setattr(
            chatbot_tools._gemini_client,
            "summarize_pdf",
            AsyncMock(return_value="Brochure summary..."),
        )

        tool = chatbot_tools.TOOL_REGISTRY["summarize_investment_advisor_filing"]
        result = await tool.execute(ia_user, db_mock, {"filing_id": 99})

        # ``form_type`` is plumbed through so the resolver picks the
        # Part 2A brochure inside the EDGAR package.
        assert captured["form_type"] == "Form ADV"
        assert captured["source_url"] == filing.source_filing_url
        assert "Brochure summary" in result["summary"]
        # Detail link points at the advisor.
        assert result["link"] == "/advisor-list/77"


# ── get_app_help ────────────────────────────────────────────────────────


class TestGetAppHelp:
    """The app-help tool is unique among the registry: it has NO feature
    gate (every user can ask Doxie how DOX works), but it DOES silently
    filter results to features the calling user can actually see — so
    a viewer asking about admin-only Users gets the fallback catalog
    instead of a leaked admin page. These tests pin both behaviours."""

    async def test_topic_match_returns_visible_feature_with_link(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(bd_user, db_stub, {"topic": "Master List"})

        assert result["total_matched"] >= 1
        top = result["items"][0]
        assert top["feature_key"] == MASTER_LIST
        assert top["route"] == "/master-list"
        # Deep-link mirrors the route so the FE can render an in-app jump.
        assert top["link"] == "/master-list"
        # Full projection (matched path) includes the verbose action prose.
        assert "what_to_do_here" in top
        assert top["what_to_do_here"]

    async def test_domain_jargon_synonym_matches(
        self,
        ii_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(ii_user, db_stub, {"topic": "13F"})

        assert result["total_matched"] >= 1
        assert result["items"][0]["feature_key"] == INSTITUTIONAL_INVESTORS

    async def test_missing_topic_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(bd_user, db_stub, {})
        assert result["error"] == "invalid_args"

    async def test_blank_topic_returns_invalid_args(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(bd_user, db_stub, {"topic": "   "})
        assert result["error"] == "invalid_args"

    async def test_no_match_falls_back_to_visible_catalog(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Topic with no matches anywhere → catalog of features the user
        CAN see. Bd-only viewer gets just Master List in their catalog."""
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(
            bd_user, db_stub, {"topic": "quantum entanglement"}
        )

        assert result["total_matched"] == 0
        assert "note" in result
        assert "quantum entanglement" in result["note"]
        keys = {item["feature_key"] for item in result["items"]}
        assert keys == {MASTER_LIST}
        # Catalog projection drops verbose prose to keep prompt budget small.
        for item in result["items"]:
            assert "what_to_do_here" not in item

    async def test_no_access_user_match_falls_through_to_empty_catalog(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """User with no feature_permissions and no admin role asking about
        Master List → no visible matches AND no visible catalog. The
        fallback path returns the empty catalog rather than 403'ing —
        the tool is never permission-gated."""
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(
            no_access_user, db_stub, {"topic": "Master List"}
        )

        assert result["total_matched"] == 0
        assert result["items"] == []

    async def test_viewer_cannot_see_admin_only_users_entry(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Non-admin user asking about 'Users' (admin-only feature) gets
        the fallback catalog, not the admin page details."""
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(bd_user, db_stub, {"topic": "users"})

        # Either nothing matched, or the only match was filtered out and
        # we fell back to the catalog. Either way, USERS must not appear.
        matched_keys = {item["feature_key"] for item in result["items"]}
        assert "users" not in matched_keys

    async def test_admin_sees_admin_only_users_entry(
        self,
        db_stub: object,
    ) -> None:
        admin = _make_user(role="admin", features=[])
        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(admin, db_stub, {"topic": "user management"})

        assert result["total_matched"] >= 1
        top = result["items"][0]
        assert top["feature_key"] == "users"
        assert top["admin_only"] is True

    async def test_result_is_jsonable_for_gemini_payload(
        self,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """The whole result must round-trip through json.dumps — otherwise
        httpx will fail to encode the functionResponse part."""
        import json

        tool = chatbot_tools.TOOL_REGISTRY["get_app_help"]
        result = await tool.execute(bd_user, db_stub, {"topic": "Master List"})
        # Should not raise.
        json.dumps(result)


class TestResearchTerm:
    """research_term is ungated (every user can define a term) and *learns*:
    a glossary hit short-circuits the web, while a miss researches the web
    and persists the result. ``search_web`` and the glossary repo helpers
    are monkeypatched here — no network, no DB."""

    async def test_missing_query_returns_invalid_args(
        self, bd_user: AuthenticatedUser, db_stub: object
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(bd_user, db_stub, {})
        assert result["error"] == "invalid_args"

    async def test_glossary_hit_short_circuits_web(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from types import SimpleNamespace

        hit = SimpleNamespace(
            definition="A benchmark overnight rate.",
            source_url="https://example.test/sofr",
        )
        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=hit)
        )
        web = AsyncMock()
        monkeypatch.setattr(chatbot_tools, "search_web", web)
        upsert = AsyncMock()
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", upsert)

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(bd_user, db_stub, {"query": "SOFR"})

        assert result["source"] == "glossary"
        assert result["answer"] == "A benchmark overnight rate."
        web.assert_not_awaited()
        upsert.assert_not_awaited()

    async def test_miss_researches_web_and_persists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            chatbot_tools,
            "search_web",
            AsyncMock(
                return_value={
                    "results": [
                        {
                            "title": "T+1",
                            "url": "https://sec.gov/t1",
                            "snippet": "Settles next business day.",
                        }
                    ],
                    "answer": "T+1 settles one business day after trade.",
                    "provider": "serpapi",
                }
            ),
        )
        upsert = AsyncMock()
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", upsert)

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(
            bd_user, db_stub, {"query": "T+1 settlement"}
        )

        assert result["source"] == "public_web"
        assert result["answer"] == "T+1 settles one business day after trade."
        upsert.assert_awaited_once()
        kwargs = upsert.await_args.kwargs
        assert kwargs["term"] == "T+1 settlement"
        assert kwargs["definition"] == "T+1 settles one business day after trade."
        assert kwargs["source"] == "serpapi"
        assert kwargs["source_url"] == "https://sec.gov/t1"

    async def test_unavailable_when_glossary_and_web_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            chatbot_tools,
            "search_web",
            AsyncMock(
                return_value={"results": [], "answer": None, "provider": None}
            ),
        )
        upsert = AsyncMock()
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", upsert)

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(bd_user, db_stub, {"query": "zzz"})

        assert result["error"] == "unavailable"
        upsert.assert_not_awaited()

    async def test_term_alias_and_snippet_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """The 'term' arg aliases 'query', and when the web result has no
        high-confidence answer the top snippet is used as the definition."""
        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            chatbot_tools,
            "search_web",
            AsyncMock(
                return_value={
                    "results": [
                        {
                            "title": "CCP",
                            "url": "https://example.test/ccp",
                            "snippet": "A central counterparty clears trades.",
                        }
                    ],
                    "answer": None,
                    "provider": "serpapi",
                }
            ),
        )
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", AsyncMock())

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(bd_user, db_stub, {"term": "CCP"})

        assert result["query"] == "CCP"
        assert result["answer"] == "A central counterparty clears trades."

    async def test_ungated_for_no_access_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            chatbot_tools,
            "search_web",
            AsyncMock(
                return_value={
                    "results": [],
                    "answer": "A definition.",
                    "provider": "serpapi",
                }
            ),
        )
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", AsyncMock())

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        result = await tool.execute(
            no_access_user, db_stub, {"query": "anything"}
        )

        assert result.get("error") != "no_access"
        assert result["source"] == "public_web"

    async def test_limit_clamped_to_max(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, int] = {}

        async def fake_search_web(query: str, *, limit: int) -> dict[str, Any]:
            captured["limit"] = limit
            return {"results": [], "answer": "x", "provider": "serpapi"}

        monkeypatch.setattr(
            chatbot_tools, "get_learned_term", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(chatbot_tools, "search_web", fake_search_web)
        monkeypatch.setattr(chatbot_tools, "upsert_learned_term", AsyncMock())

        tool = chatbot_tools.TOOL_REGISTRY["research_term"]
        await tool.execute(bd_user, db_stub, {"query": "x", "limit": 999})

        assert captured["limit"] == chatbot_tools.WEB_RESEARCH_LIMIT_MAX


class TestFindDualRegisteredFirms:
    """Lists firms registered as both a BD and an RIA (matched on CRD).
    Gated on MASTER_LIST; the cross-table query is mocked here."""

    async def test_returns_items_with_both_links(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from types import SimpleNamespace

        firm = SimpleNamespace(
            name="Dually Inc",
            crd_number="12345",
            cik="0001",
            city="New York",
            state="NY",
            lead_priority="hot",
            broker_dealer_id=7,
            advisor_id=42,
        )
        monkeypatch.setattr(
            chatbot_tools,
            "list_dual_registered_firms",
            AsyncMock(return_value=([firm], 1)),
        )

        tool = chatbot_tools.TOOL_REGISTRY["find_dual_registered_firms"]
        result = await tool.execute(bd_user, db_stub, {})

        assert result["total_matched"] == 1
        item = result["items"][0]
        assert item["firm_name"] == "Dually Inc"
        assert item["link"] == "/master-list/7"
        assert item["advisor_link"] == "/advisor-list/42"

    async def test_no_access_user_denied(
        self, no_access_user: AuthenticatedUser, db_stub: object
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["find_dual_registered_firms"]
        result = await tool.execute(no_access_user, db_stub, {})
        assert result["error"] == "no_access"

    async def test_empty_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        monkeypatch.setattr(
            chatbot_tools,
            "list_dual_registered_firms",
            AsyncMock(return_value=([], 0)),
        )
        tool = chatbot_tools.TOOL_REGISTRY["find_dual_registered_firms"]
        result = await tool.execute(bd_user, db_stub, {})
        assert result["items"] == []
        assert result["total_matched"] == 0

    async def test_filters_and_limit_forwarded_and_clamped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bd_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake(
            db: Any, *, state: Any = None, search: Any = None, limit: int = 10
        ) -> tuple[list[Any], int]:
            captured.update(state=state, search=search, limit=limit)
            return [], 0

        monkeypatch.setattr(chatbot_tools, "list_dual_registered_firms", fake)
        tool = chatbot_tools.TOOL_REGISTRY["find_dual_registered_firms"]
        await tool.execute(
            bd_user, db_stub, {"state": "tx", "search": " Acme ", "limit": 999}
        )

        assert captured["state"] == "tx"
        assert captured["search"] == "Acme"
        assert captured["limit"] == chatbot_tools.DUAL_REGISTRATION_LIMIT_MAX
