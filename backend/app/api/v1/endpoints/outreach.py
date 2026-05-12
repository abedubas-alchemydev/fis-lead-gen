"""Endpoints behind the per-contact Outreach modal on /master-list/{id}.

``POST /outreach/draft`` generates a ``{subject, body}`` via Gemini Flash.
``POST /outreach/send`` transmits the (possibly user-edited) draft as an
email from the logged-in user's own Gmail account (Better Auth's stored
Google OAuth token) and records the attempt in ``outreach_sends``.

Validation is shared via ``_load_outreach_inputs``: both endpoints
verify the caller owns the vault folder, the broker-dealer exists, and
the contact belongs to that broker-dealer. The three failures collapse
to ``404 outreach_inputs_not_found`` so a leaked id can't confirm
existence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.outreach_send import OutreachSend
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault import (
    OutreachDraftRequest,
    OutreachDraftResponse,
    OutreachSendRequest,
    OutreachSendResponse,
)
from app.services.auth import get_current_user
from app.services.gmail_sender import (
    GMAIL_SEND_SCOPE,
    GmailScopeRequired,
    GmailSendError,
    send_gmail,
)
from app.services.google_oauth import (
    GoogleAccountNotLinked,
    GoogleOAuthConfigurationError,
    get_fresh_google_access_token,
)
from app.services.outreach import (
    ContactContext,
    FirmContext,
    OutreachConfigurationError,
    OutreachDraftError,
    ServiceContext,
    generate_outreach_draft,
)
from app.services.vault_retrieval import retrieve_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach")


async def _load_outreach_inputs(
    *,
    broker_dealer_id: int,
    contact_id: int,
    folder_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> tuple[VaultFolder, BrokerDealer, ExecutiveContact]:
    """Validate the (folder, BD, contact) triple shared by draft + send.

    All three failure modes collapse to one detail string so a leaked
    id can't confirm "this folder/contact exists for some other user".
    """
    folder_stmt = select(VaultFolder).where(
        VaultFolder.id == folder_id,
        VaultFolder.user_id == current_user.id,
    )
    folder = (await db.execute(folder_stmt)).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    bd_stmt = select(BrokerDealer).where(BrokerDealer.id == broker_dealer_id)
    broker_dealer = (await db.execute(bd_stmt)).scalar_one_or_none()
    if broker_dealer is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    contact_stmt = select(ExecutiveContact).where(
        ExecutiveContact.id == contact_id,
        ExecutiveContact.bd_id == broker_dealer_id,
    )
    contact = (await db.execute(contact_stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    return folder, broker_dealer, contact


@router.post("/draft", response_model=OutreachDraftResponse)
async def create_outreach_draft(
    payload: OutreachDraftRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftResponse:
    """Generate a cold-email draft for a (firm, contact, service) tuple.

    Misconfigured Gemini key is a 503; any other Gemini failure is a 502.
    """
    folder, broker_dealer, contact = await _load_outreach_inputs(
        broker_dealer_id=payload.broker_dealer_id,
        contact_id=payload.contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )

    firm_ctx = FirmContext(
        name=broker_dealer.name,
        city=broker_dealer.city,
        state=broker_dealer.state,
        current_clearing_partner=broker_dealer.current_clearing_partner,
        firm_operations_text=broker_dealer.firm_operations_text,
    )
    contact_ctx = ContactContext(
        name=contact.name,
        title=contact.title,
        email=contact.email,
    )
    # Build a retrieval query from the firm + contact context so RAG
    # surfaces material relevant to *this specific draft*, not just the
    # service description in the abstract. Mixing in firm-side text
    # (city, current clearing partner, firm operations) produces
    # query embeddings that lean toward chunks discussing similar firms,
    # similar clearing setups, etc.
    query_parts = [
        broker_dealer.name,
        contact.title or "",
        broker_dealer.city or "",
        broker_dealer.state or "",
        broker_dealer.current_clearing_partner or "",
        (broker_dealer.firm_operations_text or "")[:500],
        folder.name,
    ]
    retrieval_query = " ".join(part for part in query_parts if part)

    retrieved: tuple[str, ...] = ()
    if folder.description or retrieval_query:
        try:
            chunks = await retrieve_chunks(
                folder_id=folder.id, query=retrieval_query, db=db
            )
            retrieved = tuple(chunk.text for chunk in chunks)
        except Exception as exc:  # noqa: BLE001
            # Retrieval failure shouldn't break the draft path — fall
            # back to description + instructions only and log it. The
            # endpoint stays responsive even if pgvector / embeddings
            # are temporarily down.
            logger.warning(
                "outreach: chunk retrieval failed for folder %s: %s",
                folder.id,
                exc,
            )

    service_ctx = ServiceContext(
        name=folder.name,
        description=folder.description,
        instructions=folder.outreach_instructions or "",
        retrieved_chunks=retrieved,
    )

    try:
        draft = await generate_outreach_draft(
            firm=firm_ctx, contact=contact_ctx, service=service_ctx
        )
    except OutreachConfigurationError as exc:
        logger.error("outreach draft configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outreach drafts are not configured. Contact an administrator.",
        ) from exc
    except OutreachDraftError as exc:
        logger.warning("outreach draft generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The drafting service is unavailable. Try again in a moment.",
        ) from exc

    return OutreachDraftResponse(subject=draft.subject, body=draft.body)


@router.post("/send", response_model=OutreachSendResponse)
async def send_outreach(
    payload: OutreachSendRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendResponse:
    """Send the (possibly edited) draft via the user's own Gmail account.

    412 responses are recoverable on the frontend:
      - ``google_account_not_linked``: user signed in via email/password
        only, or revoked the app. FE prompts ``linkSocial`` with the
        ``gmail.send`` scope.
      - ``gmail_scope_required``: Google account is linked but the user
        has not yet consented to the send scope (incremental consent).
        FE prompts the same ``linkSocial`` flow.

    Audit: every attempt — success or failure — writes one row to
    ``outreach_sends`` so admins can answer "what was sent to whom" and
    "why did the user see an error".
    """
    folder, _, contact = await _load_outreach_inputs(
        broker_dealer_id=payload.broker_dealer_id,
        contact_id=payload.contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )
    if not contact.email:
        # The FE hides the Outreach button when the contact has no email,
        # so this only fires if the API is called directly.
        raise HTTPException(status_code=400, detail="recipient_no_email")

    audit = OutreachSend(
        user_id=current_user.id,
        broker_dealer_id=payload.broker_dealer_id,
        contact_id=payload.contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
    )

    try:
        access_token, scopes = await get_fresh_google_access_token(
            db=db, user_id=current_user.id
        )
    except GoogleAccountNotLinked as exc:
        await _record_failure(db, audit, "google_account_not_linked")
        raise HTTPException(
            status_code=412, detail="google_account_not_linked"
        ) from exc
    except GoogleOAuthConfigurationError as exc:
        logger.error("Google OAuth not configured: %s", exc)
        await _record_failure(db, audit, "google_oauth_not_configured")
        raise HTTPException(
            status_code=503, detail="google_oauth_not_configured"
        ) from exc

    if GMAIL_SEND_SCOPE not in scopes:
        await _record_failure(db, audit, "gmail_scope_required")
        raise HTTPException(status_code=412, detail="gmail_scope_required")

    try:
        gmail_message_id = await send_gmail(
            access_token=access_token,
            sender_email=current_user.email,
            to_email=contact.email,
            subject=payload.subject,
            body=payload.body,
        )
    except GmailScopeRequired as exc:
        await _record_failure(db, audit, "gmail_scope_required")
        raise HTTPException(
            status_code=412, detail="gmail_scope_required"
        ) from exc
    except GmailSendError as exc:
        logger.warning("Gmail send failed: %s", exc)
        await _record_failure(db, audit, "gmail_api_error")
        raise HTTPException(status_code=502, detail="gmail_api_error") from exc

    audit.status = "sent"
    audit.gmail_message_id = gmail_message_id
    audit.error = None
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    return OutreachSendResponse(
        id=audit.id,
        gmail_message_id=gmail_message_id,
        sent_at=audit.sent_at,
        status=audit.status,
    )


async def _record_failure(
    db: AsyncSession, audit: OutreachSend, error_code: str
) -> None:
    """Persist a failed-send audit row and commit so the caller can raise.

    Kept tiny so the happy path stays readable in ``send_outreach``.
    Errors are recorded as short machine-readable codes (the same codes
    returned to the FE) — full diagnostic detail goes to logger.warning
    upstream so this column stays grep-friendly.
    """
    audit.error = error_code
    db.add(audit)
    await db.commit()
