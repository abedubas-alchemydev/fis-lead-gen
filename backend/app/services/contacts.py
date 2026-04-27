from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact

logger = logging.getLogger(__name__)


class ContactEnrichmentUnavailableError(RuntimeError):
    pass


class ExecutiveContactService:
    # Apollo People Enrichment API endpoint.  The search endpoints
    # (/mixed_people/search and /people/search) require paid plans.
    # The /mixed_people/search endpoint is tried first (works on paid);
    # if 403, fall back to /people/match (single-person enrichment on free tier).
    _APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"
    _APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"

    async def list_contacts(self, db: AsyncSession, broker_dealer_id: int) -> list[ExecutiveContact]:
        stmt = (
            select(ExecutiveContact)
            .where(ExecutiveContact.bd_id == broker_dealer_id)
            .order_by(ExecutiveContact.enriched_at.desc(), ExecutiveContact.id.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def enrich_contacts(
        self,
        db: AsyncSession,
        broker_dealer: BrokerDealer,
        *,
        force: bool = False,
    ) -> list[ExecutiveContact]:
        existing = await self.list_contacts(db, broker_dealer.id)

        # Server-side cooldown. Stops the detail-page useEffect from re-firing
        # /enrich on every visit for empty-result firms (where the legacy
        # 90-day guard below never triggers because no ExecutiveContact rows
        # exist to read enriched_at off). NULL last_enrich_attempt_at means
        # "never attempted" -> first-time calls fall through. cooldown_hours=0
        # disables the guard entirely.
        cooldown_hours = settings.apollo_enrich_cooldown_hours
        if (
            not force
            and cooldown_hours > 0
            and broker_dealer.last_enrich_attempt_at is not None
        ):
            cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
            if broker_dealer.last_enrich_attempt_at >= cutoff:
                return existing

        if not force and existing:
            newest = max(item.enriched_at for item in existing)
            if newest >= datetime.now(timezone.utc) - timedelta(days=90):
                return existing

        provider = settings.contact_enrichment_provider.lower()
        if provider == "apollo" and settings.apollo_api_key:
            contacts, apollo_errored = await self._enrich_via_apollo(broker_dealer)
        elif provider != "disabled":
            raise ContactEnrichmentUnavailableError(
                f"Contact enrichment provider '{provider}' is not configured or missing API key. "
                "Set CONTACT_ENRICHMENT_PROVIDER=apollo and APOLLO_API_KEY in the backend .env file."
            )
        else:
            raise ContactEnrichmentUnavailableError(
                "Contact enrichment is disabled. Set CONTACT_ENRICHMENT_PROVIDER=apollo "
                "and APOLLO_API_KEY in the backend .env file."
            )

        # Transient Apollo error (5xx, 429, network) -> leave existing rows
        # and the cooldown timestamp untouched so the next visit retries
        # instead of being locked out for 24h by a 502 from Apollo.
        if apollo_errored:
            return existing

        # Apollo-owned outcome (success or no-result). Wipe stale non-FOCUS
        # rows, add any new contacts, stamp the cooldown timestamp, and
        # commit atomically. FOCUS-extracted CEO data is preserved so the
        # two sources continue to coexist.
        await db.execute(
            delete(ExecutiveContact).where(
                ExecutiveContact.bd_id == broker_dealer.id,
                ExecutiveContact.source != "focus_report",
            )
        )
        if contacts:
            db.add_all(contacts)
        broker_dealer.last_enrich_attempt_at = datetime.now(timezone.utc)
        await db.commit()
        return await self.list_contacts(db, broker_dealer.id)

    async def _enrich_via_apollo(
        self, broker_dealer: BrokerDealer
    ) -> tuple[list[ExecutiveContact], bool]:
        """Enrich contacts via Apollo.io.

        Returns ``(contacts, apollo_errored)``. ``apollo_errored`` is True
        when at least one Apollo HTTP attempt failed transiently (network
        timeout, 5xx, 429) AND no strategy produced any people. The caller
        uses this flag to decide whether to engage the cooldown timestamp:
        we only want to lock out future calls when Apollo "owned" the
        outcome (success or genuine no-result), never when a 502 made us
        give up early.

        Strategy cascade:
        1. People search (/mixed_people/search) — requires paid plan.
        2. Organization enrich (/organizations/enrich) — works on free tier.
           Returns company domain, phone, LinkedIn.  From the domain we can
           derive an org-level contact record.
        """
        company_name = broker_dealer.name.strip()
        if not company_name:
            return [], False

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": settings.apollo_api_key or "",
        }

        people: list[dict] = []
        apollo_errored = False

        # ── Strategy 1: People search (paid plans) ──────────
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._APOLLO_SEARCH_URL,
                    headers=headers,
                    json={
                        "q_organization_name": company_name,
                        "page": 1,
                        "per_page": 10,
                        "person_titles": [
                            "CEO", "Chief Executive Officer",
                            "CFO", "Chief Financial Officer",
                            "COO", "Chief Operating Officer",
                            "President", "Managing Director",
                        ],
                    },
                )
                if response.status_code == 200:
                    people = response.json().get("people") or []
                elif response.status_code == 429 or 500 <= response.status_code < 600:
                    apollo_errored = True
                    logger.warning("Apollo search returned %d for '%s'", response.status_code, company_name)
                elif response.status_code != 403:
                    logger.warning("Apollo search returned %d for '%s'", response.status_code, company_name)
        except httpx.HTTPError as exc:
            apollo_errored = True
            logger.warning("Apollo search network error for '%s': %s", company_name, exc)

        # ── Strategy 2: Org enrich -> domain-based contacts (free tier) ──
        if not people:
            people, org_errored = await self._enrich_via_org_lookup(headers, company_name, broker_dealer)
            apollo_errored = apollo_errored or org_errored

        if not people or not isinstance(people, list):
            if not apollo_errored:
                logger.info("Apollo returned 0 contacts for '%s'.", company_name)
            return [], apollo_errored

        now = datetime.now(timezone.utc)
        contacts: list[ExecutiveContact] = []
        seen_names: set[str] = set()

        for person in people[:5]:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            title = str(person.get("title") or person.get("headline") or "Executive").strip()
            email = person.get("email")
            phone_obj = person.get("phone_numbers")
            phone = None
            if isinstance(phone_obj, list) and phone_obj:
                first_phone = phone_obj[0]
                if isinstance(first_phone, dict):
                    phone = str(first_phone.get("sanitized_number") or first_phone.get("raw_number") or "").strip() or None
                elif isinstance(first_phone, str):
                    phone = first_phone.strip() or None
            linkedin_url = person.get("linkedin_url")

            contacts.append(
                ExecutiveContact(
                    bd_id=broker_dealer.id,
                    name=name,
                    title=title[:255],
                    email=str(email).strip() if email else None,
                    phone=phone,
                    linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
                    source="apollo",
                    enriched_at=now,
                )
            )

        if not contacts:
            logger.info("Apollo returned 0 contacts for '%s'.", company_name)

        return contacts, False

    async def _enrich_via_org_lookup(
        self,
        headers: dict[str, str],
        company_name: str,
        broker_dealer: BrokerDealer,
    ) -> tuple[list[dict], bool]:
        """Use the Apollo org-enrich endpoint (free tier) to get company data,
        then build a synthetic org-level contact record with whatever data
        Apollo provides (phone, LinkedIn, domain).

        Returns ``(people, errored)``. ``errored`` is True when at least one
        domain attempt failed transiently (network / 5xx / 429) so the
        caller can avoid engaging the cooldown timestamp on what looks like
        a no-result but is actually a flaky Apollo response.
        """
        domain_guesses = self._guess_domains(company_name)
        apollo_errored = False

        for domain in domain_guesses:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.post(
                        "https://api.apollo.io/api/v1/organizations/enrich",
                        headers=headers,
                        json={"domain": domain},
                    )
                    if resp.status_code != 200:
                        if resp.status_code == 429 or 500 <= resp.status_code < 600:
                            apollo_errored = True
                        continue

                    org = resp.json().get("organization")
                    if not org or not isinstance(org, dict):
                        continue

                    org_name = org.get("name") or company_name
                    linkedin = org.get("linkedin_url")
                    primary_phone = org.get("primary_phone")
                    phone = None
                    if isinstance(primary_phone, dict):
                        phone = primary_phone.get("sanitized_number") or primary_phone.get("number")

                    # Build a company-level contact record.
                    return (
                        [
                            {
                                "name": org_name,
                                "title": "Company (Organization Profile)",
                                "email": None,
                                "phone_numbers": [{"sanitized_number": phone}] if phone else [],
                                "linkedin_url": linkedin,
                            }
                        ],
                        apollo_errored,
                    )
            except httpx.HTTPError as exc:
                apollo_errored = True
                logger.debug("Org enrich failed for domain '%s': %s", domain, exc)
                continue

        return [], apollo_errored

    @staticmethod
    def _guess_domains(company_name: str) -> list[str]:
        """Generate plausible website domains from a broker-dealer name."""
        import re as _re
        clean = _re.sub(r"[^a-z0-9 ]+", "", company_name.lower()).strip()
        tokens = [t for t in clean.split() if t not in {"llc", "inc", "corp", "lp", "ltd", "the", "of", "and", "a"}]
        if not tokens:
            tokens = clean.split()[:1]

        guesses: list[str] = []
        # "BTIG, LLC" -> "btig.com"
        if tokens:
            guesses.append(f"{tokens[0]}.com")
        # "Wedbush Securities Inc." -> "wedbush.com"  (already covered above)
        # Full slug: "wedbushsecurities.com"
        if len(tokens) > 1:
            guesses.append(f"{''.join(tokens)}.com")
            guesses.append(f"{tokens[0]}{tokens[1]}.com")
        return guesses[:4]
