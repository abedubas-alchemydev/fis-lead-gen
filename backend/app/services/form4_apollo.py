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
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.contact_discovery.pdl import PdlProvider

logger = logging.getLogger(__name__)

_APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
_REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class Form4ApolloMatch:
    phone: str | None
    email: str | None
    matched: bool


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

    PDL primary, Apollo fallback. Returns
    ``Form4ApolloMatch(matched=False, phone=None, email=None)`` on
    missing keys, transport failure, or no-match across both providers.
    The caller is expected to persist ``enriched_at`` regardless so the
    FE doesn't re-trigger on every render.
    """
    first_name, last_name = _split_name(full_name)
    if first_name is None and last_name is None:
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    # ── PDL primary ──────────────────────────────────────────────
    # PDL's find_person requires both first + last (the org anchor
    # alone isn't enough to disambiguate); single-word names skip to
    # Apollo, which accepts a `name=` fallback.
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
            return Form4ApolloMatch(
                phone=pdl_hit.phone,
                email=pdl_hit.email,
                matched=bool(pdl_hit.email) or bool(pdl_hit.phone),
            )

    # ── Apollo fallback ──────────────────────────────────────────
    if not settings.apollo_api_key:
        logger.info("Form4 enrichment skipped: APOLLO_API_KEY not set.")
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    payload: dict[str, str] = {"organization_name": issuer_name}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    # Also send the raw concatenated name -- Apollo accepts ``name`` and
    # falls back to it if first/last didn't match a record.
    payload["name"] = full_name

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
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    if response.status_code != 200:
        logger.info(
            "Apollo people-match non-200 (%s) for %r: %s",
            response.status_code,
            full_name,
            response.text[:200],
        )
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    try:
        data = response.json()
    except ValueError:
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    person = data.get("person") if isinstance(data, dict) else None
    if not isinstance(person, dict):
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    email = person.get("email")
    phone_numbers = person.get("phone_numbers")
    phone: str | None = None
    if isinstance(phone_numbers, list):
        for entry in phone_numbers:
            if isinstance(entry, dict):
                value = entry.get("sanitized_number") or entry.get("raw_number")
                if isinstance(value, str) and value.strip():
                    phone = value.strip()
                    break
            elif isinstance(entry, str) and entry.strip():
                phone = entry.strip()
                break

    matched = bool(email) or bool(phone)
    return Form4ApolloMatch(
        phone=phone,
        email=email if isinstance(email, str) and email.strip() else None,
        matched=matched,
    )
