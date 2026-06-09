"""People Data Labs ``/v5/person/enrich`` reverse-email enrichment provider.

Slotted right after Apollo in the default chain because PDL is the one paid API
in our stack that actually returns person **phones** -- PR #419's audit showed
Apollo's ``/people/match`` came back with ``phone_numbers=[]`` for 0/105
historical rows on our plan, and Snov's profile endpoint returns no phone at
all. PDL also returns multiple emails (work + personal), so it's the strongest
single source for the two fields the "Enrich" button most often still misses
after Apollo: a phone and a genuine *personal* email.

Hand it the discovered email; it returns the person record (``data``):

    full_name / first_name / last_name      -> name (title-cased; PDL lowercases)
    job_title                               -> title
    job_company_name                        -> company
    linkedin_url                            -> linkedin_url (scheme prepended)
    personal_emails[] / emails[{address,type}] / work_email
                                            -> a personal (else differing) email
    mobile_phone (else phone_numbers[0])    -> phone

Server-side ``min_likelihood`` (``settings.pdl_min_likelihood``) means PDL only
bills for matches that already clear our confidence floor, so a returned ``data``
object is a confident match. ``404`` is PDL's documented "no confident match" ->
a clean miss; any other non-2xx (429 / 5xx) / network / payload failure raises
:class:`EnrichmentError` so the orchestrator treats it as a retryable provider
error rather than an honest "no match". Requires ``pdl_api_key``; without it the
provider is skipped.

Kept self-contained (its own HTTP + parse) like the Snov enrichment provider so
the enrichment package doesn't reach into ``contact_discovery``.
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

PERSON_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"
REQUEST_TIMEOUT_SECONDS = 20.0


class PdlEnrichProvider(EmailEnrichmentProvider):
    name = "pdl"

    def is_configured(self) -> bool:
        return bool(settings.pdl_api_key)

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        # One call returns the whole person record; ``needed`` is irrelevant.
        api_key = settings.pdl_api_key
        if not api_key:
            return None

        # ``min_likelihood`` is a server-side filter: PDL only bills for (and
        # returns) matches that already clear our confidence floor.
        payload = {"email": email, "min_likelihood": settings.pdl_min_likelihood}
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(PERSON_ENRICH_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EnrichmentError(f"network: {exc.__class__.__name__}: {exc}") from exc

        # 404 is PDL's documented "no confident match" -> a clean miss.
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            snippet = response.text[:200] if response.text else "(no body)"
            raise EnrichmentError(f"http {response.status_code}: {snippet}")

        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise EnrichmentError(f"invalid json: {exc}") from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None

        result = EnrichmentData(
            name=_name(data),
            title=clean_str(data.get("job_title")),
            company=clean_str(data.get("job_company_name")),
            linkedin_url=_linkedin(data),
            email=_alt_email(data, email),
            phone=_phone(data),
        )
        return result if result.has_any() else None


def _name(data: dict[str, Any]) -> str | None:
    """Person's display name. PDL returns names lowercased (``"jane doe"``), so
    title-case a fully-lowercase value; leave an already-cased name untouched."""
    full = clean_str(data.get("full_name"))
    if not full:
        first, last = clean_str(data.get("first_name")), clean_str(data.get("last_name"))
        full = f"{first} {last}" if first and last else (first or last)
    if not full:
        return None
    return full.title() if full == full.lower() else full


def _linkedin(data: dict[str, Any]) -> str | None:
    """PDL hands back a bare ``linkedin.com/in/...`` (no scheme); normalise it."""
    url = clean_str(data.get("linkedin_url"))
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return f"https://{url}"


def _phone(data: dict[str, Any]) -> str | None:
    """Best single number for outreach: mobile first, else the first work
    number. PDL doesn't tag ``phone_numbers[]``, so order is its own."""
    mobile = clean_str(data.get("mobile_phone"))
    if mobile:
        return mobile
    numbers = data.get("phone_numbers")
    if isinstance(numbers, list):
        for raw in numbers:
            num = clean_str(raw)
            if num:
                return num
    return None


def _alt_email(data: dict[str, Any], discovered: str) -> str | None:
    """A genuine alternate email, personal preferred.

    The discovered address already keyed the lookup and is shown on the row, so
    echoing it back adds nothing (and would block a later provider from filling
    a better one). Collect every address that *differs* from the discovered one
    and return a personal-typed hit if any, else the first differing address.
    ``personal_emails[]`` (bare strings) are personal by construction; ``emails[]``
    entries carry an explicit ``type``.
    """
    discovered_l = discovered.lower()
    candidates: list[tuple[bool, str]] = []  # (is_personal, address)

    personal = data.get("personal_emails")
    if isinstance(personal, list):
        for raw in personal:
            addr = clean_str(raw)
            if addr and addr.lower() != discovered_l:
                candidates.append((True, addr))

    emails = data.get("emails")
    if isinstance(emails, list):
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            addr = clean_str(entry.get("address"))
            if addr and addr.lower() != discovered_l:
                is_personal = str(entry.get("type") or "").strip().lower() == "personal"
                candidates.append((is_personal, addr))

    work_email = clean_str(data.get("work_email"))
    if work_email and work_email.lower() != discovered_l:
        candidates.append((False, work_email))

    if not candidates:
        return None
    for is_personal, addr in candidates:
        if is_personal:
            return addr
    return candidates[0][1]
