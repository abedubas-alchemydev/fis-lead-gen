"""Provider-chain orchestrator for discovered-email enrichment.

Walks ``settings.email_enrichment_chain`` IN ORDER and gap-fills a single
:class:`EnrichmentData` across the configured providers: the first provider to
return a field wins it, later providers fill only the fields still empty. So
Apollo's name / title / LinkedIn and a phone scraped by the web fallback can
land together on one row. Stops early once every field is filled.

Failure isolation: an unconfigured provider is skipped (not a miss); a provider
that raises is logged and treated as a provider error (not fatal). The final
row status is decided from the outcome tally:

* any field populated      -> ``enriched``
* none populated, >=1 ran  -> ``no_match`` (providers ran, honestly found nothing)
* none populated, all errored -> ``error`` (retryable -- the UI shows Retry)

This is the single entrypoint the API + bulk loop call; it owns the DB write
(commit + refresh) so providers stay pure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.discovered_email import DiscoveredEmail
from app.services.contact_discovery._shared import apollo_phone_reveal_configured
from app.services.email_extractor.enrichment.apollo import ApolloEnrichProvider
from app.services.email_extractor.enrichment.base import (
    MERGE_FIELDS,
    NOT_CONFIGURED_MESSAGE,
    NOT_FOUND_MESSAGE,
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
)
from app.services.email_extractor.enrichment.hunter import HunterEnrichProvider
from app.services.email_extractor.enrichment.name_lookup import NameLookupEnrichProvider
from app.services.email_extractor.enrichment.pdl import PdlEnrichProvider
from app.services.email_extractor.enrichment.snov import SnovEnrichProvider
from app.services.email_extractor.enrichment.web_scraper import WebScraperEnrichProvider

logger = logging.getLogger(__name__)

# All registered providers, keyed by their chain name. The active chain +
# order come from settings.email_enrichment_chain, so trimming/reordering is an
# env change with no code edit.
_PROVIDERS: dict[str, EmailEnrichmentProvider] = {
    "apollo": ApolloEnrichProvider(),
    "pdl": PdlEnrichProvider(),
    "hunter": HunterEnrichProvider(),
    "snov": SnovEnrichProvider(),
    "name_lookup": NameLookupEnrichProvider(),
    "web_scraper": WebScraperEnrichProvider(),
}


def _chain_names() -> list[str]:
    return [name.strip() for name in (settings.email_enrichment_chain or "").split(",") if name.strip()]


def configured_providers() -> list[EmailEnrichmentProvider]:
    """Providers named in the chain that have their credentials/flags set, in
    chain order."""
    providers: list[EmailEnrichmentProvider] = []
    for name in _chain_names():
        provider = _PROVIDERS.get(name)
        if provider is not None and provider.is_configured():
            providers.append(provider)
    return providers


def any_provider_configured() -> bool:
    """True when at least one chain provider is ready to run -- the gate the
    enrich endpoints use before queuing work."""
    return bool(configured_providers())


async def enrich_discovered_email(db: AsyncSession, discovered_email_id: int) -> DiscoveredEmail:
    """Enrich one ``DiscoveredEmail`` by walking the provider chain.

    Commits the updated row and returns it. Raises :class:`EnrichmentError`
    with :data:`NOT_FOUND_MESSAGE` (no such row) or :data:`NOT_CONFIGURED_MESSAGE`
    (no provider configured); the API layer maps those to 404 / 503.
    """
    row = await db.get(DiscoveredEmail, discovered_email_id)
    if row is None:
        raise EnrichmentError(NOT_FOUND_MESSAGE)

    providers = configured_providers()
    if not providers:
        raise EnrichmentError(NOT_CONFIGURED_MESSAGE)

    merged = EnrichmentData()
    ran_ok = 0
    errored = 0
    # The async Apollo phone-reveal webhook correlates a revealed number back to
    # this row by ``apollo_person_id``. Apollo dispatches that reveal the moment
    # it matches (first provider in the chain), but the callback can land in
    # 1-13s -- well before a slow downstream provider (hunter 429-backoff, snov
    # timeout) lets the chain finish. So the moment a provider yields a person id
    # we persist+commit the correlation key IMMEDIATELY, ahead of the slow tail,
    # so the webhook always finds the row. Once-only: guarded by this flag.
    apollo_id_persisted = False
    for provider in providers:
        # The fields still empty -- lets a provider skip expensive sub-steps
        # whose output would just be discarded (see web_scraper).
        needed = {field for field in MERGE_FIELDS if getattr(merged, field) is None}
        try:
            data = await provider.enrich(row.email, needed)
        except EnrichmentError as exc:
            errored += 1
            logger.warning(
                "enrichment provider %s failed for email_id=%s: %s",
                provider.name,
                discovered_email_id,
                exc,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - a provider bug must not abort the chain
            errored += 1
            logger.warning(
                "enrichment provider %s raised for email_id=%s: %r",
                provider.name,
                discovered_email_id,
                exc,
            )
            continue

        ran_ok += 1
        if data is not None:
            _merge(merged, data)
            # Close the phone-reveal race: stamp + commit apollo_person_id the
            # instant we have it, before continuing to slower providers, so the
            # webhook (separate request/txn, arrives in seconds) can correlate.
            # phone_reveal_requested_at starts the spinner TTL at the true
            # request time. Only when no inline phone landed and reveal is wired
            # -- the same gate _apply and phone_reveal_pending read.
            if (
                not apollo_id_persisted
                and merged.apollo_person_id is not None
                and row.enriched_phone is None
                and apollo_phone_reveal_configured()
            ):
                row.apollo_person_id = merged.apollo_person_id
                row.phone_reveal_requested_at = datetime.now(UTC)
                await db.commit()
                apollo_id_persisted = True
            if merged.is_complete():
                break

    # The webhook may have written ``enriched_phone`` between the early commit
    # and here (separate txn). With expire_on_commit=False our session still
    # holds the row with the stale ``enriched_phone=None`` it loaded, so refresh
    # to pull in any webhook-written phone before the final write -- otherwise
    # the chain-end commit would flush NULL back over it. Only needed when the
    # early commit fired (a reveal is genuinely in flight).
    if apollo_id_persisted:
        # Pull a webhook-written phone in BEFORE our final write (set-if-null in
        # _apply then can't clobber it).
        await db.refresh(row)

    _apply(row, merged, ran_ok=ran_ok, errored=errored)
    await db.commit()
    await db.refresh(row)  # return the canonical row after our own commit
    return row


def _merge(into: EnrichmentData, src: EnrichmentData) -> None:
    """Gap-fill ``into`` from ``src``: only fields still empty are taken, so the
    first provider in chain order wins each field."""
    for field in MERGE_FIELDS:
        if getattr(into, field) is None and getattr(src, field) is not None:
            setattr(into, field, getattr(src, field))
    if into.apollo_person_id is None and src.apollo_person_id is not None:
        into.apollo_person_id = src.apollo_person_id


def _apply(
    row: DiscoveredEmail,
    merged: EnrichmentData,
    *,
    ran_ok: int,
    errored: int,
) -> None:
    """Write the merged result onto the row + set status. Non-destructive: only
    populated fields are written, so a retry never erases prior data."""
    if merged.has_any():
        if merged.name is not None:
            row.enriched_name = merged.name
        if merged.title is not None:
            row.enriched_title = merged.title
        if merged.company is not None:
            row.enriched_company = merged.company
        if merged.linkedin_url is not None:
            row.enriched_linkedin_url = merged.linkedin_url
        if merged.email is not None:
            row.enriched_email = merged.email
        # Set-if-NULL, not set-if-present: the async phone-reveal webhook (a
        # separate txn) may have written enriched_phone after the early commit
        # for apollo_person_id. Guard against clobbering it here in case the
        # webhook lands in the micro-window between db.refresh(row) above and
        # this final commit. (Apollo's sync phone is null by design, so merged
        # rarely carries one anyway -- this only ever fills a still-empty cell.)
        if merged.phone is not None and row.enriched_phone is None:
            row.enriched_phone = merged.phone
        if merged.apollo_person_id is not None:
            row.apollo_person_id = merged.apollo_person_id
        row.enrichment_status = "enriched"
    elif ran_ok > 0:
        # Providers ran and honestly found nothing.
        row.enrichment_status = "no_match"
    else:
        # Every configured provider errored -> retryable; the UI shows Retry.
        row.enrichment_status = "error"

    row.enriched_at = datetime.now(UTC)

    # Stamp when the async Apollo phone-reveal was actually requested so
    # phone_reveal_pending can bound the "finding phone…" spinner with a TTL.
    # The reveal goes out exactly when Apollo matched a person (id stamped),
    # the reveal flow is wired, and no phone landed inline -- the same gate the
    # property reads. Skip it if a phone arrived synchronously (nothing pending)
    # or reveal isn't configured (no callback will ever come).
    #
    # Set-if-NULL: the orchestrator stamps this the moment the id is first seen
    # (the early-commit branch in enrich_discovered_email), which is the TRUE
    # request time. Don't overwrite that earlier, more-correct timestamp with a
    # chain-end one -- only stamp here for the (rare) path that reaches _apply
    # without having taken the early commit.
    if (
        row.apollo_person_id is not None
        and row.enriched_phone is None
        and row.phone_reveal_requested_at is None
        and apollo_phone_reveal_configured()
    ):
        row.phone_reveal_requested_at = datetime.now(UTC)
