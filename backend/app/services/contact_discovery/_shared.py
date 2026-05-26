"""Shared helpers used by multiple Apollo callers.

Lifted out of ``apollo_match`` so the email-extractor enrichment flow
(``app/services/email_extractor/apollo_enrichment.py``) and the
contact-discovery person-match flow parse Apollo's ``phone_numbers``
payload the same way.
"""

from __future__ import annotations

from typing import Any


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
