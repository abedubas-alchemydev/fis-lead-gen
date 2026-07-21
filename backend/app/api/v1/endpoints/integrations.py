"""Machine-to-machine integration endpoints (service-to-service reads).

These routes are consumed by SIBLING SERVICES, not by a browser session. The
first (and only, so far) consumer is the CRM, which surfaces DOX "saved
contacts" for every user. Unlike the owner-scoped ``/api/v1/saved-contacts``
surface -- BetterAuth session cookie + ``email_extractor`` feature gate, one
user's rows only -- this endpoint is a CROSS-USER read guarded solely by a
shared bearer key (``settings.crm_integration_api_key``). There is no session,
no feature grant, and no per-user DB lookup: it is a service credential.

Auth contract (see ``_require_crm_integration_key``):
* Key unset/empty  -> 503 (integration not configured; FAILS CLOSED so the
  cross-user read is unreachable without a deliberately configured key -- an
  empty string can never match).
* Missing/bad token -> 401 (no valid credentials). Constant-time compared with
  ``hmac.compare_digest`` to mirror the token handling in ``services/auth.py``.

Contract:
* ``GET /api/v1/integrations/saved-contacts?source=discovered_email``
  -> ``SavedContactWithOwner[]`` (newest first), one row per saved contact
  across ALL users, each carrying the owner's id/name/email.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.saved_contact import SavedContactResponse, SavedContactWithOwner
from app.services.saved_contacts import list_all_saved_contacts

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _require_crm_integration_key(request: Request) -> None:
    """Gate the integration surface on the shared CRM bearer key.

    FAILS CLOSED: when ``crm_integration_api_key`` is unset/empty the whole
    surface returns 503 rather than allowing an empty-string token to match --
    a missing deployment secret must never open a cross-user read. A missing or
    mismatched ``Authorization: Bearer <token>`` returns 401. The token is
    compared with ``hmac.compare_digest`` so a probe can't recover it by timing
    (mirrors the constant-time comparisons in ``services/auth.py``). No session
    cookie, feature grant, or DB lookup -- this is a service-to-service key.
    """
    expected = settings.crm_integration_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRM integration is not configured.",
        )

    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :].strip()
    # ``token`` comes off the (latin-1 decoded) Authorization header; a non-ASCII
    # byte makes a str/str ``compare_digest`` raise TypeError -- an uncaught 500
    # an UNAUTHENTICATED caller could trigger. Compare BYTES, which never raises
    # on non-ASCII (mirrors the try/except guard in services/lead_shares.py). The
    # 503 fail-closed check above still runs first, so an empty key can't match.
    if not token or not hmac.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing integration token.",
        )


@router.get(
    "/saved-contacts",
    response_model=list[SavedContactWithOwner],
    dependencies=[Depends(_require_crm_integration_key)],
)
async def list_saved_contacts_for_crm(
    source: str | None = Query(default=None),
    limit: int = Query(default=1000),
    db: AsyncSession = Depends(get_db_session),
) -> list[SavedContactWithOwner]:
    """Return EVERY user's saved contacts, newest first, each annotated with
    the owner's id/name/email.

    Optional ``source`` filters to a single origin (e.g. ``discovered_email``).
    Optional ``limit`` caps the page size; it is clamped to ``1..1000`` in the
    service, so an out-of-range value returns the newest 1000 rather than 422.
    The CRM caller omits it and receives the newest 1000 (the v1 cap).

    Cross-user by design -- the owner-scoped surface is ``/api/v1/saved-contacts``;
    this one exists so the CRM can attribute each saved contact to the user who
    pinned it. The auth gate runs as a route dependency, so a bad/absent key is
    rejected before any DB query executes.
    """
    rows = await list_all_saved_contacts(db, source, limit=limit)
    return [
        SavedContactWithOwner(
            **SavedContactResponse.model_validate(contact).model_dump(),
            saved_by_id=owner_id,
            saved_by_name=owner_name,
            saved_by_email=owner_email,
        )
        for contact, owner_id, owner_name, owner_email in rows
    ]
