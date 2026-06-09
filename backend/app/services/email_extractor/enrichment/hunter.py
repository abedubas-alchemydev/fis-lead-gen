"""Hunter.io Email Enrichment provider (``GET /v2/people/find?email=``).

Second link in the default chain. Hunter's Enrichment API is the reverse-email
analog to Apollo's /people/match: hand it an email, get the person back. The
response wraps the record under ``data`` (and, on the combined endpoint, under
``data.person`` + ``data.company``); we tolerate both shapes:

    data.name.fullName / .givenName / .familyName
    data.employment.title / .name (company) / .seniority
    data.linkedin.handle           -> normalised to a profile URL
    data.phone
    data.email                     -> only kept when it differs from the input

Hunter answers ``404`` when it has no record for the email (a clean miss). Any
other non-200 (429 / 5xx) is raised as an :class:`EnrichmentError` so the
orchestrator treats it as a retryable provider error rather than an honest
"no match". Requires ``hunter_api_key``; without it the provider is skipped.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.email_extractor.enrichment.base import (
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
    clean_str,
)

logger = logging.getLogger(__name__)

PEOPLE_FIND_URL = "https://api.hunter.io/v2/people/find"
REQUEST_TIMEOUT_SECONDS = 20.0


class HunterEnrichProvider(EmailEnrichmentProvider):
    name = "hunter"

    def is_configured(self) -> bool:
        return bool(settings.hunter_api_key)

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        # One call returns the whole person record; ``needed`` is irrelevant.
        api_key = settings.hunter_api_key
        if not api_key:
            return None

        params = {"email": email, "api_key": api_key}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(PEOPLE_FIND_URL, params=params)
        except httpx.HTTPError as exc:
            raise EnrichmentError(f"network: {exc.__class__.__name__}: {exc}") from exc

        # Hunter returns 404 when it simply has no record for this email.
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            snippet = response.text[:200] if response.text else "(no body)"
            raise EnrichmentError(f"http {response.status_code}: {snippet}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise EnrichmentError(f"invalid json: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        # Combined endpoint nests the person under data.person; the people/find
        # endpoint returns the person fields directly on data.
        person = data.get("person") if isinstance(data.get("person"), dict) else data

        candidate_email = clean_str(person.get("email"))
        # Only contribute an email when it's genuinely different from the
        # discovered address -- echoing the input back adds nothing and would
        # block a later provider from filling a better (personal) one.
        new_email = (
            candidate_email
            if candidate_email and candidate_email.lower() != email.lower()
            else None
        )

        result = EnrichmentData(
            name=_name(person),
            title=_title(person),
            company=_company(person, data),
            linkedin_url=_linkedin_url(person),
            email=new_email,
            phone=clean_str(person.get("phone")) or clean_str(data.get("phone")),
        )
        return result if result.has_any() else None


def _name(person: dict[str, Any]) -> str | None:
    raw = person.get("name")
    if isinstance(raw, dict):
        full = clean_str(raw.get("fullName"))
        if full:
            return full
        given, family = clean_str(raw.get("givenName")), clean_str(raw.get("familyName"))
        if given and family:
            return f"{given} {family}"
        return given or family
    return clean_str(raw)


def _title(person: dict[str, Any]) -> str | None:
    employment = person.get("employment")
    if isinstance(employment, dict):
        title = clean_str(employment.get("title"))
        if title:
            return title
    return clean_str(person.get("position")) or clean_str(person.get("title"))


def _company(person: dict[str, Any], data: dict[str, Any]) -> str | None:
    employment = person.get("employment")
    if isinstance(employment, dict):
        company = clean_str(employment.get("name"))
        if company:
            return company
    company_obj = data.get("company")
    if isinstance(company_obj, dict):
        return clean_str(company_obj.get("name"))
    return None


def _linkedin_url(person: dict[str, Any]) -> str | None:
    """Normalise Hunter's ``linkedin.handle`` (e.g. ``"in/jane-doe"``) into a
    full profile URL. Tolerates a dict, a bare handle, or an already-full URL."""
    linkedin = person.get("linkedin")
    handle: str | None = None
    if isinstance(linkedin, dict):
        handle = clean_str(linkedin.get("handle"))
    elif isinstance(linkedin, str):
        handle = clean_str(linkedin)
    handle = handle or clean_str(person.get("linkedin_url")) or clean_str(person.get("linkedin_handle"))
    if not handle:
        return None
    if handle.startswith("http://") or handle.startswith("https://"):
        return handle
    trimmed = handle.lstrip("/")
    if trimmed.startswith(("in/", "pub/", "company/")):
        return f"https://www.linkedin.com/{trimmed}"
    return f"https://www.linkedin.com/in/{trimmed}"
