"""Apollo.io /people/match reverse-email enrichment for DiscoveredEmail rows.

Per-row, user-triggered: the endpoint at
`POST /api/v1/email-extractor/discovered-emails/{id}/enrich` invokes this.
Each call consumes one Apollo credit (paid), so the frontend gates it behind
an explicit button per row to keep usage bounded to human clicks.

Writes to the enrichment columns added in Alembic 20260423_0013
(enriched_name, enriched_title, enriched_linkedin_url, enriched_company,
enriched_at, enrichment_status) plus ``enriched_phone`` (added in
20260515_0041) and ``enriched_email`` + ``apollo_person_id`` (added in
20260529_0069). The request opts into ``reveal_personal_emails`` (returned
synchronously) and, when the reveal flow is configured, ``reveal_phone_number``
-- the phone then arrives asynchronously via the webhook and is matched back to
this row by ``apollo_person_id``. Status maps:

- `enriched`   — Apollo returned a person match; fields populated.
- `no_match`   — Apollo returned 200 but no person object; fields stay null.
- `error`      — HTTP failure / unexpected payload; status stored, caller
                  sees a 502 so the UI can show an error pill.

Error-prefix convention (per brokercheck_extractor ADR 0002): this module
emits bare error strings; the caller in the endpoint wraps them with
`apollo: <err>` exactly once.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.discovered_email import DiscoveredEmail
from app.services.contact_discovery._shared import (
    apollo_phone_reveal_fields,
    first_apollo_phone,
)

logger = logging.getLogger(__name__)

APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
REQUEST_TIMEOUT_SECONDS = 20.0


class EnrichmentError(Exception):
    """Bare error message wrapping an HTTP / payload failure from Apollo."""


async def enrich_discovered_email(db: AsyncSession, discovered_email_id: int) -> DiscoveredEmail:
    """Enrich a single DiscoveredEmail row via Apollo /people/match.

    Commits the updated row and returns it. Raises `EnrichmentError` with a
    bare string on any HTTP / config failure; the caller is responsible for
    the 'apollo:' prefix in response bodies and logs.
    """
    row = await db.get(DiscoveredEmail, discovered_email_id)
    if row is None:
        raise EnrichmentError("discovered_email not found")

    api_key = settings.apollo_api_key
    if not api_key:
        raise EnrichmentError("APOLLO_API_KEY not configured")

    # reveal_personal_emails surfaces the person's email even when the
    # discovered address is a generic/role inbox (returned synchronously).
    # apollo_phone_reveal_fields() adds reveal_phone_number + webhook_url when
    # the async reveal flow is configured -- the number then lands via the
    # webhook, keyed back to this row by apollo_person_id.
    payload: dict[str, Any] = {"email": row.email, "reveal_personal_emails": True}
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
        row.enrichment_status = "error"
        row.enriched_at = datetime.now(UTC)
        await db.commit()
        raise EnrichmentError(f"network: {exc.__class__.__name__}: {exc}") from exc

    if response.status_code != 200:
        row.enrichment_status = "error"
        row.enriched_at = datetime.now(UTC)
        await db.commit()
        snippet = response.text[:200] if response.text else "(no body)"
        raise EnrichmentError(f"http {response.status_code}: {snippet}")

    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        row.enrichment_status = "error"
        row.enriched_at = datetime.now(UTC)
        await db.commit()
        raise EnrichmentError(f"invalid json: {exc}") from exc

    person = data.get("person") if isinstance(data, dict) else None
    if not isinstance(person, dict):
        row.enrichment_status = "no_match"
        row.enriched_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return row

    row.enriched_name = _first_string(person, ["name"]) or _compose_name(person)
    row.enriched_title = _first_string(person, ["title", "headline"])
    row.enriched_linkedin_url = _first_string(person, ["linkedin_url"])
    row.enriched_email = _first_personal_email(person) or _first_string(person, ["email"])
    organization = person.get("organization")
    if isinstance(organization, dict):
        row.enriched_company = _first_string(organization, ["name", "display_name"])
    # Sync phone is usually empty -- the real number arrives via the
    # phone-reveal webhook, matched back by apollo_person_id.
    row.enriched_phone = first_apollo_phone(person.get("phone_numbers"))
    row.apollo_person_id = _person_id(person)
    row.enriched_at = datetime.now(UTC)
    row.enrichment_status = "enriched"

    await db.commit()
    await db.refresh(row)
    return row


def _first_string(obj: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _compose_name(person: dict[str, Any]) -> str | None:
    first = _first_string(person, ["first_name"])
    last = _first_string(person, ["last_name"])
    if first and last:
        return f"{first} {last}"
    return first or last


def _person_id(person: dict[str, Any]) -> str | None:
    """Apollo's stable person id, stamped so the async phone-reveal webhook can
    correlate a revealed number back to this row."""
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
