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
from app.services.contact_discovery._shared import first_apollo_phone
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


def _is_same_person(firm_record: dict[str, Any], primary_record: dict[str, Any]) -> bool:
    """True if the director-fallback ``primary_record`` is the same human as
    the firm-anchored ``firm_record``.

    The definitive signal is Apollo's own person ``id`` — Apollo returns the
    same id for a person regardless of which firm we anchored the query to,
    so an id match means "same person, different firm projection" (exactly
    the director case we're trying to resolve). When the firm record has no
    id we conservatively fall back to an exact first+last name match so a
    missing id can't silently disable the guard — but that's weaker and only
    catches the common case, not name collisions, so id match is preferred.
    """
    fid = firm_record.get("id")
    pid = primary_record.get("id")
    if isinstance(fid, (str, int)) and isinstance(pid, (str, int)):
        return str(fid).strip() == str(pid).strip()
    # No id to compare — require an exact (case-insensitive) first+last match.
    def _name(rec: dict[str, Any]) -> tuple[str, str]:
        return (
            str(rec.get("first_name") or "").strip().lower(),
            str(rec.get("last_name") or "").strip().lower(),
        )
    fn, pn = _name(firm_record), _name(primary_record)
    return fn == pn and all(fn)


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

        # Async phone-reveal opt-in. Apollo's sync response still returns
        # email/linkedin immediately; phone_numbers arrive minutes later via
        # POST to webhook_url. Both settings must be configured — when either
        # is missing the feature stays dormant and we keep the current
        # behaviour (no reveal request, no callback to fail on).
        webhook_secret = settings.apollo_webhook_secret
        base_url = settings.public_base_url
        reveal_phones = bool(webhook_secret and base_url)
        if reveal_phones:
            payload["reveal_phone_number"] = True
            payload["webhook_url"] = (
                f"{base_url.rstrip('/')}/api/v1/webhooks/apollo/"
                f"{webhook_secret}/phone-reveal"
            )

        person = await self._post_match(api_key, payload, first_name, last_name)
        if person is None:
            return None

        email = person.get("email")
        email_status = str(person.get("email_status") or "").strip().lower()
        if email:
            confidence = _EMAIL_STATUS_CONFIDENCE.get(email_status, 30.0)
        else:
            confidence = 0.0

        phone = first_apollo_phone(person.get("phone_numbers"))
        linkedin_url = person.get("linkedin_url") or None
        # MongoDB-style id from the sync response. Trimmed to the column's
        # String(64) cap so a future Apollo format change can't blow up the
        # INSERT — we'd rather store a truncated id than fail the enrich.
        person_id_raw = person.get("id")
        apollo_person_id = (
            str(person_id_raw).strip()[:64]
            if isinstance(person_id_raw, (str, int))
            else None
        )

        # Director LinkedIn fall-through. A confident match that came back
        # with no LinkedIn is the signature of an outside director: Apollo
        # recognised them at the queried firm (via a guessed email) but
        # returned the firm-projected record, whose linkedin_url is empty
        # because the URL lives on their PRIMARY-employer record. Re-query
        # with name only (no org/domain anchor) to surface that record's
        # LinkedIn. Accept it only when Apollo's own person id matches, so a
        # same-name stranger can't graft their URL onto this row.
        if (
            settings.apollo_director_linkedin_fallback
            and confidence >= float(settings.contact_discovery_min_confidence)
            and not linkedin_url
        ):
            fallback = await self._post_match(
                api_key,
                {"first_name": first_name, "last_name": last_name},
                first_name,
                last_name,
            )
            fb_linkedin = (
                (fallback.get("linkedin_url") or None) if isinstance(fallback, dict) else None
            )
            if fb_linkedin and _is_same_person(person, fallback):
                linkedin_url = str(fb_linkedin).strip()
                logger.info(
                    "Apollo director fallback recovered LinkedIn for %s %s",
                    first_name,
                    last_name,
                )

        return DiscoveryResult(
            email=str(email).strip() if email else None,
            phone=phone,
            linkedin_url=str(linkedin_url).strip() if linkedin_url else None,
            confidence=confidence,
            provider=self.name,
            raw=person,
            apollo_person_id=apollo_person_id,
        )

    async def _post_match(
        self,
        api_key: str,
        payload: dict[str, Any],
        first_name: str,
        last_name: str,
    ) -> dict[str, Any] | None:
        """POST to /people/match and return the parsed ``person`` dict, or
        ``None`` on any non-200 / transport / parse error / missing person.
        Shared by the firm-anchored query and the director fallback so both
        paths have identical failure semantics."""
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
        return person if isinstance(person, dict) else None

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


