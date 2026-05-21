"""Per-person phone lookup for institutional-investor contacts.

Mirror of ``ExecutiveContactService.find_phone_for_contact`` in
``app/services/contacts.py``: PDL primary, Apollo fallback, populates the
``phones`` JSONB array on a hit, never regresses an existing non-null
phone. Lives as a sibling service rather than a polymorphic refactor of
the BD service so the BD path stays untouched (matches the codebase's
stated pattern for ``InvestorContact`` -- see its class docstring).

Shares the same exception classes as the BD service so the endpoint
layer can map them with one ``except`` ladder regardless of entity type.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery import pdl
from app.services.contact_discovery._shared import first_apollo_phone
from app.services.contact_discovery.base import DiscoveryResult
from app.services.contacts import (
    ApolloLookupError,
    ContactEnrichmentUnavailableError,
    ContactNotFoundError,
    NoEmailForLookupError,
)

logger = logging.getLogger(__name__)


class InvestorContactService:
    _APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"

    async def find_phone_for_contact(
        self, db: AsyncSession, contact_id: int
    ) -> InvestorContact:
        """Per-person phone lookup for an InvestorContact.

        Same chain semantics as the BD ``find_phone_for_contact``: PDL via
        ``pdl.enrich_by_email`` runs first, Apollo ``/people/match`` is the
        fallback when PDL has no match / no key / a hard error. PDL errors
        are silenced (graceful degradation -- a flaky upstream shouldn't
        break the per-row button); Apollo's transient errors still raise
        ``ApolloLookupError`` so the FE can show a 502.

        Never regresses an existing non-null phone. Only writes ``phone`` /
        ``phones`` / ``enriched_at`` -- name / title / linkedin (which may
        come from higher-trust sources like 13F ingestion) are untouched.
        """
        contact = await db.get(InvestorContact, contact_id)
        if contact is None:
            raise ContactNotFoundError(f"contact {contact_id} not found")
        if not contact.email:
            raise NoEmailForLookupError("contact has no email to look up")

        if not settings.pdl_api_key and not settings.apollo_api_key:
            raise ContactEnrichmentUnavailableError(
                "Contact enrichment is disabled. Set PDL_API_KEY (preferred) "
                "or APOLLO_API_KEY in the backend .env file."
            )

        # ── PDL primary ──────────────────────────────────────────────
        pdl_hit: DiscoveryResult | None = None
        if settings.pdl_api_key:
            try:
                pdl_hit = await pdl.enrich_by_email(contact.email)
            except Exception as exc:  # noqa: BLE001 -- any PDL error = miss + fall through
                logger.warning(
                    "PDL enrich_by_email failed for %s: %s", contact.email, exc
                )

        if pdl_hit and pdl_hit.phones:
            contact.phones = [asdict(hit) for hit in pdl_hit.phones]
            if pdl_hit.phone:
                contact.phone = pdl_hit.phone
            contact.enriched_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(contact)
            return contact

        # ── Apollo fallback ──────────────────────────────────────────
        api_key = settings.apollo_api_key
        if not api_key:
            # PDL configured but missed; no Apollo to fall back on. Bump
            # enriched_at so the UI shows "tried, nothing found".
            contact.enriched_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(contact)
            return contact

        headers = {
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    self._APOLLO_MATCH_URL,
                    json={"email": contact.email},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ApolloLookupError(f"network: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code != 200:
            snippet = response.text[:200] if response.text else "(no body)"
            raise ApolloLookupError(f"http {response.status_code}: {snippet}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ApolloLookupError(f"invalid json: {exc}") from exc

        person = data.get("person") if isinstance(data, dict) else None
        phone = (
            first_apollo_phone(person.get("phone_numbers"))
            if isinstance(person, dict)
            else None
        )

        if phone:
            contact.phone = phone
        contact.enriched_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(contact)
        return contact
