"""Bank-charter endpoints — the banks sibling of ``broker_dealers.py``.

Read-only: rows are written exclusively by the nightly watcher
(``scripts/watch_bank_charters.py`` — see docs/runbooks/bank-charter-watch.md),
which ingests the two official public sources (FDIC BankFind, OCC Corporate
Applications Search) plus the OCC digital-assets applications page.

Gated on the ``banks`` feature permission (admins bypass, mirroring the
master list's ``master_list`` gate).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import BANKS
from app.db.session import get_db_session
from app.models.bank import Bank
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.pipeline_run import PipelineRun
from app.schemas.auth import AuthenticatedUser
from app.schemas.bank import (
    BankApplicationEventItem,
    BankContactItem,
    BankDetail,
    BankListResponse,
    BankSourceLink,
    GapFillBankResponse,
)
from app.schemas.favorite_list import FavoriteListWithMembership
from app.services.auth import ensure_feature, get_current_user
from app.services.bank_contact_gap_fill import (
    GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
    GAP_FILL_COOLDOWN_DAYS,
    run_gap_fill_bank_contacts_background,
)
from app.services.banks import BankRepository
from app.services.fdic_bankfind import bankfind_public_url
from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/banks")
repository = BankRepository()


def _is_recent_run(started_at: datetime | None) -> bool:
    """True if a run started recently enough to still be considered alive.

    Anything older than ``STALE_REFRESH_RUN_AGE`` is presumed orphaned (its
    in-process BackgroundTask was killed by an instance swap) and must not be
    treated as in-flight. Mirror of the advisor endpoint's helper.
    """
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at >= datetime.now(timezone.utc) - STALE_REFRESH_RUN_AGE


def _require_banks(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, BANKS)
    return user


def _parse_multi(values: list[str] | None) -> list[str]:
    """Split repeat-key and comma-joined multi-selects (same convention as
    the master list's ``_parse_states`` — these are short enum values, so
    comma-splitting is safe)."""
    if not values:
        return []
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


@router.get("", response_model=BankListResponse)
async def list_banks(
    search: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    charter_authority: list[str] | None = Query(default=None),
    charter_status: list[str] | None = Query(default=None),
    # OCC CharterType (e.g. 'National', 'TrustCo-National'); repeatable and
    # comma-joinable like the other multi-selects.
    charter_type: list[str] | None = Query(default=None),
    established_after: date | None = Query(default=None),
    established_before: date | None = Query(default=None),
    # Tri-state: omitted = all banks; true = digital-assets-tagged only
    # (OCC Digital Assets Licensing Applications page matches); false =
    # untagged only.
    digital_assets: bool | None = Query(default=None),
    # New-charters-only: opt-in narrowing to newly opened / pending charters
    # (the original 59-row new/pending/digital-asset set) even after the
    # opt-in full sync has seeded the entire bank universe. Default off.
    new_charters_only: bool = Query(default=False),
    sort_by: str = Query(default="established_date"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_banks),
    db: AsyncSession = Depends(get_db_session),
) -> BankListResponse:
    if (
        established_after is not None
        and established_before is not None
        and established_after > established_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="established_after must be on or before established_before.",
        )

    return await repository.list_banks(
        db,
        search=search,
        states=_parse_multi(state),
        charter_authorities=_parse_multi(charter_authority),
        charter_statuses=_parse_multi(charter_status),
        charter_types=_parse_multi(charter_type),
        established_after=established_after,
        established_before=established_before,
        digital_assets=digital_assets,
        new_charters_only=new_charters_only,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        limit=limit,
    )


@router.get("/states", response_model=list[str])
async def list_bank_states(
    _: AuthenticatedUser = Depends(_require_banks),
    db: AsyncSession = Depends(get_db_session),
) -> list[str]:
    """Distinct states with at least one bank — fuels the state filter."""
    return await repository.list_states(db)


def _build_source_links(bank: Bank) -> list[BankSourceLink]:
    """Official government source pages for the detail view.

    Everything here is a public occ.gov / fdic.gov URL; the FE renders
    them as outbound links so a user can verify any row against its
    source of record in one click.
    """
    links: list[BankSourceLink] = []
    if bank.fdic_cert:
        links.append(
            BankSourceLink(
                label="FDIC BankFind institution profile",
                url=bankfind_public_url(bank.fdic_cert),
            )
        )
    # The CAS details URL is stamped on every event of a filing; take the
    # newest event that carries one (events arrive newest-first via the
    # relationship ordering).
    for event in bank.application_events:
        if event.source_url:
            links.append(
                BankSourceLink(
                    label="OCC Corporate Applications Search filing",
                    url=event.source_url,
                )
            )
            break
    for entry in bank.digital_asset_pdfs or []:
        if isinstance(entry, dict) and entry.get("url"):
            links.append(
                BankSourceLink(
                    label=(
                        f"OCC digital-assets application (public portion) — "
                        f"{entry.get('title') or bank.name}"
                    ),
                    url=str(entry["url"]),
                )
            )
    return links


@router.get("/{bank_id}", response_model=BankDetail)
async def get_bank(
    bank_id: int,
    _: AuthenticatedUser = Depends(_require_banks),
    db: AsyncSession = Depends(get_db_session),
) -> BankDetail:
    """Bank detail: the row plus its OCC application-event timeline, the
    official source links (FDIC BankFind page, OCC CAS filing page,
    digital-assets application PDFs), and the people extracted from those
    PDFs (``contacts`` — same style as the BD detail's executive contacts)."""
    bank = await repository.get_bank(db, bank_id)
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")

    detail = BankDetail.model_validate(bank)
    detail.application_events = [
        BankApplicationEventItem.model_validate(event) for event in bank.application_events
    ]
    detail.source_links = _build_source_links(bank)
    # BankContactItem synthesizes the emails/phones arrays from the scalar
    # columns on read, so contacts[] carry the channel shape the FE renders.
    detail.contacts = [BankContactItem.model_validate(contact) for contact in bank.contacts]
    return detail


# ─── Per-bank contact discovery + gap-fill ("Generate More Details") ──────────
# Banks have no FINRA/ADV roster, so this both (1) org→people discovers a
# public contact channel from the bank's website domain and (2) gap-fills any
# existing bank_contacts rows (PDF-extracted people) missing LinkedIn/email/
# phone. Async: returns a run_id the FE polls at GET /pipeline/run/{run_id}.
# Mirror of investment_advisors.py::gap_fill_advisor_contacts.


async def _run_gap_fill_bank_contacts_background(
    run_id: int, bank_id: int, trigger_source: str
) -> None:
    """Defensive wrapper so an unhandled exception in the background task
    doesn't leave the PipelineRun stuck on ``queued``."""
    try:
        await run_gap_fill_bank_contacts_background(run_id, bank_id, trigger_source)
    except Exception:
        logger.exception(
            "bank gap-fill-contacts background task failed (run_id=%s bank_id=%s)",
            run_id,
            bank_id,
        )


@router.post("/{bank_id}/gap-fill-contacts", response_model=GapFillBankResponse)
async def gap_fill_bank_contacts(
    bank_id: int,
    background_tasks: BackgroundTasks,
    response: Response,
    force: bool = False,
    current_user: AuthenticatedUser = Depends(_require_banks),
    db: AsyncSession = Depends(get_db_session),
) -> GapFillBankResponse:
    """Discover + gap-fill this bank's People panel.

    Runs org→people discovery from the bank's website domain and gap-fills any
    existing ``bank_contacts`` rows missing LinkedIn/email/phone, merging new
    hits in place WITHOUT overwriting existing data. Works even for banks with
    zero contacts (the org→domain discovery seeds a public channel).

    Async: returns 202 with a ``run_id`` the FE polls at
    ``GET /api/v1/pipeline/run/{run_id}``.

    Guards (mirroring ``gap_fill_advisor_contacts``): attaches to an in-flight
    run for this bank rather than queueing a second job, and applies a 30-day
    cost cooldown that ``force`` (the user-facing "Generate More Details"
    button) bypasses. Banks carry no ``last_gap_fill_attempt_at`` column, so
    the cooldown reads the most recent gap-fill ``PipelineRun`` for this bank
    instead.
    """
    bank = await db.get(Bank, bank_id)
    if bank is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found."
        )

    bank_marker = f'"bank_id": {bank_id}'
    # Match the id as a COMPLETE JSON token — delimited by ',' or '}' — so one
    # bank id can never prefix-match another (bank 12 must NOT match bank 123's
    # run). ``notes`` is always ``json.dumps({"bank_id": id, ...})``, so the id
    # is followed by ',' (another key after it) or '}' (last key).
    bank_run_marker_match = or_(
        PipelineRun.notes.ilike(f"%{bank_marker},%"),
        PipelineRun.notes.ilike(f"%{bank_marker}" + "}%"),
    )

    # Guard ORDER note: unlike the advisor mirror (which checks its cooldown
    # BEFORE the in-flight guard), banks MUST check in-flight FIRST. The advisor
    # cooldown reads ``advisor.last_gap_fill_attempt_at`` — a column stamped only
    # inside the background runner, so a just-queued in-flight run hasn't stamped
    # it yet and never trips its own cooldown. Banks have no such column, so the
    # cooldown below reads the most recent gap-fill *PipelineRun* start time,
    # UNFILTERED by status — which includes the in-flight run itself. Checking
    # cooldown first would therefore 429 a non-``force`` caller on a run that just
    # started, instead of letting them attach to it. So this order is intentional.
    #
    # Attach to an in-flight gap-fill run for this bank rather than queueing a
    # second concurrent job. A run only counts as in-flight if it started
    # within STALE_REFRESH_RUN_AGE; an older running/queued row is an orphan
    # (its BackgroundTask was killed by an instance swap) and is ignored.
    in_flight_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == GAP_FILL_BANK_CONTACTS_PIPELINE_NAME)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(bank_run_marker_match)
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    in_flight = (await db.execute(in_flight_stmt)).scalar_one_or_none()
    if in_flight is not None and _is_recent_run(in_flight.started_at):
        response.status_code = status.HTTP_202_ACCEPTED
        return GapFillBankResponse(
            run_id=in_flight.id,
            status="in_flight",
            bank_id=bank_id,
            reason="A gap-fill run is already in flight for this bank.",
        )

    # 30-day cost cooldown, bypassed by ``force``. Keyed on the most recent
    # gap-fill run's start time for this bank (banks have no per-row attempt
    # stamp). Automated / bare API callers omit ``force`` and keep the cooldown.
    if not force:
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(days=GAP_FILL_COOLDOWN_DAYS)
        recent_stmt = (
            select(PipelineRun)
            .where(PipelineRun.pipeline_name == GAP_FILL_BANK_CONTACTS_PIPELINE_NAME)
            .where(bank_run_marker_match)
            .where(PipelineRun.started_at.isnot(None))
            .order_by(PipelineRun.id.desc())
            .limit(1)
        )
        recent = (await db.execute(recent_stmt)).scalar_one_or_none()
        if recent is not None and recent.started_at is not None:
            started_at = recent.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if started_at >= cooldown_cutoff:
                days_remaining = max(
                    1,
                    GAP_FILL_COOLDOWN_DAYS
                    - (datetime.now(timezone.utc) - started_at).days,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Gap-fill cooldown active. Try again in {days_remaining} day(s).",
                    headers={"Retry-After": str(days_remaining * 86400)},
                )

    trigger_source = f"manual_gap_fill:{current_user.email}"
    run = PipelineRun(
        pipeline_name=GAP_FILL_BANK_CONTACTS_PIPELINE_NAME,
        trigger_source=trigger_source,
        status="queued",
        total_items=1,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps({"bank_id": bank_id, "stage": "queued"}),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        _run_gap_fill_bank_contacts_background,
        run.id,
        bank_id,
        trigger_source,
    )

    response.status_code = status.HTTP_202_ACCEPTED
    return GapFillBankResponse(
        run_id=run.id,
        status=run.status,
        bank_id=bank_id,
        reason=None,
    )


