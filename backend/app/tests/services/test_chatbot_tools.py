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
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.feature_permissions import (
    ALERTS,
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    INVESTORS,
    MASTER_LIST,
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
        # Each hit carries a deep-link; the wrapper carries a
        # list-link that re-runs the natural-language query as a name
        # search on the master list (best-effort fallback for the user).
        assert item["link"] == "/master-list/42"
        assert result["list_link"] == "/master-list?q=small+introducing+brokers"

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


class TestAskVault:
    async def test_happy_path_owner_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        # Mock the DB owner check + the retrieve_chunks call. The owner
        # query is a raw select(VaultFolder.user_id); we shim
        # db.execute to return the calling user's id.
        from unittest.mock import MagicMock

        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = vault_user.id

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        from app.services.vault_retrieval import RetrievedChunk

        async def fake_retrieve(
            *, folder_id: int, query: str, db: Any, top_k: int
        ) -> list[Any]:
            assert folder_id == 7
            assert "playbook" in query
            return [
                RetrievedChunk(
                    chunk_id=1,
                    file_id=10,
                    chunk_index=0,
                    text="Step 1: confirm clearing partner before outreach.",
                    original_filename="compliance_playbook.pdf",
                    similarity=0.87,
                ),
            ]

        monkeypatch.setattr(chatbot_tools, "retrieve_chunks", fake_retrieve)

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user,
            db_mock,
            {"query": "what's our outreach playbook?", "folder_id": 7},
        )
        assert result["total_matched"] == 1
        item = result["items"][0]
        assert item["original_filename"] == "compliance_playbook.pdf"
        assert item["similarity"] == 0.87
        # Vault link is the bare /vault page (no per-folder route yet).
        assert result["link"] == "/vault"

    async def test_non_owner_returns_opaque_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        """Cross-tenant attempt looks identical to 'folder doesn't exist'
        — never reveal the existence of someone else's folder."""
        from unittest.mock import MagicMock

        owner_result = MagicMock()
        owner_result.scalar_one_or_none.return_value = "some-other-user"

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=owner_result)

        called = AsyncMock()
        monkeypatch.setattr(chatbot_tools, "retrieve_chunks", called)

        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            vault_user, db_mock, {"query": "x", "folder_id": 7}
        )
        assert result["error"] == "not_found"
        called.assert_not_called()

    async def test_missing_folder_returns_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        from unittest.mock import MagicMock

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
        result = await tool.execute(
            vault_user, db_stub, {"query": "  ", "folder_id": 7}
        )
        assert result["error"] == "invalid_args"

    async def test_403_returns_no_access(
        self,
        no_access_user: AuthenticatedUser,
        db_stub: object,
    ) -> None:
        tool = chatbot_tools.TOOL_REGISTRY["ask_vault"]
        result = await tool.execute(
            no_access_user, db_stub, {"query": "x", "folder_id": 7}
        )
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
