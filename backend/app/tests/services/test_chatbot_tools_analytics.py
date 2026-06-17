"""Tests for ``app.services.chatbot_tools_analytics`` — Doxie's aggregate tools.

Unlike ``test_chatbot_tools`` (which stubs the repo singletons), these tools
build their own SQL, so the suite runs them against a real in-memory SQLite
engine (the ``test_pipeline_reaper`` pattern) with seeded BrokerDealer /
InvestmentAdvisor / CompetitorProvider rows. The PG-only JSONB columns
compile as plain JSON under the sqlite dialect via the ``@compiles`` shim
below — a DDL-only concern; none of the queried columns are JSONB.

Covers the tool-wrapper contract: feature gating, invalid group_by/metric
combos, group-by + metric math (including the NULL → 'unknown' bucket and
canonical clearing-partner consolidation), and the clearing-partner firm
lookup with its limit / deep-link behavior.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Mapping

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.feature_permissions import INVESTMENT_ADVISORS, MASTER_LIST
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_partner_merge_suggestion import (
    ClearingPartnerMergeSuggestion,
)
from app.models.competitor_provider import CompetitorProvider
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services import chatbot_tools, chatbot_tools_analytics


@compiles(JSONB, "sqlite")
def _render_jsonb_as_json_on_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    """Let SQLite CREATE TABLE the PG models — JSONB columns become JSON."""
    return compiler.visit_JSON(type_, **kw)


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_user(*, features: list[str] | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=f"analytics-test-{secrets.token_hex(4)}",
        name="Analytics Tester",
        email="analytics-test@example.com",
        role="viewer",
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
def no_access_user() -> AuthenticatedUser:
    return _make_user(features=[])


_TABLES = (
    BrokerDealer.__table__,
    InvestmentAdvisor.__table__,
    CompetitorProvider.__table__,
    ClearingPartnerMergeSuggestion.__table__,
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.run_sync(table.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _bd(**overrides: Any) -> BrokerDealer:
    defaults: dict[str, Any] = {
        "name": "Seed Securities LLC",
        "status": "active",
        "matched_source": "edgar",
    }
    defaults.update(overrides)
    return BrokerDealer(**defaults)


def _ia(**overrides: Any) -> InvestmentAdvisor:
    defaults: dict[str, Any] = {
        "name": "Seed Advisors LP",
        "status": "active",
        "matched_source": "iapd",
        "files_13f": False,
    }
    defaults.update(overrides)
    return InvestmentAdvisor(**defaults)


async def _seed(db: AsyncSession, *rows: Any) -> None:
    db.add_all(rows)
    await db.commit()


async def _aggregate(
    user: AuthenticatedUser, db: AsyncSession, args: Mapping[str, Any]
) -> dict[str, Any]:
    tool = chatbot_tools.TOOL_REGISTRY["get_firm_aggregates"]
    return await tool.execute(user, db, args)


async def _by_partner(
    user: AuthenticatedUser, db: AsyncSession, args: Mapping[str, Any]
) -> dict[str, Any]:
    tool = chatbot_tools.TOOL_REGISTRY["list_firms_by_clearing_partner"]
    return await tool.execute(user, db, args)


# ── Registration ────────────────────────────────────────────────────────


def test_tools_registered_with_expected_metadata() -> None:
    for name in ("get_firm_aggregates", "list_firms_by_clearing_partner"):
        tool = chatbot_tools.TOOL_REGISTRY[name]
        assert tool.name == name
        assert tool.feature_key == MASTER_LIST
        # Cheap repo-class lookups: default timeout, dedup-cache friendly.
        assert tool.timeout_s is None
        assert tool.cacheable is True


# ── Feature gating ──────────────────────────────────────────────────────


class TestFeatureGates:
    async def test_bd_aggregates_denied_without_master_list(
        self, no_access_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            no_access_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "state"},
        )
        assert result["error"] == "no_access"
        assert MASTER_LIST in result["message"]

    async def test_ia_aggregates_gated_on_investment_advisors(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        # MASTER_LIST alone must not unlock the advisor registry.
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "investment_advisor", "group_by": "state"},
        )
        assert result["error"] == "no_access"
        assert INVESTMENT_ADVISORS in result["message"]

    async def test_clearing_partner_lookup_denied_without_master_list(
        self, no_access_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _by_partner(
            no_access_user, db_session, {"partner_name": "Pershing"}
        )
        assert result["error"] == "no_access"


# ── Argument validation ─────────────────────────────────────────────────


class TestAggregateArgValidation:
    async def test_unknown_entity_type(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            bd_user, db_session, {"entity_type": "hedge_fund", "group_by": "state"}
        )
        assert result["error"] == "invalid_args"

    async def test_bd_only_group_by_rejected_for_ia(
        self, ia_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            ia_user,
            db_session,
            {"entity_type": "investment_advisor", "group_by": "clearing_partner"},
        )
        assert result["error"] == "invalid_args"
        assert "group_by" in result["message"]

    async def test_unknown_group_by_rejected(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            bd_user, db_session, {"entity_type": "broker_dealer", "group_by": "city"}
        )
        assert result["error"] == "invalid_args"

    async def test_ia_metric_rejected_for_bd(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "state", "metric": "avg_aum"},
        )
        assert result["error"] == "invalid_args"
        assert "metric" in result["message"]

    async def test_bd_metric_rejected_for_ia(
        self, ia_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            ia_user,
            db_session,
            {
                "entity_type": "investment_advisor",
                "group_by": "state",
                "metric": "total_net_capital",
            },
        )
        assert result["error"] == "invalid_args"

    async def test_unknown_metric_rejected(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "state", "metric": "median"},
        )
        assert result["error"] == "invalid_args"

    async def test_files_13f_filter_rejected_for_bd(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "state", "files_13f": True},
        )
        assert result["error"] == "invalid_args"

    async def test_clearing_type_filter_rejected_for_ia(
        self, ia_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _aggregate(
            ia_user,
            db_session,
            {
                "entity_type": "investment_advisor",
                "group_by": "state",
                "clearing_type": "fully_disclosed",
            },
        )
        assert result["error"] == "invalid_args"


# ── get_firm_aggregates: happy paths ────────────────────────────────────


class TestFirmAggregates:
    async def test_bd_count_by_state_with_unknown_bucket(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _bd(name="TX One", state="TX"),
            _bd(name="TX Two", state="TX"),
            _bd(name="TX Three", state="TX"),
            _bd(name="CA One", state="CA"),
            _bd(name="CA Two", state="CA"),
            _bd(name="No State", state=None),
        )
        result = await _aggregate(
            bd_user, db_session, {"entity_type": "broker_dealer", "group_by": "state"}
        )

        assert result["total_firms"] == 6
        assert result["groups_total"] == 3
        assert result["groups"] == [
            {"group_value": "TX", "count": 3},
            {"group_value": "CA", "count": 2},
            {"group_value": "unknown", "count": 1},
        ]
        # list=all so the linked master-list total matches total_firms (the
        # default primary list hides deficient firms).
        assert result["list_link"] == "/master-list?list=all"

    async def test_filters_narrow_the_universe_and_echo_into_link(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _bd(name="TX Active", state="TX", status="active"),
            _bd(name="TX Inactive", state="TX", status="inactive"),
            _bd(name="CA Active", state="CA", status="active"),
        )
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "status", "state": "TX"},
        )

        assert result["total_firms"] == 2
        assert {g["group_value"]: g["count"] for g in result["groups"]} == {
            "active": 1,
            "inactive": 1,
        }
        assert result["list_link"] == "/master-list?state=TX&list=all"

    async def test_avg_and_total_net_capital_ignore_null_values(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _bd(name="NY Small", state="NY", latest_net_capital=Decimal("100000")),
            _bd(name="NY Big", state="NY", latest_net_capital=Decimal("300000")),
            _bd(name="NY Missing", state="NY", latest_net_capital=None),
        )
        avg = await _aggregate(
            bd_user,
            db_session,
            {
                "entity_type": "broker_dealer",
                "group_by": "state",
                "metric": "avg_net_capital",
            },
        )
        assert avg["groups"] == [
            {"group_value": "NY", "count": 3, "metric_value": 200000.0}
        ]

        total = await _aggregate(
            bd_user,
            db_session,
            {
                "entity_type": "broker_dealer",
                "group_by": "state",
                "metric": "total_net_capital",
            },
        )
        assert total["groups"] == [
            {"group_value": "NY", "count": 3, "metric_value": 400000.0}
        ]

    async def test_metric_ordering_puts_no_data_groups_last(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _bd(name="AA One", state="AA", latest_net_capital=Decimal("100000")),
            _bd(name="AA Two", state="AA", latest_net_capital=Decimal("300000")),
            _bd(name="BB One", state="BB", latest_net_capital=Decimal("1000000")),
            _bd(name="CC One", state="CC", latest_net_capital=None),
        )
        result = await _aggregate(
            bd_user,
            db_session,
            {
                "entity_type": "broker_dealer",
                "group_by": "state",
                "metric": "avg_net_capital",
            },
        )
        assert [g["group_value"] for g in result["groups"]] == ["BB", "AA", "CC"]
        assert result["groups"][0]["metric_value"] == 1000000.0
        assert result["groups"][1]["metric_value"] == 200000.0
        assert result["groups"][2]["metric_value"] is None

    async def test_clearing_partner_groups_consolidate_to_canonical_labels(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            CompetitorProvider(
                name="Pershing LLC",
                display_name="Pershing",
                aliases=["BNY Pershing"],
            ),
            _bd(name="Intro A", current_clearing_partner="PERSHING LLC"),
            _bd(name="Intro B", current_clearing_partner="PERSHING LLC"),
            _bd(name="Intro C", current_clearing_partner="BNY Pershing"),
            _bd(name="Intro D", current_clearing_partner="Apex Clearing Corporation"),
            _bd(name="Self Clearer", current_clearing_partner=None),
        )
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "clearing_partner"},
        )

        assert result["total_firms"] == 5
        # Raw variants fold under the registry display label, exactly like
        # the master-list dropdown; the NULL partner lands in 'unknown'.
        assert result["groups"] == [
            {"group_value": "Pershing", "count": 3},
            {"group_value": "Apex Clearing Corporation", "count": 1},
            {"group_value": "unknown", "count": 1},
        ]

    async def test_top_n_truncates_but_totals_cover_all_groups(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _bd(name="TX One", state="TX"),
            _bd(name="TX Two", state="TX"),
            _bd(name="CA One", state="CA"),
            _bd(name="NY One", state="NY"),
        )
        result = await _aggregate(
            bd_user,
            db_session,
            {"entity_type": "broker_dealer", "group_by": "state", "top_n": 2},
        )
        assert len(result["groups"]) == 2
        assert result["groups"][0] == {"group_value": "TX", "count": 2}
        assert result["groups_total"] == 3
        assert result["total_firms"] == 4

    async def test_ia_count_by_state_with_files_13f_filter(
        self, ia_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _ia(name="TX Filer", state="TX", files_13f=True),
            _ia(name="TX Non-Filer", state="TX", files_13f=False),
            _ia(name="CA Filer", state="CA", files_13f=True),
            _ia(name="Stateless Filer", state=None, files_13f=True),
        )
        result = await _aggregate(
            ia_user,
            db_session,
            {
                "entity_type": "investment_advisor",
                "group_by": "state",
                "files_13f": True,
            },
        )
        assert result["total_firms"] == 3
        assert {g["group_value"]: g["count"] for g in result["groups"]} == {
            "TX": 1,
            "CA": 1,
            "unknown": 1,
        }
        # files_13f=True is the advisor list's default scope — stripped.
        assert result["list_link"] == "/advisor-list"

    async def test_ia_avg_aum(
        self, ia_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await _seed(
            db_session,
            _ia(name="MA One", state="MA", regulatory_aum=Decimal("1000000000")),
            _ia(name="MA Two", state="MA", regulatory_aum=Decimal("3000000000")),
        )
        result = await _aggregate(
            ia_user,
            db_session,
            {
                "entity_type": "investment_advisor",
                "group_by": "state",
                "metric": "avg_aum",
            },
        )
        assert result["groups"] == [
            {"group_value": "MA", "count": 2, "metric_value": 2000000000.0}
        ]
        # No files_13f filter → the whole table was counted, so the link
        # must broaden the FE's hard 13F-only default to 'all'.
        assert result["list_link"] == "/advisor-list?files_13f=all"


# ── list_firms_by_clearing_partner ──────────────────────────────────────


class TestListFirmsByClearingPartner:
    async def _seed_pershing_universe(self, db: AsyncSession) -> None:
        await _seed(
            db,
            CompetitorProvider(
                name="Pershing LLC",
                display_name="Pershing",
                aliases=["BNY Pershing"],
            ),
            _bd(
                name="Big Intro",
                crd_number="111",
                state="NY",
                current_clearing_partner="PERSHING LLC",
                current_clearing_type="fully_disclosed",
                latest_net_capital=Decimal("9000000"),
            ),
            _bd(
                name="Mid Intro",
                crd_number="222",
                state="TX",
                current_clearing_partner="BNY Pershing",
                current_clearing_type="fully_disclosed",
                latest_net_capital=Decimal("5000000"),
            ),
            _bd(
                name="Small Intro",
                crd_number="333",
                state="CA",
                current_clearing_partner="PERSHING LLC",
                current_clearing_type="omnibus",
                latest_net_capital=None,
            ),
            _bd(
                name="Apex Intro",
                crd_number="444",
                state="IL",
                current_clearing_partner="Apex Clearing Corporation",
                current_clearing_type="fully_disclosed",
                latest_net_capital=Decimal("7000000"),
            ),
        )

    async def test_substring_match_expands_canonical_group(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await self._seed_pershing_universe(db_session)
        result = await _by_partner(
            bd_user, db_session, {"partner_name": "pershing"}
        )

        assert result["matched_partners"] == ["Pershing"]
        assert result["total_matched"] == 3
        # Master-list default ordering: latest_net_capital desc, NULLs last.
        assert [item["name"] for item in result["items"]] == [
            "Big Intro",
            "Mid Intro",
            "Small Intro",
        ]
        first = result["items"][0]
        assert first["crd_number"] == "111"
        assert first["state"] == "NY"
        assert first["clearing_type"] == "fully_disclosed"
        assert first["link"] == f"/master-list/{first['id']}"
        assert result["list_link"] == "/master-list?clearing_partner=Pershing&list=all"

    async def test_limit_caps_rows_but_not_total(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await self._seed_pershing_universe(db_session)
        result = await _by_partner(
            bd_user, db_session, {"partner_name": "Pershing", "limit": 1}
        )
        assert result["total_matched"] == 3
        assert [item["name"] for item in result["items"]] == ["Big Intro"]

    async def test_long_tail_partner_matches_by_raw_value(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await self._seed_pershing_universe(db_session)
        result = await _by_partner(bd_user, db_session, {"partner_name": "apex"})
        assert result["matched_partners"] == ["Apex Clearing Corporation"]
        assert result["total_matched"] == 1
        assert result["items"][0]["name"] == "Apex Intro"

    async def test_no_match_returns_note_not_error(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        await self._seed_pershing_universe(db_session)
        result = await _by_partner(
            bd_user, db_session, {"partner_name": "Wells Fargo"}
        )
        assert "error" not in result
        assert result["matched_partners"] == []
        assert result["items"] == []
        assert result["total_matched"] == 0
        assert "No clearing partner" in result["note"]

    async def test_missing_partner_name_is_invalid(
        self, bd_user: AuthenticatedUser, db_session: AsyncSession
    ) -> None:
        result = await _by_partner(bd_user, db_session, {"partner_name": "   "})
        assert result["error"] == "invalid_args"


# ── Module-level clamps ─────────────────────────────────────────────────


def test_clamp_int_bounds() -> None:
    clamp = chatbot_tools_analytics._clamp_int
    assert clamp(None, 10, 25) == 10
    assert clamp("not a number", 10, 25) == 10
    assert clamp(0, 10, 25) == 1
    assert clamp(999, 10, 25) == 25
