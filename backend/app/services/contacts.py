from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.services.contact_discovery import pdl
from app.services.contact_discovery._shared import first_apollo_phone
from app.services.contact_discovery.base import DiscoveryResult

logger = logging.getLogger(__name__)


class ContactEnrichmentUnavailableError(RuntimeError):
    pass


class ContactNotFoundError(RuntimeError):
    pass


class NoEmailForLookupError(RuntimeError):
    """find_phone_for_contact() called on a contact with no email to look up."""


class ApolloLookupError(RuntimeError):
    """Apollo /people/match returned a transient failure (network / 5xx / 429)."""


class ExecutiveContactService:
    # Apollo /people/match — single-person enrichment, returns verified email +
    # LinkedIn on Basic/Pro plans. This is the canonical enrichment path.
    #
    # The previous bulk search endpoint /mixed_people/search was deprecated by
    # Apollo (returns 422 → "use the new mixed_people/api_search endpoint"),
    # and the new search endpoint masks names/emails on our tier — so the only
    # path that returns usable per-officer PII is /people/match called once per
    # officer. Officers come from FINRA Form BD (direct_owners +
    # executive_officers JSONB on the BD row).
    _APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
    _APOLLO_ORG_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"

    # Caps the per-BD fan-out cost. FINRA's executive_officers list can list
    # 20+ rows for large firms; we focus on the senior names so a single
    # detail-page visit doesn't burn 25 Apollo credits.
    _MAX_OFFICER_FANOUT = 10
    _APOLLO_FANOUT_CONCURRENCY = 5
    _APOLLO_PER_OFFICER_TIMEOUT_S = 20.0

    # Tokens that mark a FINRA owner row as an entity rather than a person —
    # we skip these when building the /people/match fan-out list because
    # Apollo's people endpoints don't enrich organizations. Reused by
    # _firm_tokens to normalise firm names before the org-match guard.
    _ORG_NAME_TOKENS = re.compile(
        r"(?<!\w)(LLC|L\.L\.C\.|LLP|L\.L\.P\.|INC\.?|INCORPORATED|"
        r"CORP\.?|CORPORATION|L\.P\.|LP|LTD\.?|LIMITED|HOLDINGS|"
        r"GROUP|MANAGEMENT|PARTNERS|PLC|TRUST|FUND|COMPANY|CO\.)(?!\w)",
        re.IGNORECASE,
    )

    # English filler words stripped when comparing firm names. Keeping the
    # set tight ("of", "and", "the") so legitimate token-overlap matches
    # aren't lost. Single letters and "&" / "+" are filtered separately by
    # the length>=2 rule in _firm_tokens.
    _FIRM_NAME_STOPWORDS = frozenset(
        {"the", "a", "an", "of", "and", "for", "in", "to"}
    )

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
        """Enrich contacts via Apollo.io using per-officer /people/match fan-out.

        Returns ``(contacts, apollo_errored)``. ``apollo_errored`` is True
        when at least one Apollo HTTP attempt failed transiently (network
        timeout, 5xx, 429) AND we ended up with zero contacts to write. The
        caller uses this flag to decide whether to engage the cooldown
        timestamp: we only want to lock out future calls when Apollo "owned"
        the outcome (success or genuine no-result), never when a 502 made
        us give up early.

        Strategy cascade:

        1. Per-officer /people/match — for each FINRA-listed person owner /
           executive officer, call Apollo's enrichment endpoint with first +
           last name + organization. This is the only Apollo path that
           returns verified emails and LinkedIn URLs on our plan; the bulk
           /mixed_people/search endpoint is deprecated and the replacement
           masks PII for callers on Basic/Pro tiers.
        2. /organizations/enrich fallback — used only when FINRA has no
           person officers OR every per-officer match returned nothing.
           Yields a single synthetic "Company (Organization Profile)" row
           carrying the firm's HQ phone and LinkedIn, so we still surface
           something for firms whose officers Apollo doesn't know.
        """
        company_name = broker_dealer.name.strip()
        if not company_name:
            return [], False

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": settings.apollo_api_key or "",
        }

        officers = self._extract_person_officers(broker_dealer)
        contacts: list[ExecutiveContact] = []
        apollo_errored = False

        if officers:
            contacts, apollo_errored = await self._match_officers_via_apollo(
                broker_dealer, officers, headers
            )

        # Fall back to org-level enrichment when the per-officer path produced
        # zero contacts. That covers two cases:
        #   - FINRA listed only entities (e.g. CARLYLE INVESTMENT MANAGEMENT,
        #     L.L.C.) as the owner so there were no person officers to match.
        #   - Every /people/match call returned no person (Apollo doesn't
        #     know the FINRA-listed humans for this firm).
        # In both cases the org row gives the UI something to show. The
        # tightened gate (see refresh_all_orchestrator.has_executive_contacts)
        # ensures this synthetic row does NOT permanently close the enrich
        # gate, so a future visit can still upgrade to per-officer data.
        if not contacts:
            org_people, org_errored = await self._enrich_via_org_lookup(
                headers, company_name, broker_dealer
            )
            apollo_errored = apollo_errored or org_errored
            contacts = self._build_contacts_from_org_payload(
                broker_dealer, org_people
            )

        if not contacts:
            logger.info("Apollo returned 0 contacts for '%s'.", company_name)

        return contacts, apollo_errored

    def _extract_person_officers(
        self, broker_dealer: BrokerDealer
    ) -> list[dict[str, str]]:
        """Pull (first, last, title) tuples from FINRA officer JSONB columns.

        Skips org-shaped rows (CARLYLE INVESTMENT MANAGEMENT, L.L.C., etc.),
        dedupes on lowercased (first, last), and caps to ``_MAX_OFFICER_FANOUT``
        to bound the per-BD Apollo spend. Order: direct_owners first
        (typically smaller / higher-confidence list), then executive_officers.
        """
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        source_rows = list(broker_dealer.direct_owners or []) + list(
            broker_dealer.executive_officers or []
        )
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            parsed = self._parse_finra_person_name(name)
            if not parsed:
                continue
            first, last = parsed
            key = (first.lower(), last.lower())
            if key in seen:
                continue
            seen.add(key)
            title = str(row.get("title") or "Executive").strip() or "Executive"
            out.append({"first": first, "last": last, "title": title})
            if len(out) >= self._MAX_OFFICER_FANOUT:
                break
        return out

    @classmethod
    def _parse_finra_person_name(cls, raw: str) -> tuple[str, str] | None:
        """Parse FINRA's ``LAST, FIRST M.`` format into ``(first, last)``.

        Returns ``None`` for org-shaped rows so the caller can skip them.
        Detection rule: if the string contains an org-suffix token (LLC,
        INC, L.P., HOLDINGS, etc.) we treat it as an entity. Otherwise we
        split on the first comma and take the first whitespace-token after
        it as the given name. Title-cases both halves so the resulting
        display name matches the frontend's nameMatches() parsing path
        (``parseApolloName`` vs ``parseFinraName``).
        """
        if not raw or "," not in raw:
            return None
        if cls._ORG_NAME_TOKENS.search(raw):
            return None
        last_raw, rest = raw.split(",", 1)
        last_raw = last_raw.strip()
        rest = rest.strip()
        if not last_raw or not rest:
            return None
        first_token = rest.split()[0] if rest.split() else ""
        # Strip a trailing period from initials like "J." so "Charles J." parses
        # the same way as "Charles".
        first_token = first_token.rstrip(".")
        if not first_token or not first_token.isalpha():
            return None
        return first_token.title(), last_raw.title()

    async def _match_officers_via_apollo(
        self,
        broker_dealer: BrokerDealer,
        officers: list[dict[str, str]],
        headers: dict[str, str],
    ) -> tuple[list[ExecutiveContact], bool]:
        """Fan out /people/match per officer in parallel.

        Returns ``(contacts, apollo_errored)``. ``apollo_errored`` is True
        only when AT LEAST ONE call hit a transient failure AND zero matches
        succeeded — that's the signal the orchestrator uses to skip stamping
        the cooldown so the next visit retries.
        """
        domain = self._website_domain(broker_dealer.website)
        sem = asyncio.Semaphore(self._APOLLO_FANOUT_CONCURRENCY)

        async def match_one(o: dict[str, str]) -> tuple[dict[str, str], dict | None, bool]:
            async with sem:
                person, errored = await self._apollo_people_match(
                    headers,
                    first_name=o["first"],
                    last_name=o["last"],
                    org_name=broker_dealer.name.strip(),
                    domain=domain,
                )
                return o, person, errored

        results = await asyncio.gather(
            *[match_one(o) for o in officers],
            return_exceptions=False,
        )

        now = datetime.now(timezone.utc)
        contacts: list[ExecutiveContact] = []
        any_errored = False

        for officer, person, errored in results:
            if errored:
                any_errored = True
            if not isinstance(person, dict):
                continue
            # Reject cross-firm pollution. Apollo's /people/match can
            # return a same-name person at a different company when it
            # has no real match at the target firm. Probe found a Morgan
            # Stanley "Patricia Fletcher" matched to a Catholic charity
            # — without this guard her email + LinkedIn would land on
            # the Morgan Stanley card.
            apollo_org_name = self._resolve_apollo_org_name(person)
            if not self._firm_name_matches(
                apollo_org_name, broker_dealer.name
            ):
                logger.info(
                    "Apollo match for %s %s rejected: org %r != %r",
                    officer["first"],
                    officer["last"],
                    apollo_org_name,
                    broker_dealer.name,
                )
                continue
            email = person.get("email")
            email_clean = str(email).strip() if email else None
            linkedin_url = person.get("linkedin_url")
            linkedin_clean = (
                str(linkedin_url).strip() if linkedin_url else None
            )
            phone = self._first_phone_value(person.get("phone_numbers"))
            # Apollo /people/match can return an "I think this is the person"
            # match with no email/linkedin/phone (just name + title). Skip
            # those — they would just be empty rows that fail the
            # ContactRow render guard (no contact channel = no row shown).
            if not email_clean and not linkedin_clean and not phone:
                continue
            display_name = f"{officer['first']} {officer['last']}"
            contacts.append(
                ExecutiveContact(
                    bd_id=broker_dealer.id,
                    name=display_name,
                    title=officer["title"][:255],
                    email=email_clean,
                    phone=phone,
                    linkedin_url=linkedin_clean,
                    source="apollo",
                    enriched_at=now,
                )
            )

        # Only treat the call as "errored" when we have nothing to write.
        # Partial transient failures with at least one good match should
        # still stamp the cooldown so we don't burn credits hammering the
        # same firm every visit.
        return contacts, (any_errored and not contacts)

    async def _apollo_people_match(
        self,
        headers: dict[str, str],
        *,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> tuple[dict | None, bool]:
        """Single /people/match call.

        Returns ``(person_dict_or_None, errored)``. ``errored`` is True for
        transient failures (5xx / 429 / network) so the caller can decide
        whether to stamp the cooldown. A 404 / 200-with-no-person is NOT an
        error — it's a clean "Apollo doesn't know this person".
        """
        payload: dict[str, object] = {
            "first_name": first_name,
            "last_name": last_name,
            "organization_name": org_name,
        }
        if domain:
            payload["domain"] = domain
        try:
            async with httpx.AsyncClient(
                timeout=self._APOLLO_PER_OFFICER_TIMEOUT_S
            ) as client:
                response = await client.post(
                    self._APOLLO_MATCH_URL, headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "Apollo /people/match network error for %s %s: %s",
                first_name,
                last_name,
                exc,
            )
            return None, True

        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                return None, False
            person = body.get("person") if isinstance(body, dict) else None
            return (person if isinstance(person, dict) else None), False

        if response.status_code == 429 or 500 <= response.status_code < 600:
            logger.warning(
                "Apollo /people/match transient %d for %s %s",
                response.status_code,
                first_name,
                last_name,
            )
            return None, True

        # 4xx other than 429 (404, 422, etc.) — treat as clean no-match.
        logger.info(
            "Apollo /people/match returned %d for %s %s",
            response.status_code,
            first_name,
            last_name,
        )
        return None, False

    @classmethod
    def _firm_tokens(cls, name: str) -> frozenset[str]:
        """Tokenize a firm name for comparison: lowercase, strip org-suffix
        words (LLC/INC/L.P./HOLDINGS/etc), drop stopwords, keep distinctive
        word tokens of length >= 2."""
        if not name:
            return frozenset()
        cleaned = cls._ORG_NAME_TOKENS.sub(" ", name.lower())
        tokens = re.findall(r"[a-z0-9]+", cleaned)
        return frozenset(
            t for t in tokens if t not in cls._FIRM_NAME_STOPWORDS and len(t) >= 2
        )

    @staticmethod
    def _resolve_apollo_org_name(person: dict) -> str | None:
        """Resolve the person's current employer name from an Apollo
        ``/people/match`` record, tolerant of Apollo's response variance.

        Apollo is inconsistent about echoing the top-level ``organization``
        object: on a staging sample the same person came back WITH it on one
        call and WITHOUT it on another. When it's absent, the employer name is
        still reliably carried in ``employment_history`` (the entry flagged
        ``current``). The firm-name guard reads only ``organization.name``, so
        without this fallback it rejected ~62% of genuine matches purely
        because Apollo didn't include the org object — the root cause of the
        broker-dealer enrich failure rate.

        Resolution order: ``organization.name`` → current ``employment_history``
        entry's ``organization_name`` → first listed employment entry. Returns
        ``None`` only when Apollo gives no employer signal at all, in which case
        the caller's guard still (correctly) drops the match.
        """
        org = person.get("organization")
        if isinstance(org, dict):
            name = org.get("name")
            if name and str(name).strip():
                return str(name).strip()

        history = person.get("employment_history")
        if isinstance(history, list):
            # Prefer the current employer; fall back to the first listed so a
            # record that omits the ``current`` flag still yields a name.
            current = [
                e for e in history if isinstance(e, dict) and e.get("current")
            ]
            others = [
                e for e in history if isinstance(e, dict) and not e.get("current")
            ]
            for entry in current + others:
                name = entry.get("organization_name")
                if name and str(name).strip():
                    return str(name).strip()

        return None

    @classmethod
    def _firm_name_matches(cls, apollo_name: str | None, bd_name: str) -> bool:
        """True when Apollo's organization.name is plausibly the same firm
        as the broker-dealer we're enriching.

        Guards against cross-firm pollution: Apollo's /people/match can
        return a person with the same first+last name working at a
        completely different company (probe found a Morgan Stanley
        "Patricia Fletcher" matched to "Franciscan Friars of the
        Atonement"). Without this check, that stranger's email + LinkedIn
        would land on the BD's officer card as if they belonged to it.

        Strategy: tokenize both names (lowercased, org-suffix-stripped,
        stopwords removed). Match when at least 50% of the SHORTER name's
        tokens are present in the longer name. This tolerates natural
        verbosity differences (FINRA "MORGAN STANLEY" vs Apollo
        "Morgan Stanley & Co.") while rejecting unrelated firms. Returns
        False when either side has no usable tokens — we'd rather drop a
        possibly-correct match than write a wrong one onto a real BD.
        """
        bd_tokens = cls._firm_tokens(bd_name)
        apollo_tokens = cls._firm_tokens(apollo_name or "")
        if not bd_tokens or not apollo_tokens:
            return False
        shared = bd_tokens & apollo_tokens
        smaller = min(len(bd_tokens), len(apollo_tokens))
        return len(shared) / smaller >= 0.5

    @staticmethod
    def _website_domain(website: str | None) -> str | None:
        """Strip ``https://``, trailing slash, and path off a BD website to
        get the bare domain Apollo expects. Returns None for empty inputs."""
        if not website:
            return None
        cleaned = website.strip().lower()
        cleaned = re.sub(r"^https?://", "", cleaned)
        cleaned = cleaned.split("/", 1)[0]
        cleaned = cleaned.rstrip(".")
        return cleaned or None

    @staticmethod
    def _first_phone_value(phone_obj: object) -> str | None:
        """Pull the first usable phone number out of Apollo's phone_numbers
        list. Handles both dict shape ({sanitized_number, raw_number}) and
        bare strings."""
        if not isinstance(phone_obj, list) or not phone_obj:
            return None
        first = phone_obj[0]
        if isinstance(first, dict):
            value = first.get("sanitized_number") or first.get("raw_number")
            return str(value).strip() if value else None
        if isinstance(first, str):
            return first.strip() or None
        return None

    def _build_contacts_from_org_payload(
        self,
        broker_dealer: BrokerDealer,
        org_people: list[dict],
    ) -> list[ExecutiveContact]:
        """Convert the synthetic org-level dict from _enrich_via_org_lookup
        into ExecutiveContact rows. Mirrors the per-person construction
        above so both paths produce identical row shapes (source='apollo',
        title='Company (Organization Profile)' for the synthetic case)."""
        if not org_people:
            return []
        now = datetime.now(timezone.utc)
        contacts: list[ExecutiveContact] = []
        for person in org_people:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            title = str(person.get("title") or "Executive").strip()
            email = person.get("email")
            phone = self._first_phone_value(person.get("phone_numbers"))
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
        return contacts

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

    async def find_phone_for_contact(
        self, db: AsyncSession, contact_id: int
    ) -> ExecutiveContact:
        """Per-person phone lookup. Tries PDL first, falls back to Apollo.

        Used by the "Find phone" button on the broker-dealer detail page.
        PDL is the primary path because PR #419's audit showed Apollo
        ``/people/match`` returns ``phone_numbers=[]`` for 0/105 historical
        rows on the current plan. PDL returns multi-value phones (mobile +
        work) which land in the ``phones`` JSONB column; the scalar
        ``phone`` gets PDL's best single number (mobile if present).

        Apollo runs as a fallback when PDL has no match, no key, or a hard
        error -- preserving existing behaviour for emails PDL doesn't know.
        Apollo's transient errors (5xx / network) still raise
        ``ApolloLookupError`` so the FE shows a 502; PDL errors are silenced
        and fall through (graceful degradation -- a flaky upstream shouldn't
        break the per-row button).

        Never regresses an existing non-null phone. Never overwrites
        name / title / company / linkedin (which may come from higher-trust
        sources like FOCUS PDF).
        """
        contact = await db.get(ExecutiveContact, contact_id)
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
            except Exception as exc:  # noqa: BLE001 -- treat any PDL error as miss + fall through
                logger.warning("PDL enrich_by_email failed for %s: %s", contact.email, exc)

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
            # enriched_at so the UI shows "tried, nothing found" instead of
            # a stale row.
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
        phone = first_apollo_phone(person.get("phone_numbers")) if isinstance(person, dict) else None

        # Don't regress an existing phone if Apollo had nothing for us.
        if phone:
            contact.phone = phone
        contact.enriched_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(contact)
        return contact

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
