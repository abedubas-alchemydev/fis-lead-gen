"""PDL + Apollo people-match helper for Form 4 reporting persons.

Two-stage match: PDL's ``/v5/person/enrich`` runs first (the only paid
API in this chain that actually returns person phones on the current
plan, per PR #419's audit), Apollo ``/people/match`` as the fallback.
PDL errors silently fall through to Apollo (graceful degradation -- a
flaky upstream shouldn't break the per-row enrich button). Returns
``matched=False`` only when neither provider had anything (or when
neither key is configured).

Lightweight, single-purpose wrapper. The existing
``ExecutiveContactService`` is tied to ``broker_dealer`` + persists to
``executive_contacts`` rows; Form 4 reporting persons are not
broker-dealer employees and don't fit that shape, so the Investors tab
gets its own helper that returns ``(phone, email, matched)`` and lets
the caller persist to ``form4_transactions`` directly.

Match anchor for both providers: insider name + issuer's company name.
For outside 10%-holder filings (e.g. Bill Gates filing under Republic
Services) the issuer isn't the insider's employer, so both providers
miss more often than executive-officer filings (where the issuer IS
the employer). The PDL/Apollo fallback maximizes the combined hit rate
across both shapes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.contact_discovery.pdl import PdlProvider

logger = logging.getLogger(__name__)

_APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
_REQUEST_TIMEOUT_SECONDS = 10.0

# Reporting-owner names ending in any of these tokens are entities (funds,
# GPs, LPs, holdcos, trusts), not natural persons. Apollo's /people/match
# and PDL's /person/enrich are both person-only APIs and can never resolve
# an entity name, so we short-circuit before burning a credit. Lifted from
# the FINRA executive-owner skip list in ``services/contacts.py`` and
# extended with ``GP`` (common Form 4 suffix for fund general partners).
_ENTITY_NAME_TOKENS = re.compile(
    r"(?<!\w)(LLC|L\.L\.C\.|LLP|L\.L\.P\.|INC\.?|INCORPORATED|"
    r"CORP\.?|CORPORATION|L\.P\.|LP|GP|LTD\.?|LIMITED|HOLDINGS|"
    r"GROUP|MANAGEMENT|PARTNERS|PLC|TRUST|FUND|COMPANY|CO\.)(?!\w)",
    re.IGNORECASE,
)


def looks_like_entity(name: str) -> bool:
    """True if the reporting-owner name looks like an entity, not a person."""
    return bool(_ENTITY_NAME_TOKENS.search(name or ""))


@dataclass(slots=True)
class Form4ApolloMatch:
    phone: str | None
    email: str | None
    matched: bool
    # ``"entity_filer"`` when the row's name matches ``looks_like_entity``
    # and the upstream people-match was deliberately skipped. ``None`` for
    # all other paths (real hit, real miss, or transport failure). The FE
    # surfaces a distinct copy when set so the user understands the row
    # wasn't a failed lookup but a no-op by design.
    skip_reason: str | None = None
    # LinkedIn profile URL when either provider returned one. PDL prefixes
    # bare URLs with ``https://`` upstream; Apollo returns full URLs.
    # ``matched`` counts a LinkedIn-only hit so the FE doesn't show
    # "Apollo returned no match" when a usable handle was recovered.
    linkedin_url: str | None = None
    # Apollo's MongoDB-style person.id from the sync /people/match response.
    # Set only when the Apollo path ran (PDL had no phone) AND a record was
    # returned. The caller persists it so the async phone-reveal webhook can
    # find this insider's rows when the phone arrives minutes later.
    apollo_person_id: str | None = None


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    """Split ``"COOK TIMOTHY D"`` style names into (first, last).

    Form 4 names are uppercase ``LASTNAME FIRSTNAME [MIDDLE]`` by SEC
    convention. We swap the order to first-last for the providers and
    drop trailing single-letter middle initials. Best-effort -- both
    Apollo's and PDL's match endpoints also accept the full name as a
    fallback anchor.
    """
    cleaned = (full_name or "").strip()
    if not cleaned:
        return None, None
    tokens = [t for t in cleaned.split() if t]
    if len(tokens) == 1:
        return tokens[0].title(), None
    # SEC convention is ``LAST FIRST [MIDDLE]``; surface that order
    # to the providers as ``First Last`` so it matches LinkedIn-style
    # records.
    last = tokens[0].title()
    first = tokens[1].title()
    return first, last


async def match_form4_person(
    *,
    full_name: str,
    issuer_name: str,
) -> Form4ApolloMatch:
    """Resolve a Form 4 reporting person's contact details.

    PDL primary (synchronous email / LinkedIn / phone), then Apollo for the
    phone when PDL didn't supply one. PDL almost never returns phones for
    Form 4 insiders on the current plan, so the Apollo step requests an async
    **phone-reveal** (``reveal_phone_number`` + ``webhook_url``, same gate as
    the BD/IA chain): email + LinkedIn come back in the sync body, the phone
    is POSTed to our webhook minutes later. ``apollo_person_id`` is captured
    on that sync response so the webhook can find this insider's rows.

    Entity filers (LLC / LP / GP / Fund / ...) short-circuit before any
    network call with ``skip_reason="entity_filer"``. The caller persists
    ``enriched_at`` + ``apollo_person_id`` on real attempts so the FE doesn't
    re-trigger and the async reveal can land later.
    """
    if looks_like_entity(full_name):
        return Form4ApolloMatch(
            phone=None, email=None, matched=False, skip_reason="entity_filer"
        )

    first_name, last_name = _split_name(full_name)
    if first_name is None and last_name is None:
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    # ── PDL primary (sync email / LinkedIn / phone) ──────────────────
    # PDL's find_person requires both first + last (the org anchor alone
    # isn't enough to disambiguate); single-word names skip to Apollo.
    pdl_phone: str | None = None
    pdl_email: str | None = None
    pdl_linkedin: str | None = None
    if settings.pdl_api_key and first_name and last_name:
        try:
            pdl_hit = await PdlProvider().find_person(
                first_name=first_name,
                last_name=last_name,
                org_name=issuer_name,
                domain=None,
            )
        except Exception as exc:  # noqa: BLE001 -- treat any PDL error as miss
            logger.warning("PDL form4 match failed for %r: %s", full_name, exc)
            pdl_hit = None

        if pdl_hit is not None:
            pdl_phone = pdl_hit.phone
            pdl_email = pdl_hit.email
            pdl_linkedin = pdl_hit.linkedin_url
            # PDL already returned a phone -> done. Firing Apollo here would
            # only add cost (its /people/match returns no phone without a
            # billed reveal, and we already have the number).
            if pdl_phone:
                return Form4ApolloMatch(
                    phone=pdl_phone,
                    email=pdl_email,
                    linkedin_url=pdl_linkedin,
                    matched=True,
                )

    # ── Apollo (phone via async reveal; email / LinkedIn sync) ───────
    # Reached when PDL is unconfigured, missed, or matched WITHOUT a phone
    # (the common case for insiders). This is the path that lands a phone.
    def _carry_pdl() -> Form4ApolloMatch:
        """Return whatever PDL gave us (no phone) when Apollo can't run."""
        return Form4ApolloMatch(
            phone=None,
            email=pdl_email,
            linkedin_url=pdl_linkedin,
            matched=bool(pdl_email) or bool(pdl_linkedin),
        )

    if not settings.apollo_api_key:
        if not settings.pdl_api_key:
            logger.info("Form4 enrichment skipped: no PDL or Apollo key set.")
        return _carry_pdl()

    payload: dict[str, object] = {"organization_name": issuer_name}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    # Also send the raw concatenated name -- Apollo accepts ``name`` and
    # falls back to it if first/last didn't match a record.
    payload["name"] = full_name

    # Async phone-reveal opt-in -- same gate as contact_discovery/apollo_match.
    # Both must be configured; when set, Apollo POSTs phone_numbers to our
    # webhook minutes later (sync body still carries email + LinkedIn).
    webhook_secret = settings.apollo_webhook_secret
    base_url = settings.public_base_url
    if webhook_secret and base_url:
        payload["reveal_phone_number"] = True
        payload["webhook_url"] = (
            f"{base_url.rstrip('/')}/api/v1/webhooks/apollo/"
            f"{webhook_secret}/phone-reveal"
        )

    headers = {
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "X-Api-Key": settings.apollo_api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _APOLLO_MATCH_URL, json=payload, headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("Apollo people-match transport error: %s", exc)
        return _carry_pdl()

    if response.status_code != 200:
        logger.info(
            "Apollo people-match non-200 (%s) for %r: %s",
            response.status_code,
            full_name,
            response.text[:200],
        )
        return _carry_pdl()

    try:
        data = response.json()
    except ValueError:
        return _carry_pdl()

    person = data.get("person") if isinstance(data, dict) else None
    if not isinstance(person, dict):
        return _carry_pdl()

    apollo_email_raw = person.get("email")
    apollo_email = (
        apollo_email_raw.strip()
        if isinstance(apollo_email_raw, str) and apollo_email_raw.strip()
        else None
    )

    # Sync phone is usually empty when a reveal was requested (it arrives via
    # the webhook), but read it anyway -- a few records carry one inline, and
    # reveal isn't always enabled.
    phone_numbers = person.get("phone_numbers")
    apollo_phone: str | None = None
    if isinstance(phone_numbers, list):
        for entry in phone_numbers:
            if isinstance(entry, dict):
                value = entry.get("sanitized_number") or entry.get("raw_number")
                if isinstance(value, str) and value.strip():
                    apollo_phone = value.strip()
                    break
            elif isinstance(entry, str) and entry.strip():
                apollo_phone = entry.strip()
                break

    linkedin_raw = person.get("linkedin_url")
    apollo_linkedin = (
        linkedin_raw.strip()
        if isinstance(linkedin_raw, str) and linkedin_raw.strip()
        else None
    )

    person_id_raw = person.get("id")
    apollo_person_id = (
        str(person_id_raw).strip()[:64]
        if isinstance(person_id_raw, (str, int))
        else None
    )

    # Prefer PDL's email / LinkedIn (higher quality); fall back to Apollo's.
    # pdl_phone is None here by construction, so phone is Apollo's sync value
    # (usually None -> filled later by the reveal webhook).
    email = pdl_email or apollo_email
    linkedin_url = pdl_linkedin or apollo_linkedin
    phone = pdl_phone or apollo_phone

    matched = bool(email) or bool(phone) or bool(linkedin_url)
    return Form4ApolloMatch(
        phone=phone,
        email=email,
        linkedin_url=linkedin_url,
        matched=matched,
        apollo_person_id=apollo_person_id,
    )
