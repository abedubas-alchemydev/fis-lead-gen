"""Shared helpers used by multiple Apollo callers.

Lifted out of ``apollo_match`` so the email-extractor enrichment flow
(``app/services/email_extractor/apollo_enrichment.py``) and the
contact-discovery person-match flow parse Apollo's ``phone_numbers``
payload the same way.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings


def apollo_phone_reveal_configured() -> bool:
    """True when the async Apollo phone-reveal flow is wired.

    Both the shared webhook secret and a public base URL must be set for
    Apollo to call us back; when either is missing the feature stays dormant.
    """
    return bool(settings.apollo_webhook_secret and settings.public_base_url)


def apollo_phone_reveal_fields() -> dict[str, Any]:
    """Reveal-request fields to merge into an Apollo ``/people/match`` payload.

    Returns ``{"reveal_phone_number": True, "webhook_url": ...}`` when the
    reveal flow is configured, else ``{}`` -- so callers can write
    ``payload.update(apollo_phone_reveal_fields())`` and stay dormant when it
    isn't wired. The phone arrives later via POST to ``webhook_url``; the
    handler in ``endpoints/webhooks_apollo.py`` correlates it back to the row
    by ``apollo_person_id``. Single source of truth for the webhook URL shape.
    """
    if not apollo_phone_reveal_configured():
        return {}
    base_url = settings.public_base_url or ""
    return {
        "reveal_phone_number": True,
        "webhook_url": (
            f"{base_url.rstrip('/')}/api/v1/webhooks/apollo/"
            f"{settings.apollo_webhook_secret}/phone-reveal"
        ),
    }


def first_apollo_phone(phone_numbers: Any) -> str | None:
    """Pick the first usable phone string from Apollo's ``phone_numbers`` field.

    Apollo returns a list of dicts with ``sanitized_number`` (E.164-ish)
    and ``raw_number`` (as-typed). Prefer sanitized; fall back to raw.
    Tolerates the legacy shape where the array element is a bare string.
    """
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
