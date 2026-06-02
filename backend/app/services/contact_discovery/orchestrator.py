"""Discovery chain orchestrator.

Three public entry points share the same multi-provider chain walk:

1. ``discover_contact(entity, bd_id, session)`` — resolves a broker-dealer
   officer into a persisted ``ExecutiveContact`` row. Called by the
   ``POST /api/v1/broker-dealers/{id}/enrich`` endpoint.
2. ``discover_investor_contact(entity, investor_id, session)`` — the
   institutional-investor sibling. Called by ``POST
   /api/v1/institutional-investors/{id}/enrich``.
3. ``discover_advisor_contact(entity, advisor_id, session)`` — the
   investment-advisor sibling. Called by the IA refresh orchestrator's
   ``_run_enrich_contacts`` sub-pipeline.

All:

1. Check a 90-day cache (per-entity-type table, name + FK lookup). Cache
   hit returns the row without touching any provider.
2. Otherwise fan out to every provider listed in
   ``settings.contact_discovery_chain`` concurrently (``asyncio.gather``),
   calling ``find_person`` or ``find_org`` per entity type.
3. Keep every result with ``confidence >=
   settings.contact_discovery_min_confidence`` and merge them into one
   canonical ``DiscoveryResult`` (see ``_merge_discovery_results``).
4. Persist that merged result as a typed row with the highest-confidence
   contributor's native identifier on ``discovery_source`` (e.g. ``pdl``,
   ``apollo_match``, ``apollo_org``, ``hunter``, ``hunter_domain``,
   ``snov``, ``snov_domain``), its 0..100 ``confidence`` on
   ``discovery_confidence``, and the merged multi-value ``emails`` /
   ``phones`` lists serialised into the JSONB columns (multi-value
   providers contribute typed hits directly; single-value providers
   contribute their scalar email/phone lifted to a typed hit).

The commit is left to the caller so the endpoint can batch multiple
officers into a single transaction.

Provider failures are swallowed deliberately. A provider that raises is
logged and treated like a miss — one flaky upstream can't block the
fan-out.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.advisor_contact import AdvisorContact
from app.models.executive_contact import ExecutiveContact
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)
from app.services.contact_discovery.hunter import HunterProvider
from app.services.contact_discovery.linkedin_search import LinkedInSearchProvider
from app.services.contact_discovery.pdl import PdlProvider
from app.services.contact_discovery.snov import SnovProvider

logger = logging.getLogger(__name__)


# Registry of known providers keyed by the identifier that appears in
# ``settings.contact_discovery_chain``. Instances are stateless so a single
# module-level copy is safe across requests.
_PROVIDERS: dict[str, ContactDiscoveryProvider] = {
    "pdl": PdlProvider(),
    "apollo_match": ApolloMatchProvider(),
    "hunter": HunterProvider(),
    "snov": SnovProvider(),
    "linkedin_search": LinkedInSearchProvider(),
}

_CACHE_TTL_DAYS = 90


_EntityType = Literal["person", "organization"]


# Parsed entity tuple shape:
#   (entity_type, first_name, last_name, org_name, domain, title, cache_name)
_ParsedEntity = tuple[_EntityType, str, str, str, str | None, str | None, str]


async def discover_contact(
    entity: Mapping[str, Any],
    *,
    bd_id: int,
    session: AsyncSession,
    use_cache: bool = True,
) -> ExecutiveContact | None:
    """Resolve a broker-dealer officer into a persisted ``ExecutiveContact``.

    See module docstring for the chain semantics. The row is added to the
    session but **not** committed — the caller owns the transaction
    boundary.

    ``use_cache=False`` (the user-facing force/refresh button) skips the
    90-day cache read and re-walks the chain. To avoid stacking a duplicate
    on repeat forced runs, an existing discovery-owned row for this
    ``(bd_id, name)`` — regardless of age — is updated in place instead of
    inserted. Phones are never regressed: a forced run's sync result often
    has no phone (Apollo reveal is async), so an already-revealed phone is
    kept.
    """
    parsed = _parse_entity(entity)
    if parsed is None:
        return None
    entity_type, first_name, last_name, org_name, domain, title, cache_name = parsed

    if use_cache:
        cached = await _find_cached_executive(session, bd_id=bd_id, name=cache_name)
        if cached is not None:
            return cached

    result = await _walk_chain(
        entity_type,
        first_name=first_name,
        last_name=last_name,
        org_name=org_name,
        domain=domain,
        cache_name=cache_name,
    )
    if result is None:
        return None

    title_value = title or ("Executive" if entity_type == "person" else "Organization")

    if not use_cache:
        existing = await _find_executive_any_age(session, bd_id=bd_id, name=cache_name)
        if existing is not None:
            _apply_discovery_to_executive(existing, title=title_value, result=result)
            return existing

    row = _build_executive_row(
        bd_id=bd_id,
        name=cache_name,
        title=title_value,
        result=result,
    )
    session.add(row)
    return row


async def discover_investor_contact(
    entity: Mapping[str, Any],
    *,
    investor_id: int,
    session: AsyncSession,
) -> InvestorContact | None:
    """Resolve an institutional-investor officer into a persisted ``InvestorContact``.

    Mirror of ``discover_contact`` for the institutional-investor side.
    Same chain semantics, same caching pattern; written separately because
    the codebase deliberately avoids polymorphism across the three contact
    tables (see ``InvestorContact``'s class docstring).
    """
    parsed = _parse_entity(entity)
    if parsed is None:
        return None
    entity_type, first_name, last_name, org_name, domain, title, cache_name = parsed

    cached = await _find_cached_investor(session, investor_id=investor_id, name=cache_name)
    if cached is not None:
        return cached

    result = await _walk_chain(
        entity_type,
        first_name=first_name,
        last_name=last_name,
        org_name=org_name,
        domain=domain,
        cache_name=cache_name,
    )
    if result is None:
        return None

    row = _build_investor_row(
        investor_id=investor_id,
        name=cache_name,
        title=title or ("Executive" if entity_type == "person" else "Organization"),
        result=result,
    )
    session.add(row)
    return row


async def discover_advisor_contact(
    entity: Mapping[str, Any],
    *,
    advisor_id: int,
    session: AsyncSession,
) -> AdvisorContact | None:
    """Resolve an investment-advisor officer into a persisted ``AdvisorContact``.

    Mirror of ``discover_contact`` for the investment-advisor side. Same
    chain semantics, same caching pattern; written separately because the
    codebase deliberately avoids polymorphism across the three contact
    tables (see ``AdvisorContact``'s class docstring).
    """
    parsed = _parse_entity(entity)
    if parsed is None:
        return None
    entity_type, first_name, last_name, org_name, domain, title, cache_name = parsed

    cached = await _find_cached_advisor(session, advisor_id=advisor_id, name=cache_name)
    if cached is not None:
        return cached

    result = await _walk_chain(
        entity_type,
        first_name=first_name,
        last_name=last_name,
        org_name=org_name,
        domain=domain,
        cache_name=cache_name,
    )
    if result is None:
        return None

    row = _build_advisor_row(
        advisor_id=advisor_id,
        name=cache_name,
        title=title or ("Executive" if entity_type == "person" else "Organization"),
        result=result,
    )
    session.add(row)
    return row


def _parse_entity(entity: Mapping[str, Any]) -> _ParsedEntity | None:
    """Validate and unpack the endpoint-layer ``entity`` dict into a typed tuple."""
    entity_type_raw = str(entity.get("type") or "").strip().lower()
    if entity_type_raw not in {"person", "organization"}:
        logger.warning("discover_contact called with unknown entity type %r", entity_type_raw)
        return None
    org_name = str(entity.get("org_name") or "").strip()
    if not org_name:
        return None
    domain_raw = entity.get("domain")
    domain = str(domain_raw).strip() if domain_raw else None
    title_raw = entity.get("title")
    title = str(title_raw).strip() if title_raw else None
    first_name = str(entity.get("first_name") or "").strip()
    last_name = str(entity.get("last_name") or "").strip()
    entity_type: _EntityType = "person" if entity_type_raw == "person" else "organization"
    if entity_type == "person":
        if not first_name or not last_name:
            return None
        cache_name = f"{first_name} {last_name}"
    else:
        cache_name = org_name
    return entity_type, first_name, last_name, org_name, domain, title, cache_name


async def _walk_chain(
    entity_type: _EntityType,
    *,
    first_name: str,
    last_name: str,
    org_name: str,
    domain: str | None,
    cache_name: str,
) -> DiscoveryResult | None:
    """Fan out to every configured provider in parallel and merge their hits.

    Each provider in ``settings.contact_discovery_chain`` is invoked
    concurrently via ``asyncio.gather`` so wall-clock latency is
    ``max(provider_times)`` instead of the old serial ``sum``. Every
    successful result that clears
    ``settings.contact_discovery_min_confidence`` is handed to
    ``_merge_discovery_results`` for union/dedupe; results that raise,
    return ``None``, or fall below the threshold contribute nothing and are
    skipped (mirroring the previous first-hit-wins miss handling). Returns
    ``None`` when no provider clears the threshold.
    """
    min_confidence = float(settings.contact_discovery_min_confidence)
    chain = [p.strip() for p in settings.contact_discovery_chain.split(",") if p.strip()]

    valid: list[tuple[str, ContactDiscoveryProvider]] = []
    for provider_name in chain:
        provider = _PROVIDERS.get(provider_name)
        if provider is None:
            logger.warning("contact_discovery_chain references unknown provider %r", provider_name)
            continue
        valid.append((provider_name, provider))

    if not valid:
        return None

    awaitables: list[Awaitable[DiscoveryResult | None]] = []
    for _, provider in valid:
        if entity_type == "person":
            awaitables.append(provider.find_person(first_name, last_name, org_name, domain))
        else:
            awaitables.append(provider.find_org(org_name, domain))

    raw_results = await asyncio.gather(*awaitables, return_exceptions=True)

    kept: list[DiscoveryResult] = []
    for (provider_name, _), outcome in zip(valid, raw_results):
        if isinstance(outcome, BaseException):
            # Mirror the previous broad swallow: one flaky upstream can't
            # block the merge. Log full traceback for the audit trail.
            logger.exception(
                "Provider %s raised during discovery", provider_name, exc_info=outcome
            )
            continue
        if outcome is None:
            continue
        if outcome.confidence < min_confidence:
            logger.info(
                "Provider %s returned %.1f for %s, below threshold %.1f",
                provider_name,
                outcome.confidence,
                cache_name,
                min_confidence,
            )
            continue
        kept.append(outcome)

    return _merge_discovery_results(kept)


def _merge_discovery_results(
    results: list[DiscoveryResult],
) -> DiscoveryResult | None:
    """Merge multiple confident provider hits into one canonical result.

    Semantics (see PR brief for the canonical spec):

    - Empty input -> ``None``.
    - ``emails``: union of every result's ``emails`` list plus each
      result's scalar ``email`` lifted to an ``EmailHit(type="work")``.
      Deduped on ``value.lower().strip()``; first occurrence in chain
      order wins, so a typed PDL hit outranks a lifted scalar for the
      same address.
    - ``phones``: same union/dedupe shape. Deduped on ``value.strip()``
      only (no lowercase — phone strings can carry meaningful punctuation
      like ``+`` / ``(`` / ``-``).
    - ``linkedin_url``: first non-null in chain order.
    - Scalar ``email`` / ``phone``: the value from the merged list with
      the highest non-null confidence (ties -> first in list). ``None``
      when the merged list is empty.
    - ``confidence``: max across results.
    - ``provider``: the highest-confidence contributor's provider name
      (``max(...)`` returns the first maximum on ties, preserving chain
      order).
    - ``raw``: a dict keyed by provider so audit consumers can still see
      each provider's payload.
    """
    if not results:
        return None

    merged_emails: list[EmailHit] = []
    seen_email_keys: set[str] = set()
    for r in results:
        for hit in r.emails:
            key = hit.value.lower().strip()
            if not key or key in seen_email_keys:
                continue
            seen_email_keys.add(key)
            merged_emails.append(hit)
        if r.email:
            key = r.email.lower().strip()
            if key and key not in seen_email_keys:
                seen_email_keys.add(key)
                merged_emails.append(
                    EmailHit(
                        value=r.email,
                        type="work",
                        confidence=r.confidence,
                        source=r.provider,
                    )
                )

    merged_phones: list[PhoneHit] = []
    seen_phone_keys: set[str] = set()
    for r in results:
        for hit in r.phones:
            key = hit.value.strip()
            if not key or key in seen_phone_keys:
                continue
            seen_phone_keys.add(key)
            merged_phones.append(hit)
        if r.phone:
            key = r.phone.strip()
            if key and key not in seen_phone_keys:
                seen_phone_keys.add(key)
                merged_phones.append(
                    PhoneHit(
                        value=r.phone,
                        type="work",
                        confidence=r.confidence,
                        source=r.provider,
                    )
                )

    linkedin_url: str | None = None
    for r in results:
        if r.linkedin_url:
            linkedin_url = r.linkedin_url
            break

    # apollo_person_id: first non-null in chain order. Only apollo_match
    # ever sets it; if a future Apollo-shaped provider also populates it,
    # chain order picks the winner the same way linkedin_url does.
    apollo_person_id: str | None = None
    for r in results:
        if r.apollo_person_id:
            apollo_person_id = r.apollo_person_id
            break

    email_scalar = _highest_confidence_value(merged_emails)
    phone_scalar = _highest_confidence_value(merged_phones)

    top = max(results, key=lambda r: r.confidence)
    return DiscoveryResult(
        email=email_scalar,
        phone=phone_scalar,
        linkedin_url=linkedin_url,
        confidence=top.confidence,
        provider=top.provider,
        raw={r.provider: r.raw for r in results},
        emails=merged_emails,
        phones=merged_phones,
        apollo_person_id=apollo_person_id,
    )


def _highest_confidence_value(
    hits: list[EmailHit] | list[PhoneHit],
) -> str | None:
    """Return the ``value`` of the hit with the highest confidence.

    ``None`` confidence is treated as 0 for ordering. On ties, the first
    hit in input order wins (``max`` is stable for the first maximum).
    """
    if not hits:
        return None
    best = max(hits, key=lambda h: h.confidence if h.confidence is not None else 0.0)
    return best.value


async def _find_cached_executive(
    session: AsyncSession,
    *,
    bd_id: int,
    name: str,
) -> ExecutiveContact | None:
    threshold = datetime.now(timezone.utc) - timedelta(days=_CACHE_TTL_DAYS)
    stmt = (
        select(ExecutiveContact)
        .where(
            ExecutiveContact.bd_id == bd_id,
            ExecutiveContact.name == name,
            ExecutiveContact.enriched_at >= threshold,
        )
        .order_by(ExecutiveContact.enriched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _find_executive_any_age(
    session: AsyncSession,
    *,
    bd_id: int,
    name: str,
) -> ExecutiveContact | None:
    """Latest discovery-owned ``ExecutiveContact`` for ``(bd_id, name)``, any age.

    Used by the forced (``use_cache=False``) path to update an existing row
    in place rather than insert a duplicate. ``focus_report`` rows are
    excluded — they're higher-trust and coexist with discovery rows (see
    ``contacts.enrich_contacts``), so we never clobber them.
    """
    stmt = (
        select(ExecutiveContact)
        .where(
            ExecutiveContact.bd_id == bd_id,
            ExecutiveContact.name == name,
            ExecutiveContact.source != "focus_report",
        )
        .order_by(ExecutiveContact.enriched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _find_cached_investor(
    session: AsyncSession,
    *,
    investor_id: int,
    name: str,
) -> InvestorContact | None:
    threshold = datetime.now(timezone.utc) - timedelta(days=_CACHE_TTL_DAYS)
    stmt = (
        select(InvestorContact)
        .where(
            InvestorContact.investor_id == investor_id,
            InvestorContact.name == name,
            InvestorContact.enriched_at >= threshold,
        )
        .order_by(InvestorContact.enriched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _find_cached_advisor(
    session: AsyncSession,
    *,
    advisor_id: int,
    name: str,
) -> AdvisorContact | None:
    threshold = datetime.now(timezone.utc) - timedelta(days=_CACHE_TTL_DAYS)
    stmt = (
        select(AdvisorContact)
        .where(
            AdvisorContact.advisor_id == advisor_id,
            AdvisorContact.name == name,
            AdvisorContact.enriched_at >= threshold,
        )
        .order_by(AdvisorContact.enriched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def _build_executive_row(
    *,
    bd_id: int,
    name: str,
    title: str,
    result: DiscoveryResult,
) -> ExecutiveContact:
    # ``source`` is the human-meaningful category; ``discovery_source`` is
    # the fine-grained provider identifier returned inside DiscoveryResult.
    # Keeping both fields lets existing UI code that groups by ``source``
    # keep working while new UI can sort / filter on ``discovery_source``.
    source = "apollo" if result.provider.startswith("apollo") else result.provider
    return ExecutiveContact(
        bd_id=bd_id,
        name=name,
        title=title[:255],
        email=result.email,
        phone=result.phone,
        linkedin_url=result.linkedin_url,
        emails=_array_or_none(result.emails),
        phones=_array_or_none(result.phones),
        source=source,
        discovery_source=result.provider[:32],
        discovery_confidence=Decimal(str(round(result.confidence, 2))),
        apollo_person_id=result.apollo_person_id,
        enriched_at=datetime.now(timezone.utc),
    )


def _apply_discovery_to_executive(
    row: ExecutiveContact,
    *,
    title: str,
    result: DiscoveryResult,
) -> None:
    """Refresh an existing executive row in place from a new discovery result.

    Mirrors ``_build_executive_row`` field-for-field, with two don't-regress
    rules for the forced re-run path: an existing ``phone`` / ``phones``
    (which may have been filled asynchronously by the Apollo phone-reveal
    webhook) is kept when this run surfaced none, and ``apollo_person_id`` is
    preserved when the new result lacks one so a pending reveal can still
    correlate. ``bd_id`` / ``name`` are the lookup keys and stay put.
    """
    source = "apollo" if result.provider.startswith("apollo") else result.provider
    row.title = title[:255]
    row.email = result.email or row.email
    row.linkedin_url = result.linkedin_url or row.linkedin_url
    if result.phone:
        row.phone = result.phone
    if result.phones:
        row.phones = _array_or_none(result.phones)
    if result.emails:
        row.emails = _array_or_none(result.emails)
    row.source = source
    row.discovery_source = result.provider[:32]
    row.discovery_confidence = Decimal(str(round(result.confidence, 2)))
    row.apollo_person_id = result.apollo_person_id or row.apollo_person_id
    row.enriched_at = datetime.now(timezone.utc)


def _build_investor_row(
    *,
    investor_id: int,
    name: str,
    title: str,
    result: DiscoveryResult,
) -> InvestorContact:
    source = "apollo" if result.provider.startswith("apollo") else result.provider
    return InvestorContact(
        investor_id=investor_id,
        name=name,
        title=title[:255],
        email=result.email,
        phone=result.phone,
        linkedin_url=result.linkedin_url,
        emails=_array_or_none(result.emails),
        phones=_array_or_none(result.phones),
        source=source,
        discovery_source=result.provider[:32],
        discovery_confidence=Decimal(str(round(result.confidence, 2))),
        apollo_person_id=result.apollo_person_id,
        enriched_at=datetime.now(timezone.utc),
    )


def _build_advisor_row(
    *,
    advisor_id: int,
    name: str,
    title: str,
    result: DiscoveryResult,
) -> AdvisorContact:
    source = "apollo" if result.provider.startswith("apollo") else result.provider
    return AdvisorContact(
        advisor_id=advisor_id,
        name=name,
        title=title[:255],
        email=result.email,
        phone=result.phone,
        linkedin_url=result.linkedin_url,
        emails=_array_or_none(result.emails),
        phones=_array_or_none(result.phones),
        source=source,
        discovery_source=result.provider[:32],
        discovery_confidence=Decimal(str(round(result.confidence, 2))),
        apollo_person_id=result.apollo_person_id,
        enriched_at=datetime.now(timezone.utc),
    )


def _array_or_none(hits: list[Any]) -> list[dict[str, Any]] | None:
    """Serialise EmailHit / PhoneHit dataclasses to JSONB-ready dicts.

    Returns ``None`` when empty so the column stays NULL and the schema
    layer's read-time synthesis projects the scalar instead.
    """
    return [asdict(hit) for hit in hits] or None
