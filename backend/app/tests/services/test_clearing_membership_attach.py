"""Integration tests: clearing-membership attach on the list services.

Hits a real Postgres. Verifies the master-list / advisor-list services
attach ``member_agencies`` + ``clearing_membership_checked_at`` correctly
and do so with exactly ONE membership query per page (no N+1), mirroring
the batched ``_build_list_unknown_reasons`` pattern.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, event

from app.db.session import SessionLocal, engine
from app.models.broker_dealer import BrokerDealer
from app.models.clearing_agency_membership import ClearingAgencyMembership
from app.models.investment_advisor import InvestmentAdvisor
from app.services.broker_dealers import BrokerDealerRepository
from app.services.investment_advisors import InvestmentAdvisorRepository

pytestmark = pytest.mark.integration


class _MembershipQueryCounter:
    """Counts statements that touch ``clearing_agency_memberships`` so a
    test can prove the list path issues exactly one batched query."""

    def __init__(self) -> None:
        self.count = 0

    def _on(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if "clearing_agency_memberships" in statement.lower():
            self.count += 1

    def __enter__(self) -> "_MembershipQueryCounter":
        event.listen(engine.sync_engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(engine.sync_engine, "before_cursor_execute", self._on)


async def _add_membership(session, *, bd_id=None, advisor_id=None, agency, status="active") -> None:
    session.add(
        ClearingAgencyMembership(
            broker_dealer_id=bd_id,
            advisor_id=advisor_id,
            agency=agency,
            member_name_raw="Seed Firm",
            source_file="test.csv",
            match_method="exact_normalized",
            match_confidence=100.0,
            status=status,
        )
    )


async def test_bd_list_attaches_memberships_without_n_plus_1() -> None:
    token = secrets.token_hex(5)
    now = datetime.now(timezone.utc)
    ids: dict[str, int] = {}
    async with SessionLocal() as session:
        member = BrokerDealer(name=f"{token} Member BD", matched_source="edgar", status="active", clearing_membership_checked_at=now)
        nonmember = BrokerDealer(name=f"{token} Checked Nonmember BD", matched_source="edgar", status="active", clearing_membership_checked_at=now)
        unchecked = BrokerDealer(name=f"{token} Unchecked BD", matched_source="edgar", status="active")
        review_only = BrokerDealer(name=f"{token} Review BD", matched_source="edgar", status="active", clearing_membership_checked_at=now)
        session.add_all([member, nonmember, unchecked, review_only])
        await session.commit()
        for bd in (member, nonmember, unchecked, review_only):
            await session.refresh(bd)
            ids[bd.name] = bd.id
        await _add_membership(session, bd_id=member.id, agency="OCC")
        await _add_membership(session, bd_id=member.id, agency="DTC")
        await _add_membership(session, bd_id=review_only.id, agency="NSCC", status="needs_review")
        await session.commit()

    try:
        repo = BrokerDealerRepository()
        async with SessionLocal() as session:
            with _MembershipQueryCounter() as counter:
                resp = await repo.list_broker_dealers(
                    session,
                    search=token,
                    states=[], statuses=[], health_statuses=[], lead_priorities=[],
                    clearing_partners=[], clearing_types=[], types_of_business=[],
                    list_mode="all", sort_by="name", sort_dir="asc", page=1, limit=50,
                )
        by_id = {item.id: item for item in resp.items}
        assert len(by_id) == 4
        assert by_id[ids[f"{token} Member BD"]].member_agencies == ["DTC", "OCC"]
        assert by_id[ids[f"{token} Member BD"]].clearing_membership_checked_at is not None
        assert by_id[ids[f"{token} Checked Nonmember BD"]].member_agencies == []
        assert by_id[ids[f"{token} Checked Nonmember BD"]].clearing_membership_checked_at is not None
        assert by_id[ids[f"{token} Unchecked BD"]].member_agencies == []
        assert by_id[ids[f"{token} Unchecked BD"]].clearing_membership_checked_at is None
        # needs_review must NOT surface as an active membership label
        assert by_id[ids[f"{token} Review BD"]].member_agencies == []
        # One batched membership query for the whole page (no N+1).
        assert counter.count == 1
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(BrokerDealer).where(BrokerDealer.id.in_(list(ids.values()))))
            await session.commit()


async def test_ia_list_attaches_memberships_without_n_plus_1() -> None:
    token = secrets.token_hex(5)
    now = datetime.now(timezone.utc)
    ids: dict[str, int] = {}
    async with SessionLocal() as session:
        member = InvestmentAdvisor(name=f"{token} Dual IA", matched_source="iapd", status="active", clearing_membership_checked_at=now)
        plain = InvestmentAdvisor(name=f"{token} Plain IA", matched_source="iapd", status="active", clearing_membership_checked_at=now)
        session.add_all([member, plain])
        await session.commit()
        for ia in (member, plain):
            await session.refresh(ia)
            ids[ia.name] = ia.id
        await _add_membership(session, advisor_id=member.id, agency="OCC")
        await session.commit()

    try:
        repo = InvestmentAdvisorRepository()
        async with SessionLocal() as session:
            with _MembershipQueryCounter() as counter:
                resp = await repo.list_investment_advisors(
                    session,
                    search=token,
                    states=[], statuses=[], advisory_activities=[], client_types=[],
                    files_13f=None, min_regulatory_aum=None, max_regulatory_aum=None,
                    registered_after=None, registered_before=None,
                    sort_by="name", sort_dir="asc", page=1, limit=50,
                )
        by_id = {item.id: item for item in resp.items}
        assert len(by_id) == 2
        assert by_id[ids[f"{token} Dual IA"]].member_agencies == ["OCC"]
        assert by_id[ids[f"{token} Plain IA"]].member_agencies == []
        assert by_id[ids[f"{token} Plain IA"]].clearing_membership_checked_at is not None
        assert counter.count == 1
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(InvestmentAdvisor).where(InvestmentAdvisor.id.in_(list(ids.values()))))
            await session.commit()
