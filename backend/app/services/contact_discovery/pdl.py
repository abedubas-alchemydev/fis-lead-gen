"""People Data Labs provider: /v5/person/enrich.

PDL's ``POST /v5/person/enrich`` endpoint returns a person object whose
``likelihood`` (1..10) maps linearly to our 0..100 confidence scale
(``confidence = likelihood * 10``). The primary win over Apollo / Hunter
/ Snov is multi-value coverage: a single hit can return both work and
personal emails plus mobile and work phones. PR #419's audit showed
Apollo's /people/match returns ``phone_numbers=[]`` for 0/105 historical
rows on the current plan, so PDL is the first paid API in this chain
that actually returns person phones via API.

Person flow (``find_person``)
  Match anchor: ``domain`` if available, else ``org_name`` (PDL accepts
  either as the ``company`` parameter; domain yields markedly higher hit
  rates per PDL docs). Server-side ``min_likelihood`` filter
  (``settings.pdl_min_likelihood``, default 6) means PDL only bills for
  matches that clear our 60.0 confidence threshold.

Organisation flow (``find_org``)
  PDL is person-only in this chain; ``find_org`` returns ``None`` so the
  orchestrator falls through to Apollo's ``find_org`` / Hunter's
  ``find_org`` for org-level hits.

Email re-anchor helper (``enrich_by_email``)
  Exposed as a module function so ``contacts.py::find_phone_for_contact``
  can re-key the same PDL endpoint with ``{"email": "..."}`` for the
  per-row "Find Phone" button. PDL returns the same payload shape whether
  the input was a name+company anchor or an email anchor.

Failure semantics: every exception / timeout / non-200 yields ``None``
(404 is PDL's documented "no confident match" and is silenced; other
non-2xx codes log info / warning as appropriate).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)

logger = logging.getLogger(__name__)


PERSON_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"


# PDL email types that map to our canonical "work" bucket. Anything else
# (notably "personal") collapses to "personal".
_WORK_EMAIL_TYPES = {"professional", "current_professional", "work", "school"}


class PdlProvider(ContactDiscoveryProvider):
    name = "pdl"

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        api_key = settings.pdl_api_key
        if not api_key:
            return None
        # Domain is PDL's strongest match anchor; org_name is the fallback.
        company = domain or org_name
        if not company:
            return None
        payload: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "min_likelihood": settings.pdl_min_likelihood,
        }
        return await _request_and_parse(
            payload, api_key, log_context=f"{first_name} {last_name}"
        )

    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        # PDL is person-only in this chain; the org-level path is left to
        # Apollo's organisations/enrich and Hunter's domain-search, which
        # already surface domain inboxes + company primary_phone. Returning
        # ``None`` here is a transparent fall-through.
        return None


async def enrich_by_email(email: str) -> DiscoveryResult | None:
    """Re-anchor PDL's person enrich on an email address.

    Used by ``contacts.py::find_phone_for_contact`` so the per-row
    "Find phone" button can pull a multi-value PDL hit for a contact whose
    email we already know. Returns ``None`` on missing key, network failure,
    non-200 (except 404 which is a clean "no match"), or empty payload.
    """
    api_key = settings.pdl_api_key
    if not api_key or not email:
        return None
    payload: dict[str, Any] = {
        "email": email,
        "min_likelihood": settings.pdl_min_likelihood,
    }
    return await _request_and_parse(payload, api_key, log_context=email)


async def _request_and_parse(
    payload: dict[str, Any],
    api_key: str,
    *,
    log_context: str,
) -> DiscoveryResult | None:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
            response = await client.post(PERSON_ENRICH_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("PDL person enrich failed for %s: %s", log_context, exc)
        return None

    if response.status_code == 404:
        # PDL's documented "no confident match" code; not an error.
        return None
    if response.status_code != 200:
        logger.info(
            "PDL person enrich returned %d for %s", response.status_code, log_context
        )
        return None

    try:
        body = response.json()
    except ValueError:
        return None

    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    likelihood = body.get("likelihood")
    if not isinstance(likelihood, int):
        return None
    confidence = float(likelihood * 10)

    emails = _build_email_hits(data, confidence)
    phones = _build_phone_hits(data, confidence)

    linkedin_url = data.get("linkedin_url")
    if linkedin_url and isinstance(linkedin_url, str) and not linkedin_url.startswith("http"):
        linkedin_url = f"https://{linkedin_url}"

    return DiscoveryResult(
        email=_best_email_scalar(emails),
        phone=_best_phone_scalar(phones),
        linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
        confidence=confidence,
        provider="pdl",
        raw=data,
        emails=emails,
        phones=phones,
    )


def _build_email_hits(data: dict[str, Any], confidence: float) -> list[EmailHit]:
    """Parse PDL's emails[] + personal_emails + work_email into a deduped list."""
    out: list[EmailHit] = []
    seen: set[str] = set()
    raw_emails = data.get("emails")
    if isinstance(raw_emails, list):
        for entry in raw_emails:
            if not isinstance(entry, dict):
                continue
            addr = str(entry.get("address") or "").strip().lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            pdl_type = str(entry.get("type") or "").strip().lower()
            our_type: Any = "work" if pdl_type in _WORK_EMAIL_TYPES else "personal"
            out.append(EmailHit(value=addr, type=our_type, confidence=confidence, source="pdl"))
    personal_emails = data.get("personal_emails")
    if isinstance(personal_emails, list):
        for raw in personal_emails:
            if not isinstance(raw, str):
                continue
            addr = raw.strip().lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            out.append(EmailHit(value=addr, type="personal", confidence=confidence, source="pdl"))
    work_email = data.get("work_email")
    if isinstance(work_email, str):
        addr = work_email.strip().lower()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(EmailHit(value=addr, type="work", confidence=confidence, source="pdl"))
    return out


def _build_phone_hits(data: dict[str, Any], confidence: float) -> list[PhoneHit]:
    """Parse mobile_phone + phone_numbers[] into a deduped list.

    ``mobile_phone`` is the most useful single number for outreach so it
    leads as type=mobile; the rest of ``phone_numbers[]`` fall in as
    type=work (PDL doesn't tag phones, and work direct dial is the
    most useful default classification).
    """
    out: list[PhoneHit] = []
    seen: set[str] = set()
    mobile = data.get("mobile_phone")
    if isinstance(mobile, str):
        norm = mobile.strip()
        if norm:
            seen.add(norm)
            out.append(PhoneHit(value=norm, type="mobile", confidence=confidence, source="pdl"))
    phone_numbers = data.get("phone_numbers")
    if isinstance(phone_numbers, list):
        for raw in phone_numbers:
            if not isinstance(raw, str):
                continue
            norm = raw.strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(PhoneHit(value=norm, type="work", confidence=confidence, source="pdl"))
    return out


def _best_email_scalar(emails: list[EmailHit]) -> str | None:
    for hit in emails:
        if hit.type == "work":
            return hit.value
    return emails[0].value if emails else None


def _best_phone_scalar(phones: list[PhoneHit]) -> str | None:
    for hit in phones:
        if hit.type == "mobile":
            return hit.value
    return phones[0].value if phones else None
