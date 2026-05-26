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
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    MASTER_LIST,
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
        assert set(item.keys()) == set(chatbot_tools._BD_SUMMARY_KEYS)
        assert item["name"] == "Apex Clearing Corporation"
        # Decimal must have been coerced to float by _jsonable.
        assert isinstance(item["latest_net_capital"], float)

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
        assert set(result["items"][0].keys()) == set(chatbot_tools._IA_SUMMARY_KEYS)

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
    for r in (r1, r2, r3, r4, r5, r6, r7, r8, r9):
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
    }


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
        assert set(result["items"][0].keys()) == set(chatbot_tools._II_SUMMARY_KEYS)
        # total_aum (Decimal) coerced to float by _jsonable.
        assert isinstance(result["items"][0]["total_aum"], float)

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
        await tool.execute(
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
        await tool.execute(
            ia_user,
            db_stub,
            {"state": "NY", "min_regulatory_aum": 1_000_000_000},
        )
        assert captured["states"] == ["NY"]
        assert captured["min_regulatory_aum"] == 1_000_000_000.0

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