@router.get(
    "/{bank_id}/favorite-lists",
    response_model=list[FavoriteListWithMembership],
)
async def get_bank_favorite_lists(
    bank_id: int,
    current_user: AuthenticatedUser = Depends(_require_banks),
    db: AsyncSession = Depends(get_db_session),
) -> list[FavoriteListWithMembership]:
    """Return the calling user's lists with an ``is_member`` flag for ``bank_id``.

    Mirror of ``GET /investment-advisors/{advisor_id}/favorite-lists`` — same
    query shape, just keyed on ``bank_id`` instead of ``advisor_id``. Powers
    the FE list-picker on bank-list rows + the bank-detail page.
    """

    bank_check = await db.execute(select(Bank.id).where(Bank.id == bank_id))
    if bank_check.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found."
        )

    item_count_sq = (
        select(
            FavoriteListItem.list_id.label("list_id"),
            func.count(FavoriteListItem.id).label("count"),
        )
        .group_by(FavoriteListItem.list_id)
        .subquery()
    )
    is_member_expr = (
        exists()
        .where(
            FavoriteListItem.list_id == FavoriteList.id,
            FavoriteListItem.bank_id == bank_id,
        )
        .label("is_member")
    )
    stmt = (
        select(
            FavoriteList,
            func.coalesce(item_count_sq.c.count, 0).label("item_count"),
            is_member_expr,
        )
        .outerjoin(item_count_sq, FavoriteList.id == item_count_sq.c.list_id)
        .where(FavoriteList.user_id == current_user.id)
        .order_by(FavoriteList.is_default.desc(), FavoriteList.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FavoriteListWithMembership(
            id=fl.id,
            name=fl.name,
            is_default=fl.is_default,
            item_count=int(count),
            created_at=fl.created_at,
            is_member=bool(is_member),
        )
        for fl, count, is_member in rows
    ]
