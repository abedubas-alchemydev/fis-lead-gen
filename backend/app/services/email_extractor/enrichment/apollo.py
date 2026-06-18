"""Apollo.io ``/people/match`` reverse-email enrichment provider.

First link in the default chain. Takes the discovered email and returns the
person record: name, title, LinkedIn, company, an alternate (often personal)
email, and -- when the async reveal flow is configured -- requests a phone
reveal whose number arrives later via the webhook (correlated back to the row
by ``apollo_person_id``).

This is the pure-provider extraction of the former Apollo-only
``apollo_enrichment.enrich_discovered_email``: no DB, no row writes. The
orchestrator owns persistence; the shared phone / reveal helpers live in
``contact_discovery._shared`` so this and the contact-discovery person-match
flow parse Apollo's payload identically.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings
from app.services.contact_discovery._shared import (
    apollo_phone_reveal_fields,
    first_apollo_phone,
)
from app.services.email_extractor.enrichment.base import (
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
    clean_str,
)

logger = logging.getLogger(__name__)

APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
REQUEST_TIMEOUT_SECONDS = 20.0

# ── Credit-exhaustion circuit breaker ──────────────────────────────────
# Apollo answers an out-of-credits account with HTTP 422 + an "insufficient
# credits" body on EVERY /people/match call. Without this, a bulk enrich-all
# fires one dead call per email (~50/min observed on prod, scan 84) at an
# account that can't recover mid-run. Once we see that response we open a
# per-process breaker so is_configured() reports False -- the orchestrator then
# skips Apollo for the cooldown window (the rest of the run + nearby runs on the
# same instance) instead of hammering it. After the window Apollo is retried, in
# case credits were topped up. Per-process (not distributed) is sufficient: a
# bulk run executes as one background task on one instance.
_CREDIT_EXHAUSTED_COOLDOWN = timedelta(minutes=30)
_credit_exhausted_until: datetime | None = None


def _apollo_credit_exhausted() -> bool:
    return _credit_exhausted_until is not None and datetime.now(UTC) < _credit_exhausted_until


def _trip_credit_breaker() -> None:
    global _credit_exhausted_until
    _credit_exhausted_until = datetime.now(UTC) + _CREDIT_EXHAUSTED_COOLDOWN
    logger.warning(
        "apollo credit-exhaustion breaker opened; skipping Apollo enrichment for %s",
        _CREDIT_EXHAUSTED_COOLDOWN,
    )


def _reset_credit_breaker_for_tests() -> None:
    global _credit_exhausted_until
    _credit_exhausted_until = None


class ApolloEnrichProvider(EmailEnrichmentProvider):
    name = "apollo"

    def is_configured(self) -> bool:
        # Skip Apollo while the credit-exhaustion breaker is open so a bulk run
        # doesn't fire one dead 422 call per row at a depleted account.
        return bool(settings.apollo_api_key) and not _apollo_credit_exhausted()

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        # One call returns the whole person record; ``needed`` is irrelevant.
        api_key = settings.apollo_api_key
        if not api_key:
            return None

        # reveal_personal_emails surfaces the person's email even when the
        # discovered address is a generic/role inbox (returned synchronously).
        # apollo_phone_reveal_fields() adds reveal_phone_number + webhook_url
        # when the async reveal flow is configured -- the number then lands via
        # the webhook, keyed back to the row by apollo_person_id.
        payload: dict[str, Any] = {"email": email, "reveal_personal_emails": True}
        payload.update(apollo_phone_reveal_fields())
        headers = {
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(APOLLO_MATCH_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EnrichmentError(f"network: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code != 200:
            snippet = response.text[:200] if response.text else "(no body)"
            # Apollo answers a credit-exhausted account with 422 + "insufficient
            # credits" on every call -- open the breaker so the rest of a bulk
            # run skips Apollo instead of firing one dead call per row.
            if response.status_code == 422 and "insufficient credit" in response.text.lower():
                _trip_credit_breaker()
            raise EnrichmentError(f"http {response.status_code}: {snippet}")

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise EnrichmentError(f"invalid json: {exc}") from exc

        person = data.get("person") if isinstance(data, dict) else None
        if not isinstance(person, dict):
            return None

        return EnrichmentData(
            name=clean_str(person.get("name")) or _compose_name(person),
            title=_first_string(person, ["title", "headline"]),
            company=_company(person),
            linkedin_url=clean_str(person.get("linkedin_url")),
            # A revealed personal email wins over the matched work email (which
            # is usually just the discovered address echoed back).
            email=_first_personal_email(person) or clean_str(person.get("email")),
            # Sync phone is usually empty -- the real number arrives via the
            # phone-reveal webhook, matched back by apollo_person_id.
            phone=first_apollo_phone(person.get("phone_numbers")),
            apollo_person_id=_person_id(person),
        )


def _first_string(obj: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = clean_str(obj.get(key))
        if value:
            return value
    return None


def _compose_name(person: dict[str, Any]) -> str | None:
    first = clean_str(person.get("first_name"))
    last = clean_str(person.get("last_name"))
    if first and last:
        return f"{first} {last}"
    return first or last


def _company(person: dict[str, Any]) -> str | None:
    organization = person.get("organization")
    if isinstance(organization, dict):
        return _first_string(organization, ["name", "display_name"])
    return None


def _person_id(person: dict[str, Any]) -> str | None:
    """Apollo's stable person id, stamped so the async phone-reveal webhook can
    correlate a revealed number back to the row."""
    value = person.get("id")
    if isinstance(value, (str, int)):
        return str(value).strip() or None
    return None


def _first_personal_email(person: dict[str, Any]) -> str | None:
    """First usable address from Apollo's ``personal_emails`` (populated when
    reveal_personal_emails is on). Tolerates a list of strings or of dicts."""
    emails = person.get("personal_emails")
    if not isinstance(emails, list):
        return None
    for entry in emails:
        if isinstance(entry, str) and entry.strip():
            return entry.strip()
        if isinstance(entry, dict):
            value = entry.get("email") or entry.get("address")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
