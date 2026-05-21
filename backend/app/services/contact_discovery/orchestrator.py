"""Discovery chain orchestrator.

Two public entry points share the same multi-provider chain walk:

1. ``discover_contact(entity, bd_id, session)`` — resolves a broker-dealer
   officer into a persisted ``ExecutiveContact`` row. Called by the
   ``POST /api/v1/broker-dealers/{id}/enrich`` endpoint.
2. ``discover_investor_contact(entity, investor_id, session)`` — the
   institutional-investor sibling. Called by ``POST
   /api/v1/institutional-investors/{id}/enrich``.

Both:

1. Check a 90-day cache (per-entity-type table, name + FK lookup). Cache
   hit returns the row without touching any provider.
2. Otherwise walk the providers listed in ``settings.contact_discovery_chain``
   in order, calling ``find_person`` or ``find_org`` per entity type.
3. Accept the first result with ``confidence >=
   settings.contact_discovery_min_confidence``.
4. Persist that result as a typed row with the provider's native identifier
   on ``discovery_source`` (e.g. ``pdl``, ``apollo_match``, ``apollo_org``,
   ``hunter``, ``hunter_domain``, ``snov``, ``snov_domain``), the 0..100
   ``confidence`` on ``discovery_confidence``, and the provider's full
   ``emails`` / ``phones`` lists serialised into the JSONB columns
   (multi-value providers fill them; single-value providers leave them
   ``NULL`` and the schema layer synthesises a 1-element list from the
   scalar on read).

The commit is left to the caller so the endpoint can batch multiple
officers into a single transaction.

Provider failures are swallowed deliberately. A provider that raises is
logged and treated like a miss — one flaky upstream can't block the
whole chain.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.executive_contact import ExecutiveContact
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)
from app.services.contact_discovery.hunter import HunterProvider
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
) -> ExecutiveContact | None:
    """Resolve a broker-dealer officer into a persisted ``ExecutiveContact``.

    See module docstring for the chain semantics. The row is added to the
    session but **not** committed — the caller owns the transaction
    boundary.
    """
    parsed = _parse_entity(entity)
    if parsed is None:
        return None
    entity_type, first_name, last_name, org_name, domain, title, cache_name = parsed

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

    row = _build_executive_row(
        bd_id=bd_id,
        name=cache_name,
        title=title or ("Executive" if entity_type == "person" else "Organization"),
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
    """Walk the configured chain and return the first confident hit."""
    min_confidence = float(settings.contact_discovery_min_confidence)
    chain = [p.strip() for p in settings.contact_discovery_chain.split(",") if p.strip()]
    for provider_name in chain:
        provider = _PROVIDERS.get(provider_name)
        if provider is None:
            logger.warning("contact_discovery_chain references unknown provider %r", provider_name)
            continue
        try:
            if entity_type == "person":
                result = await provider.find_person(first_name, last_name, org_name, domain)
            else:
                result = await provider.find_org(org_name, domain)
        except Exception:  # noqa: BLE001 -- deliberately broad, see module docstring
            logger.exception("Provider %s raised during discovery", provider_name)
            result = None
        if result is None:
            continue
        if result.confidence < min_confidence:
            logger.info(
                "Provider %s returned %.1f for %s, below threshold %.1f",
                provider_name,
                result.confidence,
                cache_name,
                min_confidence,
            )
            continue
        return result
    return None


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
        enriched_at=datetime.now(timezone.utc),
    )


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
        enriched_at=datetime.now(timezone.utc),
    )


def _array_or_none(hits: list[Any]) -> list[dict[str, Any]] | None:
    """Serialise EmailHit / PhoneHit dataclasses to JSONB-ready dicts.

    Returns ``None`` when empty so the column stays NULL and the schema
    layer's read-time synthesis projects the scalar instead.
    """
    return [asdict(hit) for hit in hits] or None
