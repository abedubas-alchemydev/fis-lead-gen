"""Decode an OAuth ``id_token`` to recover the mailbox email address.

Used by the outreach endpoint's lazy-backfill path: when an
``account`` row was linked before ``frontend/lib/auth.ts``'s post-link
hook started writing ``email_address`` (or the hook failed for any
reason), the first send through that account decodes the stored
``id_token`` and persists the address.

Signature verification is skipped on purpose -- Better Auth verified
the ``id_token`` against the provider's JWKS before persisting the
row, so the payload claims are already trusted. We only need to peel
the base64 envelope off the middle segment and pull the right claim
for the provider.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any


def extract_email_from_id_token(
    provider_id: str, id_token: str | None
) -> str | None:
    """Return the mailbox address claim from an OAuth ``id_token``.

    Per-provider claim ordering:
      - google, yahoo: ``email``
      - microsoft: ``preferred_username`` (AAD work/school + outlook.com
        consumer), with ``upn`` and ``email`` as fallbacks for older
        tenants that don't set ``preferred_username``.

    Returns None for any unknown provider, malformed token, or missing
    claim -- callers must handle the None branch (typically by leaving
    ``account.email_address`` NULL and letting the FE picker label the
    account by provider name only).
    """

    if not id_token:
        return None
    payload = _decode_jwt_payload(id_token)
    if payload is None:
        return None

    if provider_id in {"google", "yahoo"}:
        email = payload.get("email")
        return email if isinstance(email, str) and email else None

    if provider_id == "microsoft":
        for claim in ("preferred_username", "upn", "email"):
            value = payload.get(claim)
            if isinstance(value, str) and value:
                return value
        return None

    return None


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Pull the middle segment of a JWT and parse it as JSON.

    Tolerant of missing padding (JWTs use base64url with the ``=``
    trailers stripped). Returns None for any decode failure so callers
    can branch on a single None check.
    """

    parts = token.split(".")
    if len(parts) < 2:
        return None
    b64 = parts[1].replace("-", "+").replace("_", "/")
    pad_len = (4 - len(b64) % 4) % 4
    b64 += "=" * pad_len
    try:
        raw = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
