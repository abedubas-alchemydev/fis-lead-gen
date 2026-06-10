"""Per-user OAuth send plumbing shared by outreach endpoints and Doxie.

Extracted from ``app.api.v1.endpoints.outreach`` so the Doxie chatbot's
``send_outreach_draft`` tool can transmit a saved draft through the exact
same provider path the Outreach composer uses, without a service importing
an endpoint module. Behavior is unchanged: callers resolve a sender
account, build an ``OutreachSend`` audit row, and ``provider_send_and_record``
sends + commits the audit. Failures raise ``HTTPException`` with the same
machine-readable detail codes the FE already handles (Doxie's tool layer
translates them into error dicts instead).
"""

from __future__ import annotations

import logging
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Account
from app.models.outreach_send import OutreachSend
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault import OutreachSendResponse
from app.services.email_providers import (
    PROVIDERS,
    EmailAccountNotLinked,
    EmailProviderConfigurationError,
    EmailScopeRequired,
    EmailSendError,
)
from app.services.email_providers.email_address import (
    extract_email_from_id_token,
)

logger = logging.getLogger(__name__)


# Pre-PR-C error codes the FE already handles. Preserved so the modal's
# existing recovery flow (linkSocial with the send scope) keeps working
# without an FE change for the Gmail path. Microsoft + Yahoo emit the
# provider-prefixed variants below.
SCOPE_ERROR_CODE: dict[str, str] = {
    "google": "gmail_scope_required",
    "microsoft": "microsoft_scope_required",
    "yahoo": "yahoo_scope_required",
}
API_ERROR_CODE: dict[str, str] = {
    "google": "gmail_api_error",
    "microsoft": "microsoft_api_error",
    "yahoo": "yahoo_api_error",
}


def dedupe_recipients(
    to_emails: Iterable[str],
    cc_emails: Iterable[str] = (),
    bcc_emails: Iterable[str] = (),
) -> tuple[list[str], list[str], list[str]]:
    """Normalise + de-duplicate To/CC/BCC across all three buckets.

    An address lands in at most one bucket — earliest wins in To > CC >
    BCC order, matched case-insensitively — so nobody is double-addressed
    (and a BCC recipient can't be silently un-hidden by also sitting in
    To). Within a bucket the first occurrence's original casing is kept
    and order is preserved.
    """

    seen: set[str] = set()

    def _collect(addresses: Iterable[str]) -> list[str]:
        out: list[str] = []
        for raw in addresses:
            addr = str(raw).strip()
            key = addr.lower()
            if key and key not in seen:
                seen.add(key)
                out.append(addr)
        return out

    return _collect(to_emails), _collect(cc_emails), _collect(bcc_emails)


async def record_failure(
    db: AsyncSession, audit: OutreachSend, error_code: str
) -> None:
    """Persist a failed-send audit row and commit so the caller can raise.

    Kept tiny so the happy path stays readable in the send endpoints.
    Errors are recorded as short machine-readable codes (the same codes
    returned to the FE) — full diagnostic detail goes to logger.warning
    upstream so this column stays grep-friendly.
    """
    audit.error = error_code
    db.add(audit)
    await db.commit()


async def resolve_sender_account(
    *,
    db: AsyncSession,
    current_user: AuthenticatedUser,
    folder: VaultFolder | None,
    explicit_account_id: str | None,
) -> Account:
    """Pick which linked OAuth account should send for this request.

    Three-tier fallback (deterministic — no session-state surprises):
      1. Explicit ``sender_account_id`` from the request body (the
         picker in the outreach modal).
      2. The folder's ``default_sender_account_id`` (set on the vault
         folder detail page). Skipped when ``folder`` is None — the
         adhoc-send path doesn't require a folder.
      3. The first linked account with the send scope already granted
         (oldest first; lets onboarding "just work" with one account).
      4. The first linked account at all (will surface a 412
         ``*_scope_required`` downstream and the FE will re-prompt
         consent).

    Raises ``HTTPException`` with a 412 + provider-prefixed
    ``*_account_not_linked`` code when the user has no linked
    accounts. Defaults to ``google_account_not_linked`` for the
    zero-accounts case so the FE shows "Connect Gmail" -- the most
    common onboarding path.
    """

    if explicit_account_id:
        stmt = select(Account).where(
            Account.id == explicit_account_id,
            Account.user_id == current_user.id,
            Account.provider_id.in_(("google", "microsoft", "yahoo")),
        )
        account = (await db.execute(stmt)).scalar_one_or_none()
        if account is None:
            # Don't leak whether the id exists for another user — 404
            # mirrors the rest of the outreach endpoint family.
            raise HTTPException(
                status_code=404, detail="sender_account_not_found"
            )
        return account

    if folder is not None and folder.default_sender_account_id:
        stmt = select(Account).where(
            Account.id == folder.default_sender_account_id,
            Account.user_id == current_user.id,
            Account.provider_id.in_(("google", "microsoft", "yahoo")),
        )
        account = (await db.execute(stmt)).scalar_one_or_none()
        if account is not None:
            return account
        # Fall through silently when the folder default is gone — modal
        # surfaces the orphan separately on the picker side.

    linked_stmt = (
        select(Account)
        .where(Account.user_id == current_user.id)
        .where(Account.provider_id.in_(("google", "microsoft", "yahoo")))
        .order_by(Account.created_at.asc())
    )
    linked = (await db.execute(linked_stmt)).scalars().all()
    if not linked:
        raise HTTPException(
            status_code=412, detail="google_account_not_linked"
        )

    for account in linked:
        provider = PROVIDERS.get(account.provider_id)
        if provider is None:
            continue
        scopes = (account.scope or "").replace(",", " ").split()
        if provider.send_scope in scopes:
            return account
    return linked[0]


