"""Apollo people-match helper for Form 4 reporting persons.

Lightweight, single-purpose wrapper. The existing
``ExecutiveContactService`` is tied to ``broker_dealer`` + persists
to ``executive_contacts`` rows; Form 4 reporting persons are not
broker-dealer employees and don't fit that shape, so the Investors
tab gets its own helper that returns ``(phone, email, matched)``
and lets the caller persist to ``form4_transactions`` directly.

Match strategy: a single Apollo ``/people/match`` POST with the
insider's name plus the issuer's company name. Reporting persons are
named with the company they file under, so the company name acts as
a disambiguator for common first/last name pairs. Apollo's free
``/people/match`` tier is plan-dependent; missing key or 403 returns
``matched=False`` without raising.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

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
    convention. We swap the order to first-last for Apollo and drop
    trailing single-letter middle initials. Best-effort — Apollo's
    match endpoint also accepts the full name, so a degraded split
    still returns useful results.
    """
    cleaned = (full_name or "").strip()
    if not cleaned:
        return None, None
    tokens = [t for t in cleaned.split() if t]
    if len(tokens) == 1:
        return tokens[0].title(), None
    # SEC convention is ``LAST FIRST [MIDDLE]``; surface that order
    # to Apollo as ``First Last`` so it matches LinkedIn-style records.
    last = tokens[0].title()
    first = tokens[1].title()
    return first, last


async def match_form4_person(
    *,
    full_name: str,
    issuer_name: str,
) -> Form4ApolloMatch:
    """Resolve an insider's contact details via Apollo ``/people/match``.

    Returns ``Form4ApolloMatch(matched=False, phone=None, email=None)``
    on missing key, transport failure, or no-match. The caller is
    expected to persist ``enriched_at`` regardless so the FE doesn't
    re-trigger on every render.
    """
    if not settings.apollo_api_key:
        logger.info("Form4 enrichment skipped: APOLLO_API_KEY not set.")
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    first_name, last_name = _split_name(full_name)
    if first_name is None and last_name is None:
        return Form4ApolloMatch(phone=None, email=None, matched=False)

    payload: dict[str, str] = {"organization_name": issuer_name}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    # Also send the raw concatenated name — Apollo accepts ``name`` and
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
