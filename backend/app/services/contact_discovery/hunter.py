"""Hunter.io provider: /v2/email-finder for a person, /v2/domain-search for a company.

Person flow (``find_person``)
  ``GET /v2/email-finder?domain=&first_name=&last_name=&api_key=`` returns a
  single person's email and a 0..100 ``score``. We pass ``score`` through
  unchanged as confidence because Hunter's score already means what we want
  it to mean (higher = more likely to be the real address).

Organisation flow (``find_org``)
  ``GET /v2/domain-search?domain=&api_key=`` returns a list of public
  mailboxes on the domain (``info@``, ``contact@``, ``sales@``...). We pick
  the first generic-looking one -- a ``type == "generic"`` entry if present,
  otherwise the first entry -- and pass its ``score`` through as confidence.
  Org-level hits aren't as strong as a named-person match, but they're often
  the only thing available for tiny broker-dealers, so surfacing the best
  public inbox is worth it.

Both endpoints require ``hunter_api_key``; without one the provider silently
returns ``None`` so the orchestrator can skip to the next provider without
spurious warnings.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)

logger = logging.getLogger(__name__)


EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"


class HunterProvider(ContactDiscoveryProvider):
    name = "hunter"

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        api_key = settings.hunter_api_key
        if not api_key or not domain:
            # Hunter's /email-finder requires a domain -- without it the
            # endpoint can't do anything useful. org_name alone isn't enough.
            return None

        params = {
            "domain": domain,
            "first_name": first_name,
            "last_name": last_name,
            "api_key": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
                response = await client.get(EMAIL_FINDER_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning("Hunter email-finder failed for %s %s: %s", first_name, last_name, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        email = data.get("email")
        if not email:
            return None

        confidence = _coerce_score(data.get("score"))
        phone = data.get("phone_number") or None
        linkedin_url = data.get("linkedin_url") or None

        return DiscoveryResult(
            email=str(email).strip(),
            phone=str(phone).strip() if phone else None,
            linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
            confidence=confidence,
            provider=self.name,
            raw=data,
        )

    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        api_key = settings.hunter_api_key
        if not api_key or not domain:
            return None

        params = {"domain": domain, "api_key": api_key, "limit": 10}
        try:
            async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
                response = await client.get(DOMAIN_SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning("Hunter domain-search failed for %s: %s", domain, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        emails = data.get("emails")
        if not isinstance(emails, list) or not emails:
            return None

        pick = _pick_public_inbox(emails)
        if pick is None:
            return None

        email = pick.get("value")
        if not email:
            return None

        confidence = _coerce_score(pick.get("confidence"))
        linkedin_url = data.get("linkedin") or None
        phone = data.get("phone_number") or None

        return DiscoveryResult(
            email=str(email).strip(),
            phone=str(phone).strip() if phone else None,
            linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
            confidence=confidence,
            provider="hunter_domain",
            raw={"picked": pick, "data": data},
        )


def _coerce_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _pick_public_inbox(emails: list[Any]) -> dict[str, Any] | None:
    """Return the best generic inbox candidate, falling back to the first entry.

    Hunter tags each email with ``type`` (``generic`` / ``personal``). We
    prefer ``generic`` because a public inbox is the right thing to surface
    for an org-level fallback. If Hunter didn't classify any as generic we
    take the first entry so the caller at least gets *something*.
    """
    generic: dict[str, Any] | None = None
    first: dict[str, Any] | None = None
    for entry in emails:
        if not isinstance(entry, dict):
            continue
        if first is None:
            first = entry
        if str(entry.get("type") or "").lower() == "generic":
            generic = entry
            break
    return generic or first
