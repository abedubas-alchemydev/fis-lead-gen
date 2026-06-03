"""Aggregation queries for the admin Extraction Analytics page.

Reconstructs "what did each external provider extract" from the
attribution columns already on the schema — ``discovery_source`` on the
three contact tables, ``source`` / ``enrichment_status`` on
``discovered_email``, and ``website_source`` on the firm tables. No new
columns, no migration.

Counting convention (see the page's own help text):

- Summary + firm-list counts are **coarse**: ``GROUP BY discovery_source``,
  i.e. the *winning* provider per contact. Fast on the indexed FKs.
- The per-firm drill-down is **precise**: it unnests each contact's
  ``emails`` / ``phones`` JSONB and reads every element's own ``source``,
  so a contact whose email came from Hunter but phone from Apollo shows
  both. The two views differing for such a row is expected, not a bug.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, func, literal, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_contact import AdvisorContact
from app.models.broker_dealer import BrokerDealer
from app.models.discovered_email import DiscoveredEmail
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
from app.schemas.extraction_analytics import (
    ExtractedValue,
    FirmExtractionDetailResponse,
    FirmExtractionListResponse,
    FirmExtractionRow,
    FirmProviderCount,
    ProviderCount,
    ProviderSummaryResponse,
    ProviderSummaryTotals,
)

# Provider vocabularies — one source of truth, echoed to the FE in responses.
CONTACT_PROVIDERS: tuple[str, ...] = (
    "pdl",
    "apollo_match",
    "apollo_org",
    "hunter",
    "hunter_domain",
    "snov",
    "snov_domain",
    "linkedin_search",
)
# Apollo reverse-email enrichment is the enrichment *layer* on discovered
# emails (enrichment_status='enriched'), not a ``source`` value — synthesised.
REVERSE_EMAIL_PROVIDER = "apollo_reverse_email"
DISCOVERED_EMAIL_PROVIDERS: tuple[str, ...] = (
    "site_crawler",
    "hunter",
    "snov",
    "theharvester",
)
WEBSITE_SOURCES: tuple[str, ...] = ("finra", "iapd", "apollo", "serper", "serpapi")

ALL_PROVIDERS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *CONTACT_PROVIDERS,
            *DISCOVERED_EMAIL_PROVIDERS,
            REVERSE_EMAIL_PROVIDER,
            *WEBSITE_SOURCES,
        )
    )
)

PROVIDER_LABELS: dict[str, str] = {
    "pdl": "People Data Labs",
    "apollo_match": "Apollo (person match)",
    "apollo_org": "Apollo (org enrich)",
    "apollo_reverse_email": "Apollo (reverse-email)",
    "apollo": "Apollo",
    "hunter": "Hunter",
    "hunter_domain": "Hunter (domain)",
    "snov": "Snov.io",
    "snov_domain": "Snov.io (domain)",
    "site_crawler": "Site crawler",
    "theharvester": "theHarvester",
    "linkedin_search": "LinkedIn search",
    "finra": "FINRA",
    "iapd": "IAPD",
    "serper": "serper.dev",
    "serpapi": "SerpAPI",
}


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


# (firm_type, FirmModel, ContactModel, contact_fk_attr, discovered_email_fk_attr)
# ``discovered_email_fk`` is None for institutional investors — discovered_email
# has no investor FK, so email-extractor values never attach to them.
_KINDS: tuple[tuple[str, Any, Any, str, str | None], ...] = (
    ("broker_dealer", BrokerDealer, ExecutiveContact, "bd_id", "bd_id"),
    ("advisor", InvestmentAdvisor, AdvisorContact, "advisor_id", "advisor_id"),
    (
        "institutional_investor",
        InstitutionalInvestor,
        InvestorContact,
        "investor_id",
        None,
    ),
)


def _channel_values(
    raw: Any, fallback_provider: str | None
) -> list[tuple[str, str, float | None]]:
    """Flatten a contact ``emails`` / ``phones`` JSONB array into
    ``(value, provider, confidence)`` triples.

    Each element carries its own ``source`` (the precise per-value
    provider); we fall back to the row's ``discovery_source`` when absent.
    Tolerates bare-string elements and malformed entries.
    """
    out: list[tuple[str, str, float | None]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if isinstance(entry, str):
            value = entry.strip()
            if value:
                out.append((value, fallback_provider or "unknown", None))
            continue
        if not isinstance(entry, dict):
            continue
        value = entry.get("value") or entry.get("email") or entry.get("number")
        if not isinstance(value, str) or not value.strip():
            continue
        source = entry.get("source") or fallback_provider or "unknown"
        raw_conf = entry.get("confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        out.append((value.strip(), str(source), confidence))
    return out


class ExtractionAnalyticsRepository:
    """Read-only aggregates for the Extraction Analytics endpoints."""

    async def summary(self, db: AsyncSession) -> ProviderSummaryResponse:
        contacts, unattributed = await self._contact_provider_summary(db)
        discovered_emails, total_discovered, total_enriched = (
            await self._discovered_email_summary(db)
        )
        websites, total_with_website = await self._website_summary(db)

        totals = ProviderSummaryTotals(
            total_contacts_attributed=sum(p.value_count for p in contacts),
            total_contacts_unattributed=unattributed,
            total_discovered_emails=total_discovered,
            total_enriched_emails=total_enriched,
            total_firms_with_website=total_with_website,
        )
        return ProviderSummaryResponse(
            contacts=contacts,
            discovered_emails=discovered_emails,
            websites=websites,
            totals=totals,
        )

    async def _contact_provider_summary(
        self, db: AsyncSession
    ) -> tuple[list[ProviderCount], int]:
        acc: dict[str, dict[str, int]] = {}
        unattributed = 0
        for _kind, _firm, contact_model, fk_attr, _de in _KINDS:
            fk = getattr(contact_model, fk_attr)
            stmt = select(
                contact_model.discovery_source,
                func.count(contact_model.id),
                func.count(distinct(fk)),
            ).group_by(contact_model.discovery_source)
            for source, value_count, firm_count in (await db.execute(stmt)).all():
                if source is None:
                    unattributed += int(value_count or 0)
                    continue
                bucket = acc.setdefault(source, {"value": 0, "firm": 0})
                bucket["value"] += int(value_count or 0)
                bucket["firm"] += int(firm_count or 0)
        items = [
            ProviderCount(
                provider=source,
                label=provider_label(source),
                surface="contact",
                firm_count=counts["firm"],
                value_count=counts["value"],
            )
            for source, counts in acc.items()
        ]
        items.sort(key=lambda p: p.value_count, reverse=True)
        return items, unattributed

    async def _discovered_email_summary(
        self, db: AsyncSession
    ) -> tuple[list[ProviderCount], int, int]:
        by_source = select(
            DiscoveredEmail.source,
            func.count(DiscoveredEmail.id),
            func.count(distinct(DiscoveredEmail.bd_id)),
            func.count(distinct(DiscoveredEmail.advisor_id)),
        ).group_by(DiscoveredEmail.source)
        items: list[ProviderCount] = []
        total = 0
        for source, value_count, bd_firms, advisor_firms in (
            await db.execute(by_source)
        ).all():
            total += int(value_count or 0)
            items.append(
                ProviderCount(
                    provider=source or "unknown",
                    label=provider_label(source or "unknown"),
                    surface="discovered_email",
                    firm_count=int(bd_firms or 0) + int(advisor_firms or 0),
                    value_count=int(value_count or 0),
                )
            )

        # Apollo reverse-email enrichment layer (enrichment_status='enriched').
        enriched_stmt = select(
            func.count(DiscoveredEmail.id),
            func.count(distinct(DiscoveredEmail.bd_id)),
            func.count(distinct(DiscoveredEmail.advisor_id)),
        ).where(DiscoveredEmail.enrichment_status == "enriched")
        enriched_count, enr_bd, enr_advisor = (await db.execute(enriched_stmt)).one()
        total_enriched = int(enriched_count or 0)
        if total_enriched:
            items.append(
                ProviderCount(
                    provider=REVERSE_EMAIL_PROVIDER,
                    label=provider_label(REVERSE_EMAIL_PROVIDER),
                    surface="discovered_email",
                    firm_count=int(enr_bd or 0) + int(enr_advisor or 0),
                    value_count=total_enriched,
                )
            )
        items.sort(key=lambda p: p.value_count, reverse=True)
        return items, total, total_enriched

    async def _website_summary(
        self, db: AsyncSession
    ) -> tuple[list[ProviderCount], int]:
        acc: dict[str, int] = {}
        for _kind, firm_model, _c, _fk, _de in _KINDS:
            stmt = (
                select(firm_model.website_source, func.count(firm_model.id))
                .where(firm_model.website_source.isnot(None))
                .group_by(firm_model.website_source)
            )
            for source, count in (await db.execute(stmt)).all():
                acc[source] = acc.get(source, 0) + int(count or 0)
        items = [
            ProviderCount(
                provider=source,
                label=provider_label(source),
                surface="website",
                firm_count=count,  # one website per firm
                value_count=count,
            )
            for source, count in acc.items()
        ]
        items.sort(key=lambda p: p.value_count, reverse=True)
        return items, sum(acc.values())

    async def list_firms(
        self,
        db: AsyncSession,
        *,
        firm_type: str | None,
        provider: str | None,
        q: str | None,
        page: int,
        limit: int,
    ) -> FirmExtractionListResponse:
        needle = f"%{q.strip().lower()}%" if q and q.strip() else None
        rows: list[FirmExtractionRow] = []

        for kind, firm_model, contact_model, fk_attr, de_attr in _KINDS:
            if firm_type is not None and firm_type != kind:
                continue
            stmt = self._firm_list_stmt(
                kind, firm_model, contact_model, fk_attr, de_attr, provider, needle
            )
            if stmt is None:
                continue
            for r in (await db.execute(stmt)).all():
                rows.append(
                    FirmExtractionRow(
                        firm_type=kind,
                        firm_id=r.firm_id,
                        name=r.name or "",
                        crd_number=r.crd_number,
                        website_source=r.website_source,
                        contact_total=int(r.contact_total or 0),
                        discovered_email_total=int(r.de_total or 0),
                        provider_counts=[],
                        last_enriched_at=r.last_enriched_at,
                    )
                )

        rows.sort(key=lambda r: (r.name.lower(), r.firm_type))
        total = len(rows)
        start = (page - 1) * limit
        paged = rows[start : start + limit]
        if paged:
            await self._attach_provider_counts(db, paged)
        return FirmExtractionListResponse(items=paged, total=total)

    def _firm_list_stmt(
        self,
        kind: str,
        firm_model: Any,
        contact_model: Any,
        fk_attr: str,
        de_attr: str | None,
        provider: str | None,
        needle: str | None,
    ):
        """Per-kind driver SELECT, or None when the provider filter can't
        apply to this firm kind (e.g. a discovered-email provider on II)."""
        contact_fk = getattr(contact_model, fk_attr)
        crd_col = getattr(firm_model, "crd_number", null())

        contact_total = (
            select(func.count(contact_model.id))
            .where(contact_fk == firm_model.id)
            .correlate(firm_model)
            .scalar_subquery()
        )
        last_enriched = (
            select(func.max(contact_model.enriched_at))
            .where(contact_fk == firm_model.id)
            .correlate(firm_model)
            .scalar_subquery()
        )
        if de_attr is not None:
            de_fk = getattr(DiscoveredEmail, de_attr)
            de_total = (
                select(func.count(DiscoveredEmail.id))
                .where(de_fk == firm_model.id)
                .correlate(firm_model)
                .scalar_subquery()
            )
        else:
            de_fk = None
            de_total = literal(0)

        inclusion = self._inclusion_filter(
            firm_model,
            contact_model,
            contact_fk,
            de_fk,
            provider,
            contact_total,
            de_total,
        )
        if inclusion is None:
            return None

        stmt = (
            select(
                firm_model.id.label("firm_id"),
                firm_model.name.label("name"),
                crd_col.label("crd_number"),
                firm_model.website_source.label("website_source"),
                contact_total.label("contact_total"),
                de_total.label("de_total"),
                last_enriched.label("last_enriched_at"),
            )
            .where(inclusion)
            .order_by(firm_model.name.asc())
        )
        if needle:
            stmt = stmt.where(func.lower(firm_model.name).like(needle))
        return stmt

    def _inclusion_filter(
        self,
        firm_model: Any,
        contact_model: Any,
        contact_fk: Any,
        de_fk: Any,
        provider: str | None,
        contact_total: Any,
        de_total: Any,
    ):
        """Build the firm-inclusion predicate. ``None`` means this kind can't
        satisfy the provider filter and should be skipped entirely.

        A provider name (e.g. ``hunter``) can appear in more than one surface
        — as a contact ``discovery_source`` and a ``discovered_email.source``.
        The filter is "firms this provider touched in *any* surface", so we OR
        every applicable condition rather than picking one by precedence.
        """
        if provider is None:
            base = contact_total > 0
            if de_fk is not None:
                base = or_(base, de_total > 0)
            return base

        conditions = []
        if provider in CONTACT_PROVIDERS:
            conditions.append(
                select(contact_model.id)
                .where(contact_fk == firm_model.id)
                .where(contact_model.discovery_source == provider)
                .correlate(firm_model)
                .exists()
            )
        if de_fk is not None and provider in DISCOVERED_EMAIL_PROVIDERS:
            conditions.append(
                select(DiscoveredEmail.id)
                .where(de_fk == firm_model.id)
                .where(DiscoveredEmail.source == provider)
                .correlate(firm_model)
                .exists()
            )
        if de_fk is not None and provider == REVERSE_EMAIL_PROVIDER:
            conditions.append(
                select(DiscoveredEmail.id)
                .where(de_fk == firm_model.id)
                .where(DiscoveredEmail.enrichment_status == "enriched")
                .correlate(firm_model)
                .exists()
            )
        if provider in WEBSITE_SOURCES:
            conditions.append(firm_model.website_source == provider)

        if not conditions:
            # Provider can't apply to this firm kind (e.g. a discovered-email
            # provider on institutional investors). Skip the kind entirely.
            return None
        return or_(*conditions)

    async def _attach_provider_counts(
        self, db: AsyncSession, paged: list[FirmExtractionRow]
    ) -> None:
        # (firm_type, firm_id) -> {provider: count}, scoped to the page.
        counts: dict[tuple[str, int], dict[str, int]] = {}

        for kind, _firm, contact_model, fk_attr, de_attr in _KINDS:
            ids = [r.firm_id for r in paged if r.firm_type == kind]
            if not ids:
                continue
            contact_fk = getattr(contact_model, fk_attr)
            contact_stmt = (
                select(
                    contact_fk,
                    contact_model.discovery_source,
                    func.count(contact_model.id),
                )
                .where(contact_fk.in_(ids))
                .where(contact_model.discovery_source.isnot(None))
                .group_by(contact_fk, contact_model.discovery_source)
            )
            for firm_id, source, count in (await db.execute(contact_stmt)).all():
                counts.setdefault((kind, firm_id), {})[source] = counts.get(
                    (kind, firm_id), {}
                ).get(source, 0) + int(count or 0)

            if de_attr is None:
                continue
            de_fk = getattr(DiscoveredEmail, de_attr)
            de_stmt = (
                select(de_fk, DiscoveredEmail.source, func.count(DiscoveredEmail.id))
                .where(de_fk.in_(ids))
                .group_by(de_fk, DiscoveredEmail.source)
            )
            for firm_id, source, count in (await db.execute(de_stmt)).all():
                bucket = counts.setdefault((kind, firm_id), {})
                bucket[source] = bucket.get(source, 0) + int(count or 0)

            reveal_stmt = (
                select(de_fk, func.count(DiscoveredEmail.id))
                .where(de_fk.in_(ids))
                .where(DiscoveredEmail.enrichment_status == "enriched")
                .group_by(de_fk)
            )
            for firm_id, count in (await db.execute(reveal_stmt)).all():
                bucket = counts.setdefault((kind, firm_id), {})
                bucket[REVERSE_EMAIL_PROVIDER] = bucket.get(
                    REVERSE_EMAIL_PROVIDER, 0
                ) + int(count or 0)

        for row in paged:
            bucket = counts.get((row.firm_type, row.firm_id), {})
            row.provider_counts = sorted(
                (
                    FirmProviderCount(
                        provider=provider,
                        label=provider_label(provider),
                        count=count,
                    )
                    for provider, count in bucket.items()
                    if count > 0
                ),
                key=lambda c: c.count,
                reverse=True,
            )

    async def firm_values(
        self, db: AsyncSession, *, firm_type: str, firm_id: int
    ) -> FirmExtractionDetailResponse | None:
        config = next((k for k in _KINDS if k[0] == firm_type), None)
        if config is None:
            return None
        _kind, firm_model, contact_model, fk_attr, de_attr = config
        firm = await db.get(firm_model, firm_id)
        if firm is None:
            return None

        values: list[ExtractedValue] = []
        contact_fk = getattr(contact_model, fk_attr)
        contacts = (
            (
                await db.execute(
                    select(contact_model)
                    .where(contact_fk == firm_id)
                    .order_by(contact_model.name.asc())
                )
            )
            .scalars()
            .all()
        )
        for contact in contacts:
            values.extend(_contact_values(contact))

        if de_attr is not None:
            de_fk = getattr(DiscoveredEmail, de_attr)
            discovered = (
                (
                    await db.execute(
                        select(DiscoveredEmail)
                        .where(de_fk == firm_id)
                        .order_by(DiscoveredEmail.email.asc())
                    )
                )
                .scalars()
                .all()
            )
            for row in discovered:
                values.extend(_discovered_email_values(row))

        if firm.website and firm.website_source:
            values.append(
                ExtractedValue(
                    kind="website",
                    value=firm.website,
                    provider=firm.website_source,
                    label=provider_label(firm.website_source),
                    contact_id=None,
                    contact_name=None,
                    contact_title=None,
                    confidence=None,
                    source_table="firm",
                )
            )

        return FirmExtractionDetailResponse(
            firm_type=firm_type,
            firm_id=firm_id,
            name=firm.name or "",
            website=firm.website,
            website_source=firm.website_source,
            values=values,
        )


def _contact_values(contact: Any) -> list[ExtractedValue]:
    """Precise per-value provenance for one contact row: unnest the
    ``emails`` / ``phones`` JSONB (each element's own ``source``), with a
    fallback to the scalar ``email`` / ``phone`` + ``discovery_source``."""
    fallback = contact.discovery_source
    row_conf = (
        float(contact.discovery_confidence)
        if contact.discovery_confidence is not None
        else None
    )
    out: list[ExtractedValue] = []

    def _emit(kind: str, value: str, provider: str, confidence: float | None) -> None:
        out.append(
            ExtractedValue(
                kind=kind,  # type: ignore[arg-type]
                value=value,
                provider=provider,
                label=provider_label(provider),
                contact_id=contact.id,
                contact_name=contact.name,
                contact_title=contact.title,
                confidence=confidence,
                source_table="contact",
            )
        )

    emails = _channel_values(contact.emails, fallback)
    if emails:
        for value, provider, conf in emails:
            _emit("email", value, provider, conf)
    elif contact.email:
        _emit("email", contact.email, fallback or "unknown", row_conf)

    phones = _channel_values(contact.phones, fallback)
    if phones:
        for value, provider, conf in phones:
            _emit("phone", value, provider, conf)
    elif contact.phone:
        _emit("phone", contact.phone, fallback or "unknown", row_conf)

    if contact.linkedin_url:
        _emit("linkedin", contact.linkedin_url, fallback or "unknown", row_conf)

    return out


def _discovered_email_values(row: Any) -> list[ExtractedValue]:
    out: list[ExtractedValue] = []
    out.append(
        ExtractedValue(
            kind="email",
            value=row.email,
            provider=row.source or "unknown",
            label=provider_label(row.source or "unknown"),
            contact_id=None,
            contact_name=row.enriched_name,
            contact_title=row.enriched_title,
            confidence=row.confidence,
            source_table="discovered_email",
        )
    )
    if row.enrichment_status == "enriched":
        if row.enriched_email and row.enriched_email != row.email:
            out.append(_reveal_value("email", row.enriched_email, row))
        if row.enriched_phone:
            out.append(_reveal_value("phone", row.enriched_phone, row))
        if row.enriched_linkedin_url:
            out.append(_reveal_value("linkedin", row.enriched_linkedin_url, row))
    return out


def _reveal_value(kind: str, value: str, row: Any) -> ExtractedValue:
    return ExtractedValue(
        kind=kind,  # type: ignore[arg-type]
        value=value,
        provider=REVERSE_EMAIL_PROVIDER,
        label=provider_label(REVERSE_EMAIL_PROVIDER),
        contact_id=None,
        contact_name=row.enriched_name,
        contact_title=row.enriched_title,
        confidence=None,
        source_table="discovered_email",
    )
