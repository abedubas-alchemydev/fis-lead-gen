"""Apollo provider: /people/match for a single person, /organizations/enrich for a company.

Apollo's public ``POST /v1/people/match`` endpoint returns a single person
object when it thinks it has a good match. We read its ``email_status``
field and translate it into our 0..100 confidence scale so the
orchestrator can compare scores across providers apples-to-apples:

=====================  ====
Apollo ``email_status``  conf
=====================  ====
``verified``            90
``likely_to_engage``    75
``unverified``          60
``guessed``             45
(other / missing)       30
(no email at all)        0
=====================  ====

The organisation path hits ``POST /api/v1/organizations/enrich`` with
``{"domain": ...}`` and returns an org-profile-shaped "contact" (no email
but often with a primary phone and LinkedIn URL). Confidence is a fixed
55 for org matches -- they're useful breadcrumbs but never as strong as a
person-level hit.

Failure semantics: every exception, timeout, and non-200 response yields
``None`` from the provider. The orchestrator treats that as "no match,
move on." We never raise into the caller -- one flaky provider can't
block the chain.
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


PEOPLE_MATCH_URL = "https://api.apollo.io/v1/people/match"
ORG_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"


_EMAIL_STATUS_CONFIDENCE: dict[str, float] = {
    "verified": 90.0,
    "likely_to_engage": 75.0,
    "unverified": 60.0,
    "guessed": 45.0,
}


class ApolloMatchProvider(ContactDiscoveryProvider):
    name = "apollo_match"

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        api_key = settings.apollo_api_key
        if not api_key:
            return None

        payload: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "organization_name": org_name,
        }
        if domain:
            payload["domain"] = domain

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
                response = await client.post(PEOPLE_MATCH_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("Apollo match request failed for %s %s: %s", first_name, last_name, exc)
            return None

        if response.status_code != 200:
            logger.info(
                "Apollo match returned %d for %s %s",
                response.status_code,
                first_name,
                last_name,
            )
            return None

        try:
            body = response.json()
        except ValueError:
            return None

        person = body.get("person") if isinstance(body, dict) else None
        if not isinstance(person, dict):
            return None

        email = person.get("email")
        email_status = str(person.get("email_status") or "").strip().lower()
        if email:
            confidence = _EMAIL_STATUS_CONFIDENCE.get(email_status, 30.0)
        else:
            confidence = 0.0

        phone = _first_phone(person.get("phone_numbers"))
        linkedin_url = person.get("linkedin_url") or None

        return DiscoveryResult(
            email=str(email).strip() if email else None,
            phone=phone,
            linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
            confidence=confidence,
            provider=self.name,
            raw=person,
        )

    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        api_key = settings.apollo_api_key
        if not api_key or not domain:
            return None

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
                response = await client.post(
                    ORG_ENRICH_URL,
                    headers=headers,
                    json={"domain": domain},
                )
        except httpx.HTTPError as exc:
            logger.warning("Apollo org enrich failed for domain %s: %s", domain, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            body = response.json()
        except ValueError:
            return None

        org = body.get("organization") if isinstance(body, dict) else None
        if not isinstance(org, dict):
            return None

        primary_phone = org.get("primary_phone")
        phone: str | None = None
        if isinstance(primary_phone, dict):
            phone_value = primary_phone.get("sanitized_number") or primary_phone.get("number")
            phone = str(phone_value).strip() if phone_value else None

        linkedin_url = org.get("linkedin_url")

        return DiscoveryResult(
            email=None,
            phone=phone,
            linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
            # Org-profile hits are corroborating evidence, not person-level
            # precision. 55 clears the default threshold (60) only when the
            # user has lowered CONTACT_DISCOVERY_MIN_CONFIDENCE, which makes
            # sense: nobody wants to trust "the company's 800-number" as a
            # CEO contact by default.
            confidence=55.0,
            provider="apollo_org",
            raw=org,
        )


def _first_phone(phone_numbers: Any) -> str | None:
    if not isinstance(phone_numbers, list) or not phone_numbers:
        return None
    first = phone_numbers[0]
    if isinstance(first, dict):
        value = first.get("sanitized_number") or first.get("raw_number")
        if value:
            return str(value).strip() or None
    if isinstance(first, str):
        return first.strip() or None
    return None
