"""Bank-charter endpoints — the banks sibling of ``broker_dealers.py``.

Read-only: rows are written exclusively by the nightly watcher
(``scripts/watch_bank_charters.py`` — see docs/runbooks/bank-charter-watch.md),
which ingests the two official public sources (FDIC BankFind, OCC Corporate
Applications Search) plus the OCC digital-assets applications page.

Gated on the ``banks`` feature permission (admins bypass, mirroring the
master list's ``master_list`` gate).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import BANKS
from app.db.session import get_db_session
from app.models.bank import Bank
from app.schemas.auth import AuthenticatedUser
from app.schemas.bank import (
    BankApplicationEventItem,
    BankDetail,
    BankListResponse,
    BankSourceLink,
)
from app.services.auth import ensure_feature, get_current_user
from app.services.banks import BankRepository
from app.services.fdic_bankfind import bankfind_public_url

router = APIRouter(prefix="/banks")
repository = BankRepository()


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
    established_after: date | None = Query(default=None),
    established_before: date | None = Query(default=None),
    # Tri-state: omitted = all banks; true = digital-assets-tagged only
    # (OCC Digital Assets Licensing Applications page matches); false =
    # untagged only.
    digital_assets: bool | None = Query(default=None),
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
        established_after=established_after,
        established_before=established_before,
        digital_assets=digital_assets,
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
    """Bank detail: the row plus its OCC application-event timeline and
    the official source links (FDIC BankFind page, OCC CAS filing page,
    digital-assets application PDFs)."""
    bank = await repository.get_bank(db, bank_id)
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")

    detail = BankDetail.model_validate(bank)
    detail.application_events = [
        BankApplicationEventItem.model_validate(event) for event in bank.application_events
    ]
    detail.source_links = _build_source_links(bank)
    return detail
