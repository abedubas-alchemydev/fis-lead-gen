"""Unit test for the entity-filer guard in the institutional-investor
gap-fill runner.

Apollo / PDL are person-only APIs and can't resolve an entity name like
"Citadel Advisors LLC". The runner short-circuits before calling the
discovery chain when the contact ``name`` matches the entity regex
shared with the Form 4 path. This test stubs the chain so we can
assert the call DID NOT happen and the run finalizes with a skip
message in the summary.
"""

from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investor_contact import InvestorContact
from app.models.pipeline_run import PipelineRun
from app.services import investor_contact_gap_fill

pytestmark = pytest.mark.integration


async def test_ii_gap_fill_skips_entity_shaped_contact_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain_calls: list[dict] = []

    async def _fake_walk_chain(
        entity_type: str, **kwargs: object
    ) -> None:
        chain_calls.append({"entity_type": entity_type, **kwargs})
        return None

    monkeypatch.setattr(
        investor_contact_gap_fill, "_walk_chain", _fake_walk_chain
    )

    token = secrets.token_hex(4)
    async with SessionLocal() as session:
        investor = InstitutionalInvestor(name=f"Test II {token}")
        session.add(investor)
        await session.commit()
        await session.refresh(investor)
        # One entity-shaped contact (skipped) + one person-shaped contact
        # (chain is called but returns None per the stub).
        entity_contact = InvestorContact(
            investor_id=investor.id,
            name="Citadel Advisors LLC",
            title="Entity Filer",
            email=None,
            phone=None,
        )
        person_contact = InvestorContact(
            investor_id=investor.id,
            name="Jane Doe",
            title="CIO",
            email=None,
            phone=None,
        )
        session.add_all([entity_contact, person_contact])
        await session.commit()

        run = PipelineRun(
            pipeline_name="institutional_investor_gap_fill_contacts",
            trigger_source="test",
            status="queued",
            total_items=1,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps({"investor_id": investor.id, "stage": "queued"}),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id
        investor_id = investor.id

    try:
        await investor_contact_gap_fill._run_gap_fill_ii_contacts(
            run_id=run_id, investor_id=investor_id, trigger_source="test"
        )

        # Only the person-shaped contact reached the chain.
        assert len(chain_calls) == 1
        assert chain_calls[0]["first_name"] == "Jane"
        assert chain_calls[0]["last_name"] == "Doe"

        async with SessionLocal() as session:
            terminal = (
                await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
            ).scalar_one()
            assert terminal.status == "completed"
            assert terminal.notes is not None
            notes = json.loads(terminal.notes)
            assert "entity-shaped skipped" in (notes.get("summary") or "")

            # Cooldown stamp must have been written.
            ii = await session.get(InstitutionalInvestor, investor_id)
            assert ii is not None
            assert ii.last_gap_fill_attempt_at is not None
    finally:
        async with SessionLocal() as session:
            row = await session.get(InstitutionalInvestor, investor_id)
            if row is not None:
                await session.delete(row)
            run = await session.get(PipelineRun, run_id)
            if run is not None:
                await session.delete(run)
            await session.commit()
