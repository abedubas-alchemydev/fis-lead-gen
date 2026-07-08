"""DOX Share — public profile assembly for a shared lead item.

The public ``/share/{token}`` surface renders the *same* rich per-firm
profile the authenticated detail page does, but through the allowlist
projections in ``schemas/lead_share.py`` so nothing internal (lead scoring,
favorite state, competitor flags, extraction/enrichment diagnostics,
unknown-reason blocks) can leak.

This module is the seam between the public endpoint and the Phase 1 profile
builders. It dispatches a :class:`LeadShareItem` on whichever entity FK is
set, re-fetches the ORM row through the very same repository the
authenticated endpoint uses, runs the shared builder with ``user_id=None``
(the per-user favorites lookup is skipped on that path), attaches the
email-extractor discoveries, and hands back a kind-enveloped
:class:`PublicProfileResponse`.

The Phase 1 builders live in the endpoint modules, which pull in a large
service graph; they're imported **lazily inside the dispatch** so importing
this module (and, transitively, ``endpoints/shares_public.py``) never forms
an import cycle at app-startup time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovered_email import DiscoveredEmail
from app.models.lead_share import LeadShareItem
from app.schemas.lead_share import (
    PublicAdvisorProfile,
    PublicBankProfile,
    PublicBrokerDealerProfile,
    PublicDiscoveredEmailRow,
    PublicProfileResponse,
)


# ── Discovered-email projection ───────────────────────────────────────────────


def _discovered_email_ts(row: DiscoveredEmail) -> datetime:
    """Recency key for a discovered-email row, coalescing enriched → created
    and normalising to an aware UTC value so None/naive rows can't break the
    sort comparison."""
    ts = row.enriched_at or row.created_at
    if ts is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _discovered_email_rank(row: DiscoveredEmail) -> tuple[int, int, datetime, int]:
    """Higher tuple wins the per-address dedupe: an enriched row beats a
    not-enriched one, then the one carrying more person fields, then the
    newest, then the highest id (a deterministic final tiebreak)."""
    enriched = 1 if row.enrichment_status == "enriched" else 0
    richness = sum(
        1
        for value in (
            row.enriched_name,
            row.enriched_title,
            row.enriched_phone,
            row.enriched_linkedin_url,
        )
        if value
    )
    return (enriched, richness, _discovered_email_ts(row), row.id)


async def list_discovered_emails_for_entity(
    db: AsyncSession,
    *,
    bd_id: int | None = None,
    advisor_id: int | None = None,
    bank_id: int | None = None,
) -> list[PublicDiscoveredEmailRow]:
    """Every email-extractor discovery attributed to one entity, deduped by
    ``lower(email)`` (keeping the newest / most-enriched row per address) and
    projected onto the public allowlist row. Ordered by address for a stable,
    predictable payload.

    Exactly one FK is expected from the profile dispatch; if none is supplied
    the result is empty rather than an unfiltered scan.
    """
    conditions = []
    if bd_id is not None:
        conditions.append(DiscoveredEmail.bd_id == bd_id)
    if advisor_id is not None:
        conditions.append(DiscoveredEmail.advisor_id == advisor_id)
    if bank_id is not None:
        conditions.append(DiscoveredEmail.bank_id == bank_id)
    if not conditions:
        return []

    rows = (
        await db.execute(select(DiscoveredEmail).where(*conditions))
    ).scalars().all()

    best: dict[str, DiscoveredEmail] = {}
    for row in rows:
        key = (row.email or "").strip().lower()
        if not key:
            continue
        current = best.get(key)
        if current is None or _discovered_email_rank(row) > _discovered_email_rank(
            current
        ):
            best[key] = row

    ordered = sorted(best.values(), key=lambda r: (r.email or "").lower())
    return [PublicDiscoveredEmailRow.from_model(row) for row in ordered]


# ── Profile dispatch ──────────────────────────────────────────────────────────


async def build_public_profile(
    db: AsyncSession, item: LeadShareItem
) -> PublicProfileResponse | None:
    """Assemble the public, allowlisted profile for one shared item.

    Dispatches on the single entity FK the item carries (the DB
    ``ck_lead_share_item_exactly_one_target`` XOR guarantees exactly one).
    Returns None when the referenced entity no longer exists — a firm deleted
    from DOX after the snapshot — so the endpoint answers 404 rather than 500.
    """
    if item.broker_dealer_id is not None:
        from app.api.v1.endpoints.broker_dealers import (
            build_broker_dealer_profile,
            repository as bd_repository,
        )

        broker_dealer = await bd_repository.get_broker_dealer(
            db, item.broker_dealer_id
        )
        if broker_dealer is None:
            return None
        profile = await build_broker_dealer_profile(db, broker_dealer, user_id=None)
        discovered_emails = await list_discovered_emails_for_entity(
            db, bd_id=item.broker_dealer_id
        )
        return PublicProfileResponse(
            item_id=item.id,
            kind="broker_dealer",
            broker_dealer=PublicBrokerDealerProfile.from_internal(
                profile, discovered_emails=discovered_emails
            ),
        )

    if item.advisor_id is not None:
        from app.api.v1.endpoints.investment_advisors import (
            build_investment_advisor_profile,
            repository as advisor_repository,
        )

        advisor = await advisor_repository.get_investment_advisor(
            db, item.advisor_id
        )
        if advisor is None:
            return None
        profile = await build_investment_advisor_profile(db, advisor, user_id=None)
        discovered_emails = await list_discovered_emails_for_entity(
            db, advisor_id=item.advisor_id
        )
        return PublicProfileResponse(
            item_id=item.id,
            kind="advisor",
            advisor=PublicAdvisorProfile.from_internal(
                profile, discovered_emails=discovered_emails
            ),
        )

    if item.bank_id is not None:
        from app.api.v1.endpoints.banks import (
            build_bank_detail,
            repository as bank_repository,
        )

        # get_bank selectinloads application_events + contacts — required, or
        # build_bank_detail trips an async lazy-load on those relationships.
        bank = await bank_repository.get_bank(db, item.bank_id)
        if bank is None:
            return None
        detail = build_bank_detail(bank)
        discovered_emails = await list_discovered_emails_for_entity(
            db, bank_id=item.bank_id
        )
        return PublicProfileResponse(
            item_id=item.id,
            kind="bank",
            bank=PublicBankProfile.from_internal(
                detail, discovered_emails=discovered_emails
            ),
        )

    return None
