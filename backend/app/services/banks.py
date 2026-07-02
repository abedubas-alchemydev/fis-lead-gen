"""Bank repository — query + upsert layer for the bank-charter vertical.

Mirrors ``services/broker_dealers.py``: an async repository the endpoints
call for reads, plus the idempotent upsert paths the watcher
(``scripts/watch_bank_charters.py``) calls for writes.

Write-side keys:
- FDIC rows upsert on ``fdic_cert`` (unique index) via
  ``INSERT ... ON CONFLICT (fdic_cert) DO UPDATE``.
- OCC application rows upsert on ``occ_control_number`` (unique index).
- OCC action events upsert on ``(bank_id, action, action_date)`` DO
  NOTHING — re-running an overlapping date window is a no-op.

Status transitions only move FORWARD along the lifecycle
(``CHARTER_STATUS_RANK``): a re-seen 'Receipt' can never demote an
'approved' bank back to 'pending'.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from math import ceil

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bank import Bank, BankApplicationEvent
from app.schemas.bank import BankListMeta, BankListResponse
from app.services.fdic_bankfind import FdicInstitutionRecord
from app.services.occ_cas import (
    CHARTER_STATUS_RANK,
    OccCharterFiling,
    normalize_bank_name,
)

logger = logging.getLogger(__name__)

ALLOWED_SORT_FIELDS = {
    "name": Bank.name,
    "state": Bank.state,
    "fdic_cert": Bank.fdic_cert,
    "occ_control_number": Bank.occ_control_number,
    "charter_authority": Bank.charter_authority,
    "charter_status": Bank.charter_status,
    "established_date": Bank.established_date,
    "application_received_date": Bank.application_received_date,
    "last_action_date": Bank.last_action_date,
    "asset": Bank.asset,
    "deposits": Bank.deposits,
    "offices": Bank.offices,
}


def _advance_status(current: str | None, incoming: str | None) -> str | None:
    """Forward-only lifecycle move; None means 'leave as is'."""
    if incoming is None:
        return None
    if current is None:
        return incoming
    if CHARTER_STATUS_RANK.get(incoming, -1) >= CHARTER_STATUS_RANK.get(current, -1):
        return incoming
    return None


class BankRepository:
    # ── Read side (endpoints) ──────────────────────────────────────────

    async def list_banks(
        self,
        db: AsyncSession,
        *,
        search: str | None,
        states: list[str],
        charter_authorities: list[str],
        charter_statuses: list[str],
        established_after: date | None,
        established_before: date | None,
        digital_assets: bool | None = None,
        sort_by: str,
        sort_dir: str,
        page: int,
        limit: int,
    ) -> BankListResponse:
        filters = []
        if search:
            like_value = f"%{search.strip()}%"
            filters.append(
                or_(
                    Bank.name.ilike(like_value),
                    Bank.city.ilike(like_value),
                    cast(Bank.fdic_cert, String).ilike(like_value),
                    cast(Bank.occ_control_number, String).ilike(like_value),
                    cast(Bank.occ_charter_number, String).ilike(like_value),
                )
            )
        if states:
            filters.append(Bank.state.in_(states))
        if charter_authorities:
            filters.append(Bank.charter_authority.in_(charter_authorities))
        if charter_statuses:
            filters.append(Bank.charter_status.in_(charter_statuses))
        # Tri-state: None = no filter; True = digital-assets-tagged only;
        # False = explicitly untagged only.
        if digital_assets is not None:
            filters.append(Bank.digital_assets.is_(digital_assets))
        # NULL established_date never satisfies a range comparison, so
        # pending applications drop out of an established-window filter
        # automatically — same convention as the BD registered_after filter.
        if established_after is not None:
            filters.append(Bank.established_date >= established_after)
        if established_before is not None:
            filters.append(Bank.established_date <= established_before)

        count_stmt = select(func.count(Bank.id))
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await db.execute(count_stmt)).scalar_one())

        sort_column = ALLOWED_SORT_FIELDS.get(sort_by, Bank.established_date)
        ordering = (
            sort_column.desc().nullslast()
            if sort_dir == "desc"
            else sort_column.asc().nullslast()
        )
        offset = (page - 1) * limit
        data_stmt = select(Bank)
        if filters:
            data_stmt = data_stmt.where(*filters)
        data_stmt = data_stmt.order_by(ordering, Bank.id.asc()).offset(offset).limit(limit)
        items = (await db.execute(data_stmt)).scalars().all()

        return BankListResponse(
            items=items,
            meta=BankListMeta(
                page=page,
                limit=limit,
                total=total,
                total_pages=max(1, ceil(total / limit)) if limit else 1,
            ),
        )

    async def get_bank(self, db: AsyncSession, bank_id: int) -> Bank | None:
        stmt = (
            select(Bank)
            .where(Bank.id == bank_id)
            .options(selectinload(Bank.application_events))
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_states(self, db: AsyncSession) -> list[str]:
        stmt = (
            select(Bank.state)
            .where(Bank.state.is_not(None))
            .distinct()
            .order_by(Bank.state.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [row for row in rows if row]

    async def count_all(self, db: AsyncSession) -> int:
        return int((await db.execute(select(func.count(Bank.id)))).scalar_one())

    # ── Write side (watcher) ───────────────────────────────────────────

    async def upsert_fdic_institutions(
        self, db: AsyncSession, records: list[FdicInstitutionRecord]
    ) -> int:
        """Idempotent upsert keyed on fdic_cert. Returns rows written.

        FDIC is authoritative for everything it reports on an OPENED
        institution, so on conflict the FDIC-owned columns overwrite.
        OCC-owned columns (occ_control_number, application dates, events)
        are never touched here. An ACTIVE institution is by definition
        'opened'; the forward-only guard is enforced in SQL via the rank
        CASE so a (theoretical) FDIC re-list can't demote 'opened'.
        """
        if not records:
            return 0
        # Collapse duplicate certs within the batch (institutions-window +
        # history-corroboration union can surface a cert twice): one
        # ``INSERT ... ON CONFLICT (fdic_cert)`` cannot affect the same row
        # twice (CardinalityViolation aborts the whole batch). Last wins.
        by_cert: dict[str, FdicInstitutionRecord] = {}
        for record in records:
            by_cert[record.cert] = record
        records = list(by_cert.values())
        now = datetime.now(timezone.utc)
        values = []
        for record in records:
            values.append(
                {
                    "fdic_cert": record.cert,
                    "fed_rssd": record.fed_rssd,
                    "name": record.name,
                    "address": record.address,
                    "city": record.city,
                    "state": record.state,
                    "zip": record.zip,
                    "website": record.website,
                    "charter_authority": record.charter_authority,
                    "regulator": record.regulator,
                    "bkclass": record.bkclass,
                    # An FDIC-certificated institution necessarily opened;
                    # a later closure is carried by active=False, not by a
                    # lifecycle demotion (the enum has no 'closed').
                    "charter_status": "opened",
                    "established_date": record.established_date,
                    "insured_date": record.insured_date,
                    "asset": record.asset,
                    "deposits": record.deposits,
                    "offices": record.offices,
                    "active": record.active,
                    "source": "fdic",
                    "fdic_checked_at": now,
                }
            )
        stmt = insert(Bank).values(values)
        excluded = stmt.excluded
        upsert = stmt.on_conflict_do_update(
            index_elements=[Bank.fdic_cert],
            set_={
                "fed_rssd": func.coalesce(excluded.fed_rssd, Bank.fed_rssd),
                "name": excluded.name,
                "address": func.coalesce(excluded.address, Bank.address),
                "city": func.coalesce(excluded.city, Bank.city),
                "state": func.coalesce(excluded.state, Bank.state),
                "zip": func.coalesce(excluded.zip, Bank.zip),
                "website": func.coalesce(excluded.website, Bank.website),
                "charter_authority": func.coalesce(excluded.charter_authority, Bank.charter_authority),
                "regulator": func.coalesce(excluded.regulator, Bank.regulator),
                "bkclass": func.coalesce(excluded.bkclass, Bank.bkclass),
                "established_date": func.coalesce(excluded.established_date, Bank.established_date),
                "insured_date": func.coalesce(excluded.insured_date, Bank.insured_date),
                "asset": func.coalesce(excluded.asset, Bank.asset),
                "deposits": func.coalesce(excluded.deposits, Bank.deposits),
                "offices": func.coalesce(excluded.offices, Bank.offices),
                "active": func.coalesce(excluded.active, Bank.active),
                # Forward-only status: apply the incoming status only when
                # its lifecycle rank is >= the stored one.
                "charter_status": _status_advance_sql(excluded.charter_status),
                # 'occ' + fdic -> 'fdic+occ'; plain fdic rows stay 'fdic'.
                "source": _merge_source_sql("fdic"),
                "fdic_checked_at": excluded.fdic_checked_at,
                "updated_at": func.now(),
            },
        )
        await db.execute(upsert)
        return len(values)

    async def upsert_occ_filings(
        self, db: AsyncSession, filings: list[OccCharterFiling]
    ) -> tuple[int, int]:
        """Upsert OCC application rows + their action events.

        Returns ``(banks_touched, events_inserted)``. Row-at-a-time on
        purpose: nightly volume is tiny (single-digit filings), and the
        per-filing flow needs the bank id for the event insert anyway.
        """
        if not filings:
            return (0, 0)
        now = datetime.now(timezone.utc)
        banks_touched: set[int] = set()
        events_inserted = 0

        # Group actions per control number so one bank row absorbs all of
        # its window's actions in order.
        by_control: dict[str, list[OccCharterFiling]] = {}
        for filing in filings:
            by_control.setdefault(filing.control_number, []).append(filing)

        for control_number, group in by_control.items():
            group.sort(key=lambda f: (f.action_date or date.min))
            first = group[0]
            bank = (
                await db.execute(
                    select(Bank).where(Bank.occ_control_number == control_number)
                )
            ).scalar_one_or_none()
            if bank is None:
                bank = Bank(
                    occ_control_number=control_number,
                    name=first.bank_name,
                    address=first.address,
                    city=first.city,
                    state=first.state,
                    zip=first.zip,
                    charter_authority="OCC",
                    charter_status="pending",
                    source="occ",
                )
                db.add(bank)
                await db.flush()  # need bank.id for events
            else:
                # Refresh address fields only when CAS has real values
                # (parse layer already nulls the 'TBD' placeholders).
                # FDIC owns the institution name once the row is reconciled
                # (has a cert): the CAS spelling ('World Liberty TR CO, NA')
                # must never overwrite the FDIC legal name on refresh.
                if bank.fdic_cert is None:
                    bank.name = first.bank_name or bank.name
                bank.address = first.address or bank.address
                bank.city = first.city or bank.city
                bank.state = first.state or bank.state
                bank.zip = first.zip or bank.zip
                if bank.source == "fdic":
                    bank.source = "fdic+occ"

            for filing in group:
                advanced = _advance_status(bank.charter_status, filing.charter_status)
                if advanced is not None:
                    bank.charter_status = advanced
                if "receipt" in filing.action.lower() and filing.action_date is not None:
                    if (
                        bank.application_received_date is None
                        or filing.action_date < bank.application_received_date
                    ):
                        bank.application_received_date = filing.action_date
                if filing.action_date is not None and (
                    bank.last_action_date is None or filing.action_date > bank.last_action_date
                ):
                    bank.last_action_date = filing.action_date

                if filing.action_date is None:
                    # NULL action_date bypasses the unique constraint
                    # (NULL != NULL in Postgres), so dedupe manually.
                    exists_stmt = select(BankApplicationEvent.id).where(
                        BankApplicationEvent.bank_id == bank.id,
                        BankApplicationEvent.action == filing.action,
                        BankApplicationEvent.action_date.is_(None),
                    )
                    if (await db.execute(exists_stmt)).first() is not None:
                        continue
                # RETURNING makes the inserted-count exact: rows come back
                # only for actual inserts (DO NOTHING suppresses them), and
                # psycopg3 can report rowcount=-1 on this statement shape.
                event_stmt = (
                    insert(BankApplicationEvent)
                    .values(
                        bank_id=bank.id,
                        action=filing.action,
                        action_date=filing.action_date,
                        filing_type=filing.filing_type,
                        source_url=filing.details_url,
                        raw=filing.raw,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_bank_application_events_bank_action_date"
                    )
                    .returning(BankApplicationEvent.id)
                )
                result = await db.execute(event_stmt)
                events_inserted += len(result.fetchall())

            bank.occ_checked_at = now
            banks_touched.add(bank.id)

        return (len(banks_touched), events_inserted)

    async def list_unreconciled_occ_banks(self, db: AsyncSession) -> list[Bank]:
        """OCC application rows that still lack an FDIC identity.

        The reconciliation pass in the watcher tries to link these to a
        directory row (charter number / CERT) once the bank opens.
        Withdrawn/rescinded applications are excluded — they will never
        gain an FDIC cert.
        """
        stmt = select(Bank).where(
            Bank.occ_control_number.is_not(None),
            Bank.fdic_cert.is_(None),
            Bank.charter_status.notin_(("withdrawn", "rescinded")),
        )
        return list((await db.execute(stmt)).scalars().all())

    async def merge_occ_bank_into_fdic_row(
        self, db: AsyncSession, occ_bank: Bank, fdic_bank: Bank
    ) -> Bank:
        """Fold an OCC application row into the FDIC row for the SAME bank.

        Runs when reconciliation discovers that the opened FDIC institution
        was inserted as its own row before the OCC application row gained
        its cert (both upsert keys are unique, so the pair can coexist
        until now). The FDIC row survives (it holds the richer operating
        data); the OCC identifiers, application dates, and events move
        onto it; the OCC row is deleted.

        Refuses (log + no-op) when the target row already carries a
        DIFFERENT occ_control_number — that would cross-link two distinct
        applications onto one bank; both rows are left for a human.
        """
        if (
            fdic_bank.occ_control_number is not None
            and fdic_bank.occ_control_number != occ_bank.occ_control_number
        ):
            logger.warning(
                "banks: NOT merging OCC row (id=%s, control=%s) into FDIC row "
                "(id=%s, cert=%s): target already carries a different "
                "occ_control_number=%s — refusing to cross-link two applications",
                occ_bank.id, occ_bank.occ_control_number,
                fdic_bank.id, fdic_bank.fdic_cert, fdic_bank.occ_control_number,
            )
            return occ_bank
        # Two-step handover of the unique key: NULL the source and FLUSH
        # before the target takes the value. A single flush would batch both
        # UPDATEs and may order "target gains X" before "source releases X",
        # tripping ix_banks_occ_control_number immediately (the constraint
        # is not deferred) — observed live against Postgres 16.
        moved_control_number = occ_bank.occ_control_number
        occ_bank.occ_control_number = None
        await db.flush()  # release the unique occ_control_number first
        fdic_bank.occ_control_number = moved_control_number
        fdic_bank.occ_charter_number = occ_bank.occ_charter_number or fdic_bank.occ_charter_number
        # Institutions-API enrichment stamped on the application row moments
        # before this merge must survive onto the FDIC row.
        fdic_bank.lei = occ_bank.lei or fdic_bank.lei
        fdic_bank.charter_type = occ_bank.charter_type or fdic_bank.charter_type
        fdic_bank.application_received_date = (
            occ_bank.application_received_date or fdic_bank.application_received_date
        )
        fdic_bank.last_action_date = max(
            (d for d in (occ_bank.last_action_date, fdic_bank.last_action_date) if d is not None),
            default=None,
        )
        fdic_bank.occ_checked_at = occ_bank.occ_checked_at or fdic_bank.occ_checked_at
        fdic_bank.source = "fdic+occ"
        advanced = _advance_status(fdic_bank.charter_status, occ_bank.charter_status)
        if advanced is not None:
            fdic_bank.charter_status = advanced

        # Move the events; the (bank_id, action, action_date) key stays
        # unique because the FDIC row had no OCC events of its own.
        events = (
            await db.execute(
                select(BankApplicationEvent).where(BankApplicationEvent.bank_id == occ_bank.id)
            )
        ).scalars().all()
        for event in events:
            event.bank_id = fdic_bank.id
        await db.delete(occ_bank)
        await db.flush()
        logger.info(
            "banks: merged OCC row (control=%s) into FDIC row (cert=%s, id=%d)",
            fdic_bank.occ_control_number, fdic_bank.fdic_cert, fdic_bank.id,
        )
        return fdic_bank

    async def find_by_cert(self, db: AsyncSession, cert: str) -> Bank | None:
        return (
            await db.execute(select(Bank).where(Bank.fdic_cert == cert))
        ).scalar_one_or_none()

    async def find_fdic_candidates_by_name_state(
        self, db: AsyncSession, name: str, state: str | None
    ) -> list[Bank]:
        """Conservative fallback candidates for OCC→FDIC reconciliation:
        FDIC-sourced rows in the same state whose normalized name matches
        exactly. Callers must require len(...) == 1 before linking."""
        stmt = select(Bank).where(Bank.fdic_cert.is_not(None))
        if state:
            stmt = stmt.where(Bank.state == state)
        target = normalize_bank_name(name)
        return [
            bank
            for bank in (await db.execute(stmt)).scalars().all()
            if normalize_bank_name(bank.name) == target
        ]

    async def find_banks_by_occ_charter_number(
        self, db: AsyncSession, charter_number: str
    ) -> list[Bank]:
        """All banks carrying this OCC charter number (exact match).

        Strong key for the watcher's digital-assets SEED entries. The
        column is not DB-unique (it is stamped by two independent paths:
        reconciliation and the directory), so callers must require a
        UNIQUE match before writing — same policy as the name matchers.
        """
        stmt = select(Bank).where(Bank.occ_charter_number == charter_number)
        return list((await db.execute(stmt)).scalars().all())

    async def find_banks_by_normalized_name(
        self, db: AsyncSession, name: str
    ) -> list[Bank]:
        """All banks (any source) whose normalized name matches exactly.

        Used by the digital-assets tagger, whose source page carries a name
        but no state / control number / cert. Callers must require a UNIQUE
        match before writing — same conservatism as the FDIC fallback.
        """
        target = normalize_bank_name(name)
        if not target:
            return []
        return [
            bank
            for bank in (await db.execute(select(Bank))).scalars().all()
            if normalize_bank_name(bank.name) == target
        ]

    def apply_digital_asset_tag(
        self,
        bank: Bank,
        *,
        pdf_url: str | None,
        pdf_title: str | None,
        received_date: date | None,
    ) -> bool:
        """Tag one bank as digital-assets-related; merge its PDF link.

        Pure ORM mutation (no flush/commit — the caller owns the
        transaction). Sticky and additive by design: ``digital_assets``
        only ever flips to True, and PDF links are deduped by URL so
        re-running over the same page is idempotent. Returns True when the
        row actually changed.
        """
        changed = False
        if not bank.digital_assets:
            bank.digital_assets = True
            changed = True
        if (
            bank.application_received_date is None
            and received_date is not None
            and bank.occ_control_number is not None
        ):
            # The page's "Date received" corroborates the CAS Receipt date;
            # only fill the gap on OCC application rows, never overwrite.
            bank.application_received_date = received_date
            changed = True
        if pdf_url:
            existing = list(bank.digital_asset_pdfs or [])
            if not any(
                isinstance(entry, dict) and entry.get("url") == pdf_url
                for entry in existing
            ):
                existing.append(
                    {
                        "title": pdf_title,
                        "url": pdf_url,
                        "received_date": received_date.isoformat() if received_date else None,
                    }
                )
                # Reassign (never mutate in place): plain JSONB columns don't
                # track in-place list mutations, so an append alone would
                # silently not persist.
                bank.digital_asset_pdfs = existing
                changed = True
        return changed


def _status_advance_sql(incoming_column):
    """SQL CASE mirroring ``_advance_status`` for the ON CONFLICT path."""
    from sqlalchemy import case

    def rank(column):
        return case(
            CHARTER_STATUS_RANK,
            value=column,
            else_=-1,
        )

    return case(
        (rank(incoming_column) >= rank(Bank.charter_status), incoming_column),
        else_=Bank.charter_status,
    )


def _merge_source_sql(incoming: str):
    """'fdic' arriving on an 'occ' row (or vice versa) -> 'fdic+occ'."""
    from sqlalchemy import case

    other = "occ" if incoming == "fdic" else "fdic"
    return case(
        (Bank.source.in_((other, "fdic+occ")), "fdic+occ"),
        else_=incoming,
    )