async def _backfill_account_email(
    db: AsyncSession, account: Account
) -> None:
    """Populate ``account.email_address`` from the stored id_token.

    Idempotent — no-op if already set. Used by the send path so legacy
    rows (linked before the FE's post-link hook existed) get their
    email captured on first send instead of staying blank forever.
    """

    if account.email_address:
        return
    email = extract_email_from_id_token(account.provider_id, account.id_token)
    if not email:
        return
    account.email_address = email
    # No commit here — the caller's transaction (which writes the
    # outreach_sends audit row) carries it.


async def provider_send_and_record(
    *,
    db: AsyncSession,
    current_user: AuthenticatedUser,
    audit: OutreachSend,
    sender_account: Account,
    to_emails: list[str],
    subject: str,
    body: str,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
) -> OutreachSendResponse:
    """Dispatch the send via the resolved sender account + commit the audit.

    The caller resolves the sender account via :func:`resolve_sender_account`
    so this helper stays focused on the provider plumbing. Provider is
    derived from ``sender_account.provider_id`` (the client-passed
    ``provider`` field is now legacy and overridden).

    Sets ``audit.sender_account_id``, ``audit.sender_email``, and
    ``audit.provider`` from the resolved account before committing,
    so the audit row always reflects which mailbox actually transmitted.

    ``to_emails`` is the visible primary recipient list (>= 1). ``cc_emails``
    are also visible; ``bcc_emails`` are delivered hidden. They're recorded
    on the audit row before the send attempt so failure rows still capture
    the intended recipients.
    """

    # Record the recipient set up front so even a failure row reflects who
    # the message was meant for. Single-To rows leave ``to_emails`` NULL —
    # ``recipient_email`` / the joined contact already carries the lone
    # address; multi-recipient compose-sends store the full list.
    if len(to_emails) > 1:
        audit.to_emails = ", ".join(to_emails)
    if cc_emails:
        audit.cc_emails = ", ".join(cc_emails)
    if bcc_emails:
        audit.bcc_emails = ", ".join(bcc_emails)

    provider_id = sender_account.provider_id
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        await record_failure(db, audit, "unknown_provider")
        raise HTTPException(status_code=400, detail="unknown_provider")

    not_linked_code = f"{provider_id}_account_not_linked"
    not_configured_code = f"{provider_id}_oauth_not_configured"
    scope_required_code = SCOPE_ERROR_CODE[provider_id]
    api_error_code = API_ERROR_CODE[provider_id]

    # Lazy-backfill email_address from id_token before we use it as the
    # send-time From address. Sits before get_fresh_token so a refresh
    # that mutates the account row commits the backfill in one shot.
    await _backfill_account_email(db, sender_account)

    audit.provider = provider_id
    audit.sender_account_id = sender_account.id
    audit.sender_email = sender_account.email_address or current_user.email

    try:
        access_token, scopes = await provider.get_fresh_token(
            db=db, account_id=sender_account.id
        )
    except EmailAccountNotLinked as exc:
        await record_failure(db, audit, not_linked_code)
        raise HTTPException(
            status_code=412, detail=not_linked_code
        ) from exc
    except EmailProviderConfigurationError as exc:
        logger.error(
            "Provider %s OAuth not configured: %s", provider_id, exc
        )
        await record_failure(db, audit, not_configured_code)
        raise HTTPException(
            status_code=503, detail=not_configured_code
        ) from exc

    if provider.send_scope not in scopes:
        await record_failure(db, audit, scope_required_code)
        raise HTTPException(status_code=412, detail=scope_required_code)

    try:
        message_id = await provider.send(
            access_token=access_token,
            sender_email=audit.sender_email,
            to_emails=to_emails,
            subject=subject,
            body=body,
            cc_emails=cc_emails,
            bcc_emails=bcc_emails,
        )
    except EmailScopeRequired as exc:
        await record_failure(db, audit, scope_required_code)
        raise HTTPException(
            status_code=412, detail=scope_required_code
        ) from exc
    except EmailSendError as exc:
        logger.warning("%s send failed: %s", provider_id, exc)
        await record_failure(db, audit, api_error_code)
        raise HTTPException(
            status_code=502, detail=api_error_code
        ) from exc

    audit.status = "sent"
    audit.gmail_message_id = message_id
    audit.error = None
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    return OutreachSendResponse(
        id=audit.id,
        gmail_message_id=message_id,
        sent_at=audit.sent_at,
        status=audit.status,
    )
