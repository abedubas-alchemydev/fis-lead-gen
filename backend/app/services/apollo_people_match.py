"""Apollo ``/people/match`` wrapper for email-keyed contact lookups.

Used as the opt-in fallback in ``POST /contacts/find-by-email`` when an
email has no hit in any local table. Single-shot lookup -- caller pays
~1 Apollo credit per call. Returns ``None`` on any provider failure so
the caller can fall through to "no results" gracefully.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.contact_search import ContactSearchHit

logger = logging.getLogger(__name__)

_APOLLO_BASE_URL = "https://api.apollo.io/v1"
_TIMEOUT_SECONDS = 8.0


async def match_person_by_email(email: str) -> ContactSearchHit | None:
    """Lookup ``email`` via Apollo /people/match.

    Returns a ``ContactSearchHit`` with ``source='apollo'`` and
    ``firm_type=None`` (Apollo doesn't classify the firm into our
    three-list taxonomy). Returns ``None`` on missing API key, on
    HTTP error, or when Apollo returns no person.
    """

    api_key = getattr(settings, "apollo_api_key", None) or getattr(
        settings, "APOLLO_API_KEY", None
    )
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{_APOLLO_BASE_URL}/people/match",
                headers={
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                    "X-Api-Key": api_key,
                },
                json={"email": email, "reveal_personal_emails": False},
            )
    except httpx.HTTPError as exc:
        logger.warning("apollo people/match transport error: %s", exc)
        return None

    if response.status_code != 200:
        logger.info(
            "apollo people/match returned %s for %s", response.status_code, email
        )
        return None

    body: dict[str, Any] = response.json()
    person = body.get("person")
    if not person:
        return None

    organization = person.get("organization") or {}
    return ContactSearchHit(
        source="apollo",
        firm_type=None,
        firm_id=None,
        firm_name=organization.get("name"),
        contact_id=None,
        name=person.get("name") or (
            " ".join(
                p for p in [person.get("first_name"), person.get("last_name")] if p
            )
            or None
        ),
        title=person.get("title"),
        email=person.get("email") or email,
        phone=person.get("phone_number"),
        linkedin_url=person.get("linkedin_url"),
    )
