"""Discovery chain orchestrator.

``discover_contact`` is the single entry point used by the broker-dealer
``/enrich`` endpoint. Call it once per officer (person or organisation) and
it:

1. Checks the 90-day contact cache on ``executive_contacts`` (name + bd_id).
   If a recent row exists, returns it without touching any provider.
2. Otherwise walks the providers listed in ``settings.contact_discovery_chain``
   in order, calling ``find_person`` or ``find_org`` depending on the entity
   type.
3. Accepts the first result with ``confidence >= settings.contact_discovery_min_confidence``.
4. Persists that result as an ``ExecutiveContact`` row with the provider's
   native identifier on ``discovery_source`` (e.g. ``apollo_match``,
   ``apollo_org``, ``hunter``, ``hunter_domain``, ``snov``, ``snov_domain``)
   and the 0..100 ``confidence`` on ``discovery_confidence``.

The commit is left to the caller so the endpoint can batch multiple officers
into a single transaction.

Provider failures are swallowed deliberately. A provider that raises is
logged and treated like a miss -- one flaky upstream can't block the whole
chain.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.executive_contact import ExecutiveContact
from app.services.contact_discovery.apollo_match import ApolloMatchProvider
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)
from app.services.contact_discovery.hunter import HunterProvider
from app.services.contact_discovery.snov import SnovProvider

logger = logging.getLogger(__name__)


# Registry of known providers keyed by the identifier that appears in
# ``settings.contact_discovery_chain``. Instances are stateless so a single
# module-level copy is safe across requests.
_PROVIDERS: dict[str, ContactDiscoveryProvider] = {
    "apollo_match": ApolloMatchProvider(),
    "hunter": HunterProvider(),
    "snov": SnovProvider(),
}

_CACHE_TTL_DAYS = 90


async def discover_contact(
    entity: Mapping[str, Any],
    *,
    bd_id: int,
    session: AsyncSession,
) -> ExecutiveContact | None:
    """Resolve a single officer into a persisted ``ExecutiveContact`` row.

    ``entity`` is a plain dict from the endpoint layer, shape::

        {
            "type": "person" | "organization",
            "first_name": str | None,
            "last_name": str | None,
            "org_name": str,
            "title": str | None,
            "domain": str | None,
        }

    Returns the persisted row on success, ``None`` if no provider cleared the
    confidence threshold. The row is added to the session but **not**
    committed -- the caller owns the transaction boundary.
    """
    entity_type = str(entity.get("type") or "").strip().lower()
    if entity_type not in {"person", "organization"}:
        logger.warning("discover_contact called with unknown entity type %r", entity_type)
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

    # Cache key for persons is "First Last"; for organisations it's the org
    # itself. Both live in the same table, so a single name-based lookup
    # covers them.
    if entity_type == "person":
        if not first_name or not last_name:
            return None
        cache_name = f"{first_name} {last_name}"
    else:
        cache_name = org_name

    cached = await _find_cached(session, bd_id=bd_id, name=cache_name)
    if cached is not None:
        return cached

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

        row = _build_row(
            bd_id=bd_id,
            name=cache_name,
            title=title or ("Executive" if entity_type == "person" else "Organization"),
            result=result,
        )
        session.add(row)
        return row

    return None


async def _find_cached(
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


def _build_row(
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
        source=source,
        discovery_source=result.provider[:32],
        discovery_confidence=Decimal(str(round(result.confidence, 2))),
        enriched_at=datetime.now(timezone.utc),
    )
