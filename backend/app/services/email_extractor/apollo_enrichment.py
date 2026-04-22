"""Apollo.io /people/match reverse-email enrichment for DiscoveredEmail rows.

Per-row, user-triggered: the endpoint at
`POST /api/v1/email-extractor/discovered-emails/{id}/enrich` invokes this.
Each call consumes one Apollo credit (paid), so the frontend gates it behind
an explicit button per row to keep usage bounded to human clicks.

Writes to the 6 enrichment columns added in Alembic 20260423_0013
(enriched_name, enriched_title, enriched_linkedin_url, enriched_company,
enriched_at, enrichment_status). Status maps:

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

    payload = {"email": row.email}
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
    organization = person.get("organization")
    if isinstance(organization, dict):
        row.enriched_company = _first_string(organization, ["name", "display_name"])
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
