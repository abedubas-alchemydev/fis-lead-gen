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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.advisor_contact import AdvisorContact
from app.models.auth import AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
from app.models.outreach_send import OutreachSend
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault import (
    OutreachAdvisorDraftRequest,
    OutreachAdvisorSendRequest,
    OutreachDraftRequest,
    OutreachDraftResponse,
    OutreachInvestorDraftRequest,
    OutreachInvestorSendRequest,
    OutreachSendDetailResponse,
    OutreachSendItem,
    OutreachSendRequest,
    OutreachSendResponse,
    OutreachSendsListResponse,
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


async def _load_advisor_outreach_inputs(
    *,
    advisor_id: int,
    advisor_contact_id: int,
    folder_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> tuple[VaultFolder, InvestmentAdvisor, AdvisorContact]:
    """Validate the (folder, advisor, advisor-contact) triple.

    Same opaque ``outreach_inputs_not_found`` shape as the BD variant so
    a leaked id can't confirm cross-user existence.
    """

    folder = (
        await db.execute(
            select(VaultFolder).where(
                VaultFolder.id == folder_id,
                VaultFolder.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    advisor = (
        await db.execute(
            select(InvestmentAdvisor).where(InvestmentAdvisor.id == advisor_id)
        )
    ).scalar_one_or_none()
    if advisor is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    contact = (
        await db.execute(
            select(AdvisorContact).where(
                AdvisorContact.id == advisor_contact_id,
                AdvisorContact.advisor_id == advisor_id,
            )
        )
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    return folder, advisor, contact


async def _load_investor_outreach_inputs(
    *,
    institutional_investor_id: int,
    investor_contact_id: int,
    folder_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> tuple[VaultFolder, InstitutionalInvestor, InvestorContact]:
    """Validate the (folder, investor, investor-contact) triple."""

    folder = (
        await db.execute(
            select(VaultFolder).where(
                VaultFolder.id == folder_id,
                VaultFolder.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    investor = (
        await db.execute(
            select(InstitutionalInvestor).where(
                InstitutionalInvestor.id == institutional_investor_id
            )
        )
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    contact = (
        await db.execute(
            select(InvestorContact).where(
                InvestorContact.id == investor_contact_id,
                InvestorContact.investor_id == institutional_investor_id,
            )
        )
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    return folder, investor, contact


async def _generate_polymorphic_draft(
    *,
    firm_ctx: FirmContext,
    contact_ctx: ContactContext,
    folder: VaultFolder,
    db: AsyncSession,
) -> OutreachDraftResponse:
    """Shared draft-generation path used by BD/advisor/investor endpoints.

    Builds the retrieval query from firm + contact context, fetches RAG
    chunks, hands the bundle to the Gemini-Flash drafter. Errors collapse
    the same way the BD path does: 503 for misconfiguration, 502 for any
    other provider failure.
    """

    query_parts = [
        firm_ctx.name,
        contact_ctx.title or "",
        firm_ctx.city or "",
        firm_ctx.state or "",
        firm_ctx.current_clearing_partner or "",
        (firm_ctx.firm_operations_text or "")[:500],
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


def _advisor_firm_operations(advisor: InvestmentAdvisor) -> str | None:
    """Synthesize a firm_operations_text-equivalent blurb for advisors.

    Advisors don't have a free-text firm_operations field on the model
    -- the closest analog is the Form ADV Item 5.G advisory_activities
    list. Joining them produces a one-line summary the LLM can use as
    soft context.
    """

    activities = advisor.advisory_activities or []
    if not activities:
        return None
    return "Advisory activities: " + ", ".join(str(a) for a in activities if a)


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


def _ensure_admin(current_user: AuthenticatedUser) -> None:
    """Mirror of settings.py:_ensure_admin — admins only past this gate."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def _row_to_item(row: tuple, *, include_sender: bool) -> dict:
    """Flatten a join row into the dict shape both list + detail responses
    consume. Polymorphic across firm + contact types -- picks the
    populated (id, name) pair based on which FK column is non-null on
    the send row.
    """
    send: OutreachSend = row[0]
    # Indices: 0=send, 1=bd_name, 2=advisor_name, 3=investor_name,
    # 4=exec_contact_name, 5=exec_contact_email,
    # 6=advisor_contact_name, 7=advisor_contact_email,
    # 8=investor_contact_name, 9=investor_contact_email,
    # 10=folder_name, (11=sender_name, 12=sender_email) for admin scope.
    if send.broker_dealer_id is not None:
        firm_type = "broker_dealer"
    elif send.advisor_id is not None:
        firm_type = "advisor"
    else:
        firm_type = "institutional_investor"

    if send.contact_id is not None:
        contact_type = "executive_contact"
        contact_name = row[4] or ""
        contact_email = row[5]
    elif send.advisor_contact_id is not None:
        contact_type = "advisor_contact"
        contact_name = row[6] or ""
        contact_email = row[7]
    else:
        contact_type = "investor_contact"
        contact_name = row[8] or ""
        contact_email = row[9]

    payload = {
        "id": send.id,
        "sent_at": send.sent_at,
        "status": send.status,
        "subject": send.subject,
        "gmail_message_id": send.gmail_message_id,
        "error": send.error,
        "firm_type": firm_type,
        "broker_dealer_id": send.broker_dealer_id,
        "broker_dealer_name": row[1] or "" if send.broker_dealer_id else None,
        "advisor_id": send.advisor_id,
        "advisor_name": row[2] or "" if send.advisor_id else None,
        "institutional_investor_id": send.institutional_investor_id,
        "institutional_investor_name": (
            row[3] or "" if send.institutional_investor_id else None
        ),
        "contact_type": contact_type,
        "contact_id": send.contact_id,
        "advisor_contact_id": send.advisor_contact_id,
        "investor_contact_id": send.investor_contact_id,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "folder_id": send.folder_id,
        "folder_name": row[10],
    }
    if include_sender:
        payload["user_id"] = send.user_id
        payload["sender_name"] = row[11]
        payload["sender_email"] = row[12]
    return payload


def _base_send_select(*, include_sender: bool):
    """Shared SELECT for list + detail. ``include_sender`` adds the
    AuthUser join so the row tuple carries (..., sender_name,
    sender_email) for the admin scope.

    LEFT JOINs all three firm tables + all three contact tables so the
    same query handles any row's firm/contact type without UNION ALL.
    """
    columns = [
        OutreachSend,
        BrokerDealer.name,
        InvestmentAdvisor.name,
        InstitutionalInvestor.name,
        ExecutiveContact.name,
        ExecutiveContact.email,
        AdvisorContact.name,
        AdvisorContact.email,
        InvestorContact.name,
        InvestorContact.email,
        VaultFolder.name,
    ]
    if include_sender:
        columns.extend([AuthUser.name, AuthUser.email])
    stmt = (
        select(*columns)
        .outerjoin(BrokerDealer, BrokerDealer.id == OutreachSend.broker_dealer_id)
        .outerjoin(InvestmentAdvisor, InvestmentAdvisor.id == OutreachSend.advisor_id)
        .outerjoin(
            InstitutionalInvestor,
            InstitutionalInvestor.id == OutreachSend.institutional_investor_id,
        )
        .outerjoin(ExecutiveContact, ExecutiveContact.id == OutreachSend.contact_id)
        .outerjoin(
            AdvisorContact, AdvisorContact.id == OutreachSend.advisor_contact_id
        )
        .outerjoin(
            InvestorContact, InvestorContact.id == OutreachSend.investor_contact_id
        )
        .outerjoin(VaultFolder, VaultFolder.id == OutreachSend.folder_id)
    )
    if include_sender:
        stmt = stmt.outerjoin(AuthUser, AuthUser.id == OutreachSend.user_id)
    return stmt


# ── Advisor + Investor outreach endpoints ─────────────────────────────
# Same shape as POST /draft and POST /send but keyed to advisor / investor
# firm + contact ids. The audit row uses the matching nullable FKs from
# migration 0046; the 2 XOR check constraints on outreach_sends keep
# rows from straddling firm types.


@router.post("/advisor-draft", response_model=OutreachDraftResponse)
async def create_advisor_outreach_draft(
    payload: OutreachAdvisorDraftRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftResponse:
    """Generate a cold-email draft for (advisor, advisor-contact, folder)."""

    folder, advisor, contact = await _load_advisor_outreach_inputs(
        advisor_id=payload.advisor_id,
        advisor_contact_id=payload.advisor_contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )
    firm_ctx = FirmContext(
        name=advisor.name,
        city=advisor.city,
        state=advisor.state,
        current_clearing_partner=None,
        firm_operations_text=(
            advisor.firm_operations_text or _advisor_firm_operations(advisor)
        ),
    )
    contact_ctx = ContactContext(
        name=contact.name, title=contact.title, email=contact.email
    )
    return await _generate_polymorphic_draft(
        firm_ctx=firm_ctx, contact_ctx=contact_ctx, folder=folder, db=db
    )


@router.post("/advisor-send", response_model=OutreachSendResponse)
async def send_advisor_outreach(
    payload: OutreachAdvisorSendRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendResponse:
    """Send the (possibly edited) draft to an advisor contact.

    Same Gmail OAuth + Google scope handling as the BD ``/send``
    endpoint -- 412 + machine-readable detail when the user needs to
    grant additional consent, 502 on Gmail API failure, 503 on
    misconfiguration. Audit row uses ``advisor_id`` +
    ``advisor_contact_id`` FKs.
    """

    folder, _, contact = await _load_advisor_outreach_inputs(
        advisor_id=payload.advisor_id,
        advisor_contact_id=payload.advisor_contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )
    if not contact.email:
        raise HTTPException(status_code=400, detail="recipient_no_email")

    audit = OutreachSend(
        user_id=current_user.id,
        advisor_id=payload.advisor_id,
        advisor_contact_id=payload.advisor_contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
    )
    return await _gmail_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        recipient_email=contact.email,
        subject=payload.subject,
        body=payload.body,
    )


@router.post("/investor-draft", response_model=OutreachDraftResponse)
async def create_investor_outreach_draft(
    payload: OutreachInvestorDraftRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftResponse:
    """Generate a cold-email draft for (investor, investor-contact, folder)."""

    folder, investor, contact = await _load_investor_outreach_inputs(
        institutional_investor_id=payload.institutional_investor_id,
        investor_contact_id=payload.investor_contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )
    firm_ctx = FirmContext(
        name=investor.name,
        city=investor.city,
        state=investor.state,
        current_clearing_partner=None,
        firm_operations_text=None,
    )
    contact_ctx = ContactContext(
        name=contact.name, title=contact.title, email=contact.email
    )
    return await _generate_polymorphic_draft(
        firm_ctx=firm_ctx, contact_ctx=contact_ctx, folder=folder, db=db
    )


@router.post("/investor-send", response_model=OutreachSendResponse)
async def send_investor_outreach(
    payload: OutreachInvestorSendRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendResponse:
    """Send the (possibly edited) draft to an investor contact."""

    folder, _, contact = await _load_investor_outreach_inputs(
        institutional_investor_id=payload.institutional_investor_id,
        investor_contact_id=payload.investor_contact_id,
        folder_id=payload.folder_id,
        current_user=current_user,
        db=db,
    )
    if not contact.email:
        raise HTTPException(status_code=400, detail="recipient_no_email")

    audit = OutreachSend(
        user_id=current_user.id,
        institutional_investor_id=payload.institutional_investor_id,
        investor_contact_id=payload.investor_contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
    )
    return await _gmail_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        recipient_email=contact.email,
        subject=payload.subject,
        body=payload.body,
    )


async def _gmail_send_and_record(
    *,
    db: AsyncSession,
    current_user: AuthenticatedUser,
    audit: OutreachSend,
    recipient_email: str,
    subject: str,
    body: str,
) -> OutreachSendResponse:
    """Shared Gmail send + audit-row commit path.

    Pulled out so BD/advisor/investor /send handlers don't re-implement
    the Gmail OAuth handshake, scope validation, and audit-failure paths.
    Raises the same 412/502/503 HTTPExceptions the BD /send raises;
    persists an OutreachSend row on every outcome (success + each
    failure code).
    """

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
            to_email=recipient_email,
            subject=subject,
            body=body,
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


@router.get("/sends", response_model=OutreachSendsListResponse)
async def list_outreach_sends(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Literal["sent", "failed"] | None = Query(
        None, alias="status"
    ),
    scope: Literal["mine", "all"] = Query("mine"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendsListResponse:
    """Paginated list of outreach attempts (success + failure).

    Default scope ``mine`` returns the caller's own sends — unchanged
    behavior. Scope ``all`` is admin-gated and returns every user's sends
    with sender_name / sender_email joined in so the FE can render a
    Sender column. Joins broker-dealer / contact / vault-folder metadata
    so the FE renders rows without an N+1. Body is omitted from the
    list payload — fetch via ``GET /outreach/sends/{send_id}`` on row
    expand.
    """
    include_sender = scope == "all"
    if include_sender:
        _ensure_admin(current_user)

    base = _base_send_select(include_sender=include_sender)
    if not include_sender:
        base = base.where(OutreachSend.user_id == current_user.id)
    if status_filter is not None:
        base = base.where(OutreachSend.status == status_filter)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows_stmt = base.order_by(OutreachSend.sent_at.desc()).limit(limit).offset(
        offset
    )
    rows = (await db.execute(rows_stmt)).all()

    items = [
        OutreachSendItem(**_row_to_item(row, include_sender=include_sender))
        for row in rows
    ]
    return OutreachSendsListResponse(
        items=items, total=total, limit=limit, offset=offset
    )


@router.get(
    "/sends/{send_id}", response_model=OutreachSendDetailResponse
)
async def get_outreach_send(
    send_id: int,
    scope: Literal["mine", "all"] = Query("mine"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendDetailResponse:
    """Full body + metadata for one send.

    Default scope ``mine`` returns 404 for both "id does not exist" and
    "id belongs to another user" so a leaked id can't confirm cross-user
    existence. Scope ``all`` is admin-gated and lets admins open any
    user's send.
    """
    include_sender = scope == "all"
    if include_sender:
        _ensure_admin(current_user)

    stmt = _base_send_select(include_sender=include_sender).where(
        OutreachSend.id == send_id
    )
    if not include_sender:
        stmt = stmt.where(OutreachSend.user_id == current_user.id)

    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="outreach_send_not_found")

    payload = _row_to_item(row, include_sender=include_sender)
    payload["body"] = row[0].body
    return OutreachSendDetailResponse(**payload)
