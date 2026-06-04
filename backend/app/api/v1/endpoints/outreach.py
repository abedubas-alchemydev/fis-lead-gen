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

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.advisor_contact import AdvisorContact
from app.models.auth import Account, AuthUser
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.institutional_investor import InstitutionalInvestor
from app.models.investment_advisor import InvestmentAdvisor
from app.models.investor_contact import InvestorContact
from app.models.outreach_draft import OutreachDraft
from app.models.outreach_send import OutreachSend
from app.models.pipeline_run import PipelineRun
from app.models.user_outreach_settings import UserOutreachSettings
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.schemas.outreach_contacts import (
    GapFillFirmResponse,
    OutreachContactPerson,
    OutreachContactsFirmDetailResponse,
    OutreachContactsFirmRow,
    OutreachContactsFirmsResponse,
)
from app.schemas.vault import (
    FavoriteFirmsResponse,
    FavoriteSearchResponse,
    FavoriteSearchResult,
    FirmContactsResponse,
    FirmSearchResponse,
    FirmSearchResult,
    LinkedProviderItem,
    LinkedProvidersResponse,
    OptimizeInstructionsRequest,
    OptimizeInstructionsResponse,
    OutreachAdhocDraftRequest,
    OutreachAdhocSendRequest,
    OutreachAdvisorDraftRequest,
    OutreachAdvisorSendRequest,
    OutreachComposeSendRequest,
    OutreachDraftDetail,
    OutreachDraftItem,
    OutreachDraftRecipient,
    OutreachDraftRequest,
    OutreachDraftResponse,
    OutreachDraftSaveRequest,
    OutreachDraftsListResponse,
    OutreachInvestorDraftRequest,
    OutreachInvestorSendRequest,
    OutreachSendDetailResponse,
    OutreachSendItem,
    OutreachSendRequest,
    OutreachSendResponse,
    OutreachSendsListResponse,
    OutreachSignatureResponse,
    OutreachSignatureUpdate,
    RecipientSearchResponse,
    RecipientSearchResult,
)
from app.core.feature_permissions import OUTREACH_CONTACTS, SENT_OUTREACH
from app.services.advisor_refresh_orchestrator import (
    GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
    run_gap_fill_advisor_contacts_background,
)
from app.services.auth import ensure_feature, get_current_user
from app.services.bd_contact_gap_fill import (
    GAP_FILL_BD_CONTACTS_PIPELINE_NAME,
    run_gap_fill_bd_contacts_background,
)
from app.services.investor_contact_gap_fill import (
    GAP_FILL_II_CONTACTS_PIPELINE_NAME,
    run_gap_fill_ii_contacts_background,
)
from app.services.pipeline_reaper import STALE_REFRESH_RUN_AGE
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
from app.services.outreach import (
    ContactContext,
    FirmContext,
    OutreachConfigurationError,
    OutreachDraftError,
    ServiceContext,
    generate_outreach_draft,
    optimize_instructions,
)
from app.services.vault_retrieval import retrieve_chunks


# Pre-PR-C error codes the FE already handles. Preserved so the modal's
# existing recovery flow (linkSocial with the send scope) keeps working
# without an FE change for the Gmail path. Microsoft + Yahoo emit the
# provider-prefixed variants below.
_SCOPE_ERROR_CODE: dict[str, str] = {
    "google": "gmail_scope_required",
    "microsoft": "microsoft_scope_required",
    "yahoo": "yahoo_scope_required",
}
_API_ERROR_CODE: dict[str, str] = {
    "google": "gmail_api_error",
    "microsoft": "microsoft_api_error",
    "yahoo": "yahoo_api_error",
}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach")


def _require_sent_outreach(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, SENT_OUTREACH)
    return user


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


@router.post("/adhoc-draft", response_model=OutreachDraftResponse)
async def create_adhoc_outreach_draft(
    payload: OutreachAdhocDraftRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftResponse:
    """Generate a cold-email draft for the free-form-email (adhoc) path.

    No firm / contact context — the LLM works from the service folder's
    description + RAG excerpts plus the optional recipient name. The
    retrieval query is built from the folder name + description alone
    (firm-side text isn't available), so chunks lean toward the most
    general material in the service folder.

    Same error mapping as ``/outreach/draft``: 503 for misconfigured
    Gemini, 502 for any other provider failure, 404 if the folder
    doesn't belong to the caller.
    """
    folder_stmt = select(VaultFolder).where(
        VaultFolder.id == payload.folder_id,
        VaultFolder.user_id == current_user.id,
    )
    folder = (await db.execute(folder_stmt)).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    retrieval_query = " ".join(
        part for part in (folder.name, folder.description or "") if part
    )
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
            service=service_ctx, recipient_name=payload.recipient_name
        )
    except OutreachConfigurationError as exc:
        logger.error("outreach adhoc draft configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outreach drafts are not configured. Contact an administrator.",
        ) from exc
    except OutreachDraftError as exc:
        logger.warning("outreach adhoc draft generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The drafting service is unavailable. Try again in a moment.",
        ) from exc

    return OutreachDraftResponse(subject=draft.subject, body=draft.body)


@router.post("/optimize-instructions", response_model=OptimizeInstructionsResponse)
async def optimize_outreach_instructions(
    payload: OptimizeInstructionsRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> OptimizeInstructionsResponse:
    """Rewrite the user's per-service outreach instructions via Gemini Flash.

    Backs the "Optimize Prompt" button on the Vault service editor. Stateless
    — no folder lookup; the text is sent and an improved version returned, so
    it works from the create form (before a folder exists) as well as the
    detail editor. Misconfigured Gemini key is a 503; any other Gemini
    failure (or empty input) is a 502.
    """
    try:
        optimized = await optimize_instructions(payload.text)
    except OutreachConfigurationError as exc:
        logger.error("optimize instructions configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt optimization is not configured. Contact an administrator.",
        ) from exc
    except OutreachDraftError as exc:
        logger.warning("optimize instructions failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The optimization service is unavailable. Try again in a moment.",
        ) from exc

    return OptimizeInstructionsResponse(optimized_text=optimized)


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

    sender_account = await _resolve_sender_account(
        db=db,
        current_user=current_user,
        folder=folder,
        explicit_account_id=payload.sender_account_id,
    )
    audit = OutreachSend(
        user_id=current_user.id,
        broker_dealer_id=payload.broker_dealer_id,
        contact_id=payload.contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
        provider=sender_account.provider_id,
    )
    return await _provider_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        sender_account=sender_account,
        to_emails=[contact.email],
        subject=payload.subject,
        body=payload.body,
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
    elif send.institutional_investor_id is not None:
        firm_type = "institutional_investor"
    else:
        # All firm FKs null = adhoc row from /outreach/adhoc-send.
        firm_type = "adhoc"

    if send.contact_id is not None:
        contact_type = "executive_contact"
        contact_name = row[4] or ""
        contact_email = row[5]
    elif send.advisor_contact_id is not None:
        contact_type = "advisor_contact"
        contact_name = row[6] or ""
        contact_email = row[7]
    elif send.investor_contact_id is not None:
        contact_type = "investor_contact"
        contact_name = row[8] or ""
        contact_email = row[9]
    else:
        # Adhoc rows: no joined contact. The recipient address lives
        # on the send row itself (recipient_email column); the FE
        # uses recipient_name when present, else falls back to email.
        contact_type = "adhoc"
        contact_name = send.recipient_name or ""
        contact_email = send.recipient_email

    payload = {
        "id": send.id,
        "sent_at": send.sent_at,
        "status": send.status,
        "subject": send.subject,
        "provider": send.provider,
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
        "recipient_email": send.recipient_email,
        "recipient_name": send.recipient_name,
        "to_emails": send.to_emails,
        "cc_emails": send.cc_emails,
        "bcc_emails": send.bcc_emails,
        "folder_id": send.folder_id,
        "folder_name": row[10],
    }
    if include_sender:
        payload["user_id"] = send.user_id
        payload["sender_name"] = row[11]
        # Prefer the point-in-time ``outreach_sends.sender_email``
        # column so admin sorting reflects the mailbox that actually
        # transmitted (not the login email, which can differ once a
        # user routes through a non-login linked account). Falls
        # back to the joined AuthUser.email for legacy rows where the
        # 0057 backfill is the only data we have.
        payload["sender_email"] = send.sender_email or row[12]
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
        # Hide soft-deleted sends from every read path: the list items,
        # the total count (its subquery is built from this select), and
        # the single-row detail lookup all derive from this builder.
        .where(OutreachSend.archived_at.is_(None))
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

    sender_account = await _resolve_sender_account(
        db=db,
        current_user=current_user,
        folder=folder,
        explicit_account_id=payload.sender_account_id,
    )
    audit = OutreachSend(
        user_id=current_user.id,
        advisor_id=payload.advisor_id,
        advisor_contact_id=payload.advisor_contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
        provider=sender_account.provider_id,
    )
    return await _provider_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        sender_account=sender_account,
        to_emails=[contact.email],
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

    sender_account = await _resolve_sender_account(
        db=db,
        current_user=current_user,
        folder=folder,
        explicit_account_id=payload.sender_account_id,
    )
    audit = OutreachSend(
        user_id=current_user.id,
        institutional_investor_id=payload.institutional_investor_id,
        investor_contact_id=payload.investor_contact_id,
        folder_id=folder.id,
        subject=payload.subject,
        body=payload.body,
        status="failed",
        provider=sender_account.provider_id,
    )
    return await _provider_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        sender_account=sender_account,
        to_emails=[contact.email],
        subject=payload.subject,
        body=payload.body,
    )


# ── Create-outreach tab: recipient autocomplete + adhoc send ──────────
# The new /outreach/sent?tab=create workspace needs (a) a way to search
# across all three contact tables to populate its recipient combobox,
# and (b) a send path that doesn't require the recipient to live in
# our contact tables at all -- free-form email entry.


@router.get("/contacts/search", response_model=RecipientSearchResponse)
async def search_outreach_contacts(
    q: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(20, ge=1, le=50),
    _: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> RecipientSearchResponse:
    """Partial-match search across BD / advisor / investor contacts.

    Returns rows whose contact ``name`` or ``email`` contains ``q``
    (case-insensitive). Firm-name matches are intentionally NOT
    surfaced here -- those go through the separate ``/firms/search``
    endpoint so the FE can render them as firm rows (one per firm)
    rather than per-contact rows. The FE calls both endpoints in
    parallel and renders the combined dropdown.

    Only includes contacts that have an email on file -- outreach
    can't go anywhere without one and the dropdown shouldn't tease
    un-pickable options.

    Three small queries (one per contact table) combined in Python.
    The volume is bounded by ``limit`` per kind so the merged result
    is at most 3 * limit before the final cap is applied; that keeps
    the SQL simple at the cost of one extra in-Python pass.
    """

    needle = f"%{q.strip().lower()}%"
    per_kind_limit = limit  # over-fetch per kind; trim after merge.

    exec_stmt = (
        select(
            ExecutiveContact.id.label("contact_id"),
            ExecutiveContact.name.label("contact_name"),
            ExecutiveContact.title.label("contact_title"),
            ExecutiveContact.email.label("contact_email"),
            BrokerDealer.id.label("entity_id"),
            BrokerDealer.name.label("entity_name"),
        )
        .join(BrokerDealer, BrokerDealer.id == ExecutiveContact.bd_id)
        .where(ExecutiveContact.email.isnot(None))
        .where(
            or_(
                func.lower(ExecutiveContact.name).like(needle),
                func.lower(ExecutiveContact.email).like(needle),
            )
        )
        .order_by(ExecutiveContact.name.asc())
        .limit(per_kind_limit)
    )
    advisor_stmt = (
        select(
            AdvisorContact.id.label("contact_id"),
            AdvisorContact.name.label("contact_name"),
            AdvisorContact.title.label("contact_title"),
            AdvisorContact.email.label("contact_email"),
            InvestmentAdvisor.id.label("entity_id"),
            InvestmentAdvisor.name.label("entity_name"),
        )
        .join(InvestmentAdvisor, InvestmentAdvisor.id == AdvisorContact.advisor_id)
        .where(AdvisorContact.email.isnot(None))
        .where(
            or_(
                func.lower(AdvisorContact.name).like(needle),
                func.lower(AdvisorContact.email).like(needle),
            )
        )
        .order_by(AdvisorContact.name.asc())
        .limit(per_kind_limit)
    )
    investor_stmt = (
        select(
            InvestorContact.id.label("contact_id"),
            InvestorContact.name.label("contact_name"),
            InvestorContact.title.label("contact_title"),
            InvestorContact.email.label("contact_email"),
            InstitutionalInvestor.id.label("entity_id"),
            InstitutionalInvestor.name.label("entity_name"),
        )
        .join(
            InstitutionalInvestor,
            InstitutionalInvestor.id == InvestorContact.investor_id,
        )
        .where(InvestorContact.email.isnot(None))
        .where(
            or_(
                func.lower(InvestorContact.name).like(needle),
                func.lower(InvestorContact.email).like(needle),
            )
        )
        .order_by(InvestorContact.name.asc())
        .limit(per_kind_limit)
    )

    results: list[RecipientSearchResult] = []
    for row in (await db.execute(exec_stmt)).all():
        results.append(
            RecipientSearchResult(
                entity_kind="broker_dealer",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_id=row.contact_id,
                contact_name=row.contact_name or "",
                contact_title=row.contact_title,
                contact_email=row.contact_email,
            )
        )
    for row in (await db.execute(advisor_stmt)).all():
        results.append(
            RecipientSearchResult(
                entity_kind="advisor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_id=row.contact_id,
                contact_name=row.contact_name or "",
                contact_title=row.contact_title,
                contact_email=row.contact_email,
            )
        )
    for row in (await db.execute(investor_stmt)).all():
        results.append(
            RecipientSearchResult(
                entity_kind="institutional_investor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_id=row.contact_id,
                contact_name=row.contact_name or "",
                contact_title=row.contact_title,
                contact_email=row.contact_email,
            )
        )

    # Stable sort by contact name across kinds, then trim to the
    # overall limit. Keeps results predictable for the FE's combobox.
    results.sort(key=lambda r: (r.contact_name.lower(), r.entity_kind))
    return RecipientSearchResponse(items=results[:limit])


@router.get("/firms/search", response_model=FirmSearchResponse)
async def search_outreach_firms(
    q: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(20, ge=1, le=50),
    _: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> FirmSearchResponse:
    """Partial-match firm-name search across BD / advisor / investor.

    Returns one row per firm whose name contains ``q``. Used by the
    create-outreach recipient autocomplete alongside
    ``/contacts/search``: contact-name hits go through the contact
    endpoint, firm-name hits land here. Firms with zero email-bearing
    contacts are excluded -- picking one would land in an empty modal.

    ``contact_count`` counts only contacts with a non-NULL email since
    that's what the modal will offer the user to send to.
    """

    needle = f"%{q.strip().lower()}%"
    per_kind_limit = limit

    # Sub-counts via correlated COUNT subqueries; one ROUND TRIP per
    # firm kind. Cheap because the outer LIKE is selective.
    bd_count = (
        select(func.count(ExecutiveContact.id))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .where(ExecutiveContact.email.isnot(None))
        .correlate(BrokerDealer)
        .scalar_subquery()
    )
    advisor_count = (
        select(func.count(AdvisorContact.id))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .where(AdvisorContact.email.isnot(None))
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )
    investor_count = (
        select(func.count(InvestorContact.id))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .where(InvestorContact.email.isnot(None))
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )

    bd_stmt = (
        select(
            BrokerDealer.id.label("entity_id"),
            BrokerDealer.name.label("entity_name"),
            bd_count.label("contact_count"),
        )
        .where(func.lower(BrokerDealer.name).like(needle))
        .where(bd_count > 0)
        .order_by(BrokerDealer.name.asc())
        .limit(per_kind_limit)
    )
    advisor_stmt = (
        select(
            InvestmentAdvisor.id.label("entity_id"),
            InvestmentAdvisor.name.label("entity_name"),
            advisor_count.label("contact_count"),
        )
        .where(func.lower(InvestmentAdvisor.name).like(needle))
        .where(advisor_count > 0)
        .order_by(InvestmentAdvisor.name.asc())
        .limit(per_kind_limit)
    )
    investor_stmt = (
        select(
            InstitutionalInvestor.id.label("entity_id"),
            InstitutionalInvestor.name.label("entity_name"),
            investor_count.label("contact_count"),
        )
        .where(func.lower(InstitutionalInvestor.name).like(needle))
        .where(investor_count > 0)
        .order_by(InstitutionalInvestor.name.asc())
        .limit(per_kind_limit)
    )

    results: list[FirmSearchResult] = []
    for row in (await db.execute(bd_stmt)).all():
        results.append(
            FirmSearchResult(
                entity_kind="broker_dealer",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )
    for row in (await db.execute(advisor_stmt)).all():
        results.append(
            FirmSearchResult(
                entity_kind="advisor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )
    for row in (await db.execute(investor_stmt)).all():
        results.append(
            FirmSearchResult(
                entity_kind="institutional_investor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )

    results.sort(key=lambda r: (r.entity_name.lower(), r.entity_kind))
    return FirmSearchResponse(items=results[:limit])


@router.get("/firms/contacts", response_model=FirmContactsResponse)
async def list_firm_contacts(
    entity_kind: Literal[
        "broker_dealer", "advisor", "institutional_investor"
    ] = Query(...),
    entity_id: int = Query(..., gt=0),
    _: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> FirmContactsResponse:
    """All email-bearing contacts at a single firm.

    Backs the "pick a contact" modal that opens when the user selects
    a firm row in the recipient autocomplete. The modal renders rows
    identically to how the autocomplete renders contact-hit rows;
    picking one wires the create-outreach form with the same
    contact-typed recipient as if the user had picked the contact
    directly in the autocomplete.
    """

    if entity_kind == "broker_dealer":
        firm_stmt = select(BrokerDealer).where(BrokerDealer.id == entity_id)
        firm = (await db.execute(firm_stmt)).scalar_one_or_none()
        if firm is None:
            raise HTTPException(
                status_code=404, detail="outreach_inputs_not_found"
            )
        contact_stmt = (
            select(
                ExecutiveContact.id.label("contact_id"),
                ExecutiveContact.name.label("contact_name"),
                ExecutiveContact.title.label("contact_title"),
                ExecutiveContact.email.label("contact_email"),
            )
            .where(ExecutiveContact.bd_id == entity_id)
            .where(ExecutiveContact.email.isnot(None))
            .order_by(ExecutiveContact.name.asc())
        )
    elif entity_kind == "advisor":
        firm_stmt = select(InvestmentAdvisor).where(
            InvestmentAdvisor.id == entity_id
        )
        firm = (await db.execute(firm_stmt)).scalar_one_or_none()
        if firm is None:
            raise HTTPException(
                status_code=404, detail="outreach_inputs_not_found"
            )
        contact_stmt = (
            select(
                AdvisorContact.id.label("contact_id"),
                AdvisorContact.name.label("contact_name"),
                AdvisorContact.title.label("contact_title"),
                AdvisorContact.email.label("contact_email"),
            )
            .where(AdvisorContact.advisor_id == entity_id)
            .where(AdvisorContact.email.isnot(None))
            .order_by(AdvisorContact.name.asc())
        )
    else:
        firm_stmt = select(InstitutionalInvestor).where(
            InstitutionalInvestor.id == entity_id
        )
        firm = (await db.execute(firm_stmt)).scalar_one_or_none()
        if firm is None:
            raise HTTPException(
                status_code=404, detail="outreach_inputs_not_found"
            )
        contact_stmt = (
            select(
                InvestorContact.id.label("contact_id"),
                InvestorContact.name.label("contact_name"),
                InvestorContact.title.label("contact_title"),
                InvestorContact.email.label("contact_email"),
            )
            .where(InvestorContact.investor_id == entity_id)
            .where(InvestorContact.email.isnot(None))
            .order_by(InvestorContact.name.asc())
        )

    items: list[RecipientSearchResult] = []
    for row in (await db.execute(contact_stmt)).all():
        items.append(
            RecipientSearchResult(
                entity_kind=entity_kind,
                entity_id=entity_id,
                entity_name=firm.name or "",
                contact_id=row.contact_id,
                contact_name=row.contact_name or "",
                contact_title=row.contact_title,
                contact_email=row.contact_email,
            )
        )

    return FirmContactsResponse(
        entity_kind=entity_kind,
        entity_id=entity_id,
        entity_name=firm.name or "",
        items=items,
    )


@router.get("/favorites/search", response_model=FavoriteSearchResponse)
async def search_outreach_favorites(
    q: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(20, ge=1, le=50),
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteSearchResponse:
    """Partial-match search across the caller's favorite lists.

    Returns user-owned lists whose name contains ``q``. ``firm_count``
    is the number of email-bearing-contact firms in the list -- a
    list with zero such firms is excluded since picking it would
    land in an empty drill-down modal. Reporting-owner items
    (insiders) don't have a contact table, so they're not counted.
    """

    needle = f"%{q.strip().lower()}%"

    # Per-list count of firms whose contacts have at least one email.
    # SUM of three EXISTS() per item, then SUM over items in the list.
    # Done in SQL for the count so we don't N+1 across each list's items.
    bd_has_email = (
        select(ExecutiveContact.id)
        .where(ExecutiveContact.bd_id == FavoriteListItem.broker_dealer_id)
        .where(ExecutiveContact.email.isnot(None))
        .exists()
    )
    advisor_has_email = (
        select(AdvisorContact.id)
        .where(AdvisorContact.advisor_id == FavoriteListItem.advisor_id)
        .where(AdvisorContact.email.isnot(None))
        .exists()
    )
    investor_has_email = (
        select(InvestorContact.id)
        .where(
            InvestorContact.investor_id == FavoriteListItem.institutional_investor_id
        )
        .where(InvestorContact.email.isnot(None))
        .exists()
    )

    firm_count_subq = (
        select(func.count(FavoriteListItem.id))
        .where(FavoriteListItem.list_id == FavoriteList.id)
        .where(
            or_(
                bd_has_email,
                advisor_has_email,
                investor_has_email,
            )
        )
        .correlate(FavoriteList)
        .scalar_subquery()
    )

    stmt = (
        select(
            FavoriteList.id.label("list_id"),
            FavoriteList.name.label("name"),
            firm_count_subq.label("firm_count"),
        )
        .where(FavoriteList.user_id == current_user.id)
        .where(func.lower(FavoriteList.name).like(needle))
        .where(firm_count_subq > 0)
        .order_by(FavoriteList.name.asc())
        .limit(limit)
    )

    items = [
        FavoriteSearchResult(
            list_id=row.list_id,
            name=row.name,
            firm_count=int(row.firm_count or 0),
        )
        for row in (await db.execute(stmt)).all()
    ]
    return FavoriteSearchResponse(items=items)


@router.get(
    "/favorites/{list_id}/firms", response_model=FavoriteFirmsResponse
)
async def list_favorite_firms(
    list_id: int,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteFirmsResponse:
    """Firms in a favorite list, filtered to those with email-bearing contacts.

    Drives the second view of the recipient drill-down modal. The user
    picks one of these firms; the modal then swaps to the firm-contacts
    view (``GET /outreach/firms/contacts``) for the chosen firm.
    """

    list_row = (
        await db.execute(
            select(FavoriteList).where(
                FavoriteList.id == list_id,
                FavoriteList.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if list_row is None:
        raise HTTPException(
            status_code=404, detail="outreach_inputs_not_found"
        )

    bd_count = (
        select(func.count(ExecutiveContact.id))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .where(ExecutiveContact.email.isnot(None))
        .correlate(BrokerDealer)
        .scalar_subquery()
    )
    advisor_count = (
        select(func.count(AdvisorContact.id))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .where(AdvisorContact.email.isnot(None))
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )
    investor_count = (
        select(func.count(InvestorContact.id))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .where(InvestorContact.email.isnot(None))
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )

    bd_stmt = (
        select(
            BrokerDealer.id.label("entity_id"),
            BrokerDealer.name.label("entity_name"),
            bd_count.label("contact_count"),
        )
        .join(FavoriteListItem, FavoriteListItem.broker_dealer_id == BrokerDealer.id)
        .where(FavoriteListItem.list_id == list_id)
        .where(bd_count > 0)
        .order_by(BrokerDealer.name.asc())
    )
    advisor_stmt = (
        select(
            InvestmentAdvisor.id.label("entity_id"),
            InvestmentAdvisor.name.label("entity_name"),
            advisor_count.label("contact_count"),
        )
        .join(
            FavoriteListItem,
            FavoriteListItem.advisor_id == InvestmentAdvisor.id,
        )
        .where(FavoriteListItem.list_id == list_id)
        .where(advisor_count > 0)
        .order_by(InvestmentAdvisor.name.asc())
    )
    investor_stmt = (
        select(
            InstitutionalInvestor.id.label("entity_id"),
            InstitutionalInvestor.name.label("entity_name"),
            investor_count.label("contact_count"),
        )
        .join(
            FavoriteListItem,
            FavoriteListItem.institutional_investor_id == InstitutionalInvestor.id,
        )
        .where(FavoriteListItem.list_id == list_id)
        .where(investor_count > 0)
        .order_by(InstitutionalInvestor.name.asc())
    )

    firms: list[FirmSearchResult] = []
    for row in (await db.execute(bd_stmt)).all():
        firms.append(
            FirmSearchResult(
                entity_kind="broker_dealer",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )
    for row in (await db.execute(advisor_stmt)).all():
        firms.append(
            FirmSearchResult(
                entity_kind="advisor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )
    for row in (await db.execute(investor_stmt)).all():
        firms.append(
            FirmSearchResult(
                entity_kind="institutional_investor",
                entity_id=row.entity_id,
                entity_name=row.entity_name or "",
                contact_count=int(row.contact_count or 0),
            )
        )

    firms.sort(key=lambda r: (r.entity_name.lower(), r.entity_kind))
    return FavoriteFirmsResponse(
        list_id=list_row.id, name=list_row.name, items=firms
    )


@router.post("/adhoc-send", response_model=OutreachSendResponse)
async def send_adhoc_outreach(
    payload: OutreachAdhocSendRequest,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendResponse:
    """Send outreach to a free-form email with no firm / contact context.

    Mirrors the BD / advisor / investor send paths but skips the
    contact-lookup leg -- the FE has already validated the recipient
    is an email address (or autocompleted from a contact, in which
    case the per-kind send endpoints handle it instead). Folder is
    optional: when provided, the sent-history table labels the row
    with the service name; when omitted, the row shows ``—``.

    Audit row leaves every firm / contact FK NULL and carries the
    address in ``recipient_email`` + ``recipient_name``. The XOR
    relaxation in migration 0063 permits the all-null FK state.
    """

    folder: VaultFolder | None = None
    if payload.folder_id is not None:
        folder = (
            await db.execute(
                select(VaultFolder).where(
                    VaultFolder.id == payload.folder_id,
                    VaultFolder.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if folder is None:
            raise HTTPException(
                status_code=404, detail="outreach_inputs_not_found"
            )

    sender_account = await _resolve_sender_account(
        db=db,
        current_user=current_user,
        folder=folder,
        explicit_account_id=payload.sender_account_id,
    )
    audit = OutreachSend(
        user_id=current_user.id,
        folder_id=folder.id if folder else None,
        subject=payload.subject,
        body=payload.body,
        status="failed",
        provider=sender_account.provider_id,
        recipient_email=payload.recipient_email,
        recipient_name=payload.recipient_name,
    )
    return await _provider_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        sender_account=sender_account,
        to_emails=[payload.recipient_email],
        subject=payload.subject,
        body=payload.body,
    )


def _dedupe_compose_recipients(
    payload: OutreachComposeSendRequest,
) -> tuple[list[str], list[str], list[str]]:
    """Normalise + de-duplicate To/CC/BCC across all three buckets.

    An address lands in at most one bucket — earliest wins in To > CC >
    BCC order, matched case-insensitively — so nobody is double-addressed
    (and a BCC recipient can't be silently un-hidden by also sitting in
    To). Within a bucket the first occurrence's original casing is kept
    and order is preserved.
    """

    seen: set[str] = set()

    def _collect(addresses: list[str]) -> list[str]:
        out: list[str] = []
        for raw in addresses:
            addr = str(raw).strip()
            key = addr.lower()
            if key and key not in seen:
                seen.add(key)
                out.append(addr)
        return out

    to_emails = _collect([r.email for r in payload.to])
    cc_emails = _collect(list(payload.cc))
    bcc_emails = _collect(list(payload.bcc))
    return to_emails, cc_emails, bcc_emails


@router.post("/compose-send", response_model=OutreachSendResponse)
async def send_compose_outreach(
    payload: OutreachComposeSendRequest,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSendResponse:
    """Send one email to multiple recipients with CC/BCC (the composer).

    Mirrors the adhoc-send path but carries a full To list plus optional
    CC and BCC. A single message is transmitted — To/CC recipients see
    each other; BCC is delivered hidden — and audited as one adhoc-style
    ``outreach_sends`` row (no firm/contact FK) carrying the recipient
    lists. Folder is optional service metadata, same as adhoc-send.
    """

    to_emails, cc_emails, bcc_emails = _dedupe_compose_recipients(payload)
    if not to_emails:
        # Pydantic guarantees >= 1 To, but dedupe could in theory empty it
        # if every entry collided — guard so we never send to nobody.
        raise HTTPException(status_code=422, detail="recipient_required")

    folder: VaultFolder | None = None
    if payload.folder_id is not None:
        folder = (
            await db.execute(
                select(VaultFolder).where(
                    VaultFolder.id == payload.folder_id,
                    VaultFolder.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if folder is None:
            raise HTTPException(
                status_code=404, detail="outreach_inputs_not_found"
            )

    sender_account = await _resolve_sender_account(
        db=db,
        current_user=current_user,
        folder=folder,
        explicit_account_id=payload.sender_account_id,
    )
    # Primary To powers the sent-history "recipient" column; the full
    # lists land on to_emails/cc_emails/bcc_emails inside the send helper.
    primary = payload.to[0]
    audit = OutreachSend(
        user_id=current_user.id,
        folder_id=folder.id if folder else None,
        subject=payload.subject,
        body=payload.body,
        status="failed",
        provider=sender_account.provider_id,
        recipient_email=to_emails[0],
        recipient_name=primary.name,
    )
    return await _provider_send_and_record(
        db=db,
        current_user=current_user,
        audit=audit,
        sender_account=sender_account,
        to_emails=to_emails,
        subject=payload.subject,
        body=payload.body,
        cc_emails=cc_emails or None,
        bcc_emails=bcc_emails or None,
    )


async def _resolve_sender_account(
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


async def _provider_send_and_record(
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

    The caller resolves the sender account via :func:`_resolve_sender_account`
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
        await _record_failure(db, audit, "unknown_provider")
        raise HTTPException(status_code=400, detail="unknown_provider")

    not_linked_code = f"{provider_id}_account_not_linked"
    not_configured_code = f"{provider_id}_oauth_not_configured"
    scope_required_code = _SCOPE_ERROR_CODE[provider_id]
    api_error_code = _API_ERROR_CODE[provider_id]

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
        await _record_failure(db, audit, not_linked_code)
        raise HTTPException(
            status_code=412, detail=not_linked_code
        ) from exc
    except EmailProviderConfigurationError as exc:
        logger.error(
            "Provider %s OAuth not configured: %s", provider_id, exc
        )
        await _record_failure(db, audit, not_configured_code)
        raise HTTPException(
            status_code=503, detail=not_configured_code
        ) from exc

    if provider.send_scope not in scopes:
        await _record_failure(db, audit, scope_required_code)
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
        await _record_failure(db, audit, scope_required_code)
        raise HTTPException(
            status_code=412, detail=scope_required_code
        ) from exc
    except EmailSendError as exc:
        logger.warning("%s send failed: %s", provider_id, exc)
        await _record_failure(db, audit, api_error_code)
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


@router.get("/sends", response_model=OutreachSendsListResponse)
async def list_outreach_sends(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Literal["sent", "failed"] | None = Query(
        None, alias="status"
    ),
    scope: Literal["mine", "all"] = Query("mine"),
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
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
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
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


@router.delete("/sends/{send_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outreach_send(
    send_id: int,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Soft-delete one of the caller's OWN sends (stamps ``archived_at``).

    Owner-only: even an admin can only delete rows they sent — there is no
    ``scope=all`` override here (unlike the read endpoints). Returns 404 for
    both "id does not exist" and "id belongs to another user" so a leaked id
    can't confirm cross-user existence. The row is retained for audit; every
    read path filters ``archived_at IS NULL``, so it just disappears from the
    Sent-history list. Re-deleting an already-archived row is idempotent (204).

    Uses a plain ``select(OutreachSend)`` rather than ``_base_send_select``
    (which now hides archived rows) so a repeat delete can still find the row
    to no-op on.
    """
    send = (
        await db.execute(
            select(OutreachSend).where(
                OutreachSend.id == send_id,
                OutreachSend.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if send is None:
        raise HTTPException(status_code=404, detail="outreach_send_not_found")

    if send.archived_at is None:
        send.archived_at = datetime.now(timezone.utc)
        await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Outreach drafts ───────────────────────────────────────────────────
# Saved-but-unsent composer drafts (the Outreach "Drafts" tab). A draft is
# the mutable counterpart to the immutable outreach_sends audit row: the
# Create-Outreach composer saves an in-progress To/Cc/Bcc message here, and
# Doxie-authored emails land here (source="doxie") for human review. Drafts
# are private to their owner and hard-deleted on send/discard — there's no
# audit-retention need (the send itself is recorded in outreach_sends).
# "Sending a draft" is the FE loading it back into the composer and POSTing
# /compose-send, so this is pure CRUD — no send endpoint lives here.


async def _validate_draft_folder(
    *,
    folder_id: int | None,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> None:
    """404 if a folder_id is supplied that the caller doesn't own.

    ``None`` (no service picked) is always allowed — mirrors the
    optional-folder rule on the adhoc / compose send paths.
    """
    if folder_id is None:
        return
    owns = (
        await db.execute(
            select(VaultFolder.id).where(
                VaultFolder.id == folder_id,
                VaultFolder.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if owns is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")


async def _folder_name_for(db: AsyncSession, folder_id: int | None) -> str | None:
    """The folder's display name, or None when no folder is set."""
    if folder_id is None:
        return None
    return (
        await db.execute(
            select(VaultFolder.name).where(VaultFolder.id == folder_id)
        )
    ).scalar_one_or_none()


def _apply_draft_fields(
    draft: OutreachDraft, payload: OutreachDraftSaveRequest
) -> None:
    """Copy a save request onto a draft row.

    Empty subject/body and empty recipient lists store as NULL so the DB
    stays tidy; the read path normalizes them back to "" / []. Recipients
    are stored as the compose-shaped dicts so they round-trip 1:1.
    """
    draft.subject = payload.subject or None
    draft.body = payload.body or None
    draft.to_recipients = [r.model_dump() for r in payload.to] or None
    draft.cc_emails = list(payload.cc) or None
    draft.bcc_emails = list(payload.bcc) or None
    draft.sender_account_id = payload.sender_account_id
    draft.folder_id = payload.folder_id
    draft.source = payload.source


def _draft_payload(draft: OutreachDraft, folder_name: str | None) -> dict:
    """Flatten a draft row (+ joined folder name) into the dict the item and
    detail response models consume. Recipients are projected back into the
    compose-shaped to/cc/bcc the composer hydrates from."""
    to = [
        OutreachDraftRecipient(email=str(r.get("email", "")), name=r.get("name"))
        for r in (draft.to_recipients or [])
        if isinstance(r, dict)
    ]
    return {
        "id": draft.id,
        "subject": draft.subject or "",
        "to": to,
        "cc": [str(x) for x in (draft.cc_emails or [])],
        "bcc": [str(x) for x in (draft.bcc_emails or [])],
        "folder_id": draft.folder_id,
        "folder_name": folder_name,
        "sender_account_id": draft.sender_account_id,
        "source": draft.source,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


async def _load_owned_draft(
    *,
    draft_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
) -> tuple[OutreachDraft, str | None]:
    """Fetch one of the caller's own drafts + its folder name, or 404.

    404 for both "doesn't exist" and "belongs to another user" so a leaked
    id can't confirm cross-user existence — same opacity as the sends paths.
    """
    row = (
        await db.execute(
            select(OutreachDraft, VaultFolder.name)
            .outerjoin(VaultFolder, VaultFolder.id == OutreachDraft.folder_id)
            .where(
                OutreachDraft.id == draft_id,
                OutreachDraft.user_id == current_user.id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="outreach_draft_not_found")
    return row[0], row[1]


@router.post(
    "/drafts",
    response_model=OutreachDraftDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_outreach_draft_record(
    payload: OutreachDraftSaveRequest,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftDetail:
    """Save a new composer draft for the caller. Returns the stored draft."""
    await _validate_draft_folder(
        folder_id=payload.folder_id, current_user=current_user, db=db
    )
    draft = OutreachDraft(user_id=current_user.id)
    _apply_draft_fields(draft, payload)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    folder_name = await _folder_name_for(db, draft.folder_id)
    return OutreachDraftDetail(
        **_draft_payload(draft, folder_name), body=draft.body or ""
    )


@router.get("/drafts", response_model=OutreachDraftsListResponse)
async def list_outreach_drafts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftsListResponse:
    """The caller's drafts, newest-edited first. Body is omitted from the
    list rows (fetch via ``GET /outreach/drafts/{id}`` on open)."""
    base = (
        select(OutreachDraft, VaultFolder.name)
        .outerjoin(VaultFolder, VaultFolder.id == OutreachDraft.folder_id)
        .where(OutreachDraft.user_id == current_user.id)
    )
    count_stmt = select(func.count()).select_from(
        select(OutreachDraft.id)
        .where(OutreachDraft.user_id == current_user.id)
        .subquery()
    )
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        await db.execute(
            base.order_by(OutreachDraft.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [OutreachDraftItem(**_draft_payload(row[0], row[1])) for row in rows]
    return OutreachDraftsListResponse(
        items=items, total=total, limit=limit, offset=offset
    )


@router.get("/drafts/{draft_id}", response_model=OutreachDraftDetail)
async def get_outreach_draft(
    draft_id: int,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftDetail:
    """Full draft (incl. body) for the caller to resume editing."""
    draft, folder_name = await _load_owned_draft(
        draft_id=draft_id, current_user=current_user, db=db
    )
    return OutreachDraftDetail(
        **_draft_payload(draft, folder_name), body=draft.body or ""
    )


@router.put("/drafts/{draft_id}", response_model=OutreachDraftDetail)
async def update_outreach_draft(
    draft_id: int,
    payload: OutreachDraftSaveRequest,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftDetail:
    """Overwrite one of the caller's own drafts with the composer's state."""
    draft, _ = await _load_owned_draft(
        draft_id=draft_id, current_user=current_user, db=db
    )
    await _validate_draft_folder(
        folder_id=payload.folder_id, current_user=current_user, db=db
    )
    _apply_draft_fields(draft, payload)
    await db.commit()
    await db.refresh(draft)
    folder_name = await _folder_name_for(db, draft.folder_id)
    return OutreachDraftDetail(
        **_draft_payload(draft, folder_name), body=draft.body or ""
    )


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outreach_draft(
    draft_id: int,
    current_user: AuthenticatedUser = Depends(_require_sent_outreach),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Hard-delete one of the caller's own drafts.

    Owner-only: 404 for both "doesn't exist" and "belongs to another user".
    Unlike sends, drafts carry no audit value, so this is a real delete.
    """
    draft = (
        await db.execute(
            select(OutreachDraft).where(
                OutreachDraft.id == draft_id,
                OutreachDraft.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="outreach_draft_not_found")
    await db.delete(draft)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/signature", response_model=OutreachSignatureResponse)
async def get_outreach_signature(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSignatureResponse:
    """The caller's saved outreach signature (footer), or "" if unset.

    Read by the compose surfaces (per-contact Outreach modal + the
    ``/outreach`` Create tab) to prefill the editable Footer field, and
    by the account-settings editor. Gated on ``get_current_user`` only —
    it's the caller's own row, and the settings editor must work for any
    signed-in user regardless of outreach feature permissions.
    """
    row = await db.get(UserOutreachSettings, current_user.id)
    return OutreachSignatureResponse(
        signature=row.signature if row and row.signature else ""
    )


@router.put("/signature", response_model=OutreachSignatureResponse)
async def update_outreach_signature(
    payload: OutreachSignatureUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachSignatureResponse:
    """Upsert the caller's outreach signature. Empty string clears it.

    One row per user keyed by ``user_id`` (PK), so this is a get-or-create
    followed by an in-place update — no race on a unique constraint.
    """
    signature = payload.signature.strip()
    row = await db.get(UserOutreachSettings, current_user.id)
    if row is None:
        row = UserOutreachSettings(user_id=current_user.id, signature=signature)
        db.add(row)
    else:
        row.signature = signature
    await db.commit()
    return OutreachSignatureResponse(signature=signature)


@router.get(
    "/linked-providers", response_model=LinkedProvidersResponse
)
async def list_linked_providers(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> LinkedProvidersResponse:
    """Linked OAuth accounts for the calling user, one entry per account.

    Used by the Outreach modal's "Send from" dropdown — a user with
    two Google accounts plus an Outlook account gets three entries,
    not three provider types. Each entry includes the bound mailbox
    (``email_address``, captured on link via the FE post-link hook)
    so the picker can label rows by address instead of provider name.

    Lazy-backfills ``email_address`` from the stored ``id_token`` for
    rows linked before the post-link hook existed -- the modal needs
    a label even for legacy accounts. Rows ordered by ``linked_at``
    ascending so the modal can pick a deterministic default.
    """

    stmt = (
        select(Account)
        .where(Account.user_id == current_user.id)
        .where(Account.provider_id.in_(("google", "microsoft", "yahoo")))
        .order_by(Account.created_at.asc())
    )
    accounts = (await db.execute(stmt)).scalars().all()

    dirty = False
    items: list[LinkedProviderItem] = []
    for account in accounts:
        provider = PROVIDERS.get(account.provider_id)
        if provider is None:
            continue
        scopes = (account.scope or "").replace(",", " ").split()
        has_send_scope = provider.send_scope in scopes
        if account.email_address is None:
            email = extract_email_from_id_token(
                account.provider_id, account.id_token
            )
            if email:
                account.email_address = email
                dirty = True
        items.append(
            LinkedProviderItem(
                account_id=account.id,
                provider_account_id=account.account_id,
                provider=account.provider_id,  # type: ignore[arg-type]
                email_address=account.email_address,
                scope=account.scope,
                has_send_scope=has_send_scope,
                linked_at=account.created_at,
            )
        )
    if dirty:
        await db.commit()
    return LinkedProvidersResponse(items=items)


# --------------------------------------------------------------------------- #
# Outreach Contacts page (`/outreach/contacts`)                               #
# --------------------------------------------------------------------------- #
#
# The endpoints below back the persons-by-firm browse + per-firm gap-fill
# loop. Distinct namespace and feature-gate from the send-flow endpoints
# above because the gap-fill button fires expensive provider chains
# (Apollo / PDL / Hunter / Snov) and operators may want to control that
# spend independently of the send surface.

# DEMO: retained but currently unenforced -- the cooldown gate in
# dispatch_firm_gap_fill was removed for the client demo (see the note there).
# Kept so restoring production gating is a one-line revert.
_GAP_FILL_COOLDOWN_DAYS = 30

_GAP_FILL_PIPELINE_NAMES: tuple[str, ...] = (
    GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME,
    GAP_FILL_BD_CONTACTS_PIPELINE_NAME,
    GAP_FILL_II_CONTACTS_PIPELINE_NAME,
)

_ENTITY_KIND_MARKER_KEY: dict[str, str] = {
    "broker_dealer": "bd_id",
    "advisor": "advisor_id",
    "institutional_investor": "investor_id",
}


def _require_outreach_contacts(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    ensure_feature(user, OUTREACH_CONTACTS)
    return user


def _is_recent_pipeline_run(started_at: datetime | None) -> bool:
    """True if a PipelineRun started recently enough to still be considered
    alive. Anything older than ``STALE_REFRESH_RUN_AGE`` is presumed orphaned
    by an instance swap and must not be treated as in-flight."""
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at >= datetime.now(timezone.utc) - STALE_REFRESH_RUN_AGE


async def _in_flight_firm_keys(
    db: AsyncSession,
) -> set[tuple[str, int]]:
    """Set of (entity_kind, entity_id) pairs that currently have a gap-fill
    PipelineRun in ``queued`` or ``running`` state. Used to compute the
    ``gap_fill_in_progress`` flag on the firms-list response without an
    N+1 over the per-firm rows."""
    stmt = (
        select(PipelineRun.pipeline_name, PipelineRun.notes, PipelineRun.started_at)
        .where(PipelineRun.pipeline_name.in_(_GAP_FILL_PIPELINE_NAMES))
        .where(PipelineRun.status.in_(("queued", "running")))
    )
    keys: set[tuple[str, int]] = set()
    for pipeline_name, notes, started_at in (await db.execute(stmt)).all():
        if not _is_recent_pipeline_run(started_at):
            continue
        try:
            payload = json.loads(notes) if notes else {}
        except json.JSONDecodeError:
            continue
        if pipeline_name == GAP_FILL_BD_CONTACTS_PIPELINE_NAME:
            kind = "broker_dealer"
            marker = payload.get("bd_id")
        elif pipeline_name == GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME:
            kind = "advisor"
            marker = payload.get("advisor_id")
        else:
            kind = "institutional_investor"
            marker = payload.get("investor_id")
        if isinstance(marker, int):
            keys.add((kind, marker))
    return keys


@router.get(
    "/contacts/firms",
    response_model=OutreachContactsFirmsResponse,
)
async def list_outreach_contacts_firms(
    entity_kind: Literal[
        "broker_dealer", "advisor", "institutional_investor"
    ]
    | None = Query(None),
    q: str | None = Query(None, max_length=255),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    _: AuthenticatedUser = Depends(_require_outreach_contacts),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachContactsFirmsResponse:
    """Paginated list of firms (BD + advisor + II) with at least one
    contact on file.

    UNIONs three per-kind SELECTs, each with correlated COUNT subqueries
    on the matching contact table. Drops the ``email IS NOT NULL`` filter
    used by the send-flow ``/firms/search`` endpoint because phone-only
    contacts also count here -- the new page surfaces persons regardless
    of channel.

    ``gap_fill_in_progress`` is resolved by a single auxiliary scan over
    ``pipeline_runs`` for in-flight gap-fill jobs, joined in Python.
    Cheaper than three correlated EXISTS subqueries on the outer UNION.
    """
    needle = f"%{(q or '').strip().lower()}%" if q else None

    bd_contact_count = (
        select(func.count(ExecutiveContact.id))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .correlate(BrokerDealer)
        .scalar_subquery()
    )
    bd_email_count = (
        select(func.count(ExecutiveContact.id))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .where(ExecutiveContact.email.isnot(None))
        .correlate(BrokerDealer)
        .scalar_subquery()
    )
    bd_phone_count = (
        select(func.count(ExecutiveContact.id))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .where(ExecutiveContact.phone.isnot(None))
        .correlate(BrokerDealer)
        .scalar_subquery()
    )
    bd_last_enriched = (
        select(func.max(ExecutiveContact.enriched_at))
        .where(ExecutiveContact.bd_id == BrokerDealer.id)
        .correlate(BrokerDealer)
        .scalar_subquery()
    )

    advisor_contact_count = (
        select(func.count(AdvisorContact.id))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )
    advisor_email_count = (
        select(func.count(AdvisorContact.id))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .where(AdvisorContact.email.isnot(None))
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )
    advisor_phone_count = (
        select(func.count(AdvisorContact.id))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .where(AdvisorContact.phone.isnot(None))
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )
    advisor_last_enriched = (
        select(func.max(AdvisorContact.enriched_at))
        .where(AdvisorContact.advisor_id == InvestmentAdvisor.id)
        .correlate(InvestmentAdvisor)
        .scalar_subquery()
    )

    investor_contact_count = (
        select(func.count(InvestorContact.id))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )
    investor_email_count = (
        select(func.count(InvestorContact.id))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .where(InvestorContact.email.isnot(None))
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )
    investor_phone_count = (
        select(func.count(InvestorContact.id))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .where(InvestorContact.phone.isnot(None))
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )
    investor_last_enriched = (
        select(func.max(InvestorContact.enriched_at))
        .where(InvestorContact.investor_id == InstitutionalInvestor.id)
        .correlate(InstitutionalInvestor)
        .scalar_subquery()
    )

    rows: list[OutreachContactsFirmRow] = []

    if entity_kind in (None, "broker_dealer"):
        bd_stmt = (
            select(
                BrokerDealer.id,
                BrokerDealer.name,
                bd_contact_count.label("contact_count"),
                bd_email_count.label("with_email_count"),
                bd_phone_count.label("with_phone_count"),
                bd_last_enriched.label("last_enriched_at"),
                BrokerDealer.last_gap_fill_attempt_at,
            )
            .where(bd_contact_count > 0)
            .order_by(BrokerDealer.name.asc())
        )
        if needle:
            bd_stmt = bd_stmt.where(func.lower(BrokerDealer.name).like(needle))
        for r in (await db.execute(bd_stmt)).all():
            rows.append(
                OutreachContactsFirmRow(
                    entity_kind="broker_dealer",
                    entity_id=r.id,
                    name=r.name or "",
                    contact_count=int(r.contact_count or 0),
                    with_email_count=int(r.with_email_count or 0),
                    with_phone_count=int(r.with_phone_count or 0),
                    last_enriched_at=r.last_enriched_at,
                    last_gap_fill_attempt_at=r.last_gap_fill_attempt_at,
                    gap_fill_in_progress=False,
                )
            )

    if entity_kind in (None, "advisor"):
        advisor_stmt = (
            select(
                InvestmentAdvisor.id,
                InvestmentAdvisor.name,
                advisor_contact_count.label("contact_count"),
                advisor_email_count.label("with_email_count"),
                advisor_phone_count.label("with_phone_count"),
                advisor_last_enriched.label("last_enriched_at"),
                InvestmentAdvisor.last_gap_fill_attempt_at,
            )
            .where(advisor_contact_count > 0)
            .order_by(InvestmentAdvisor.name.asc())
        )
        if needle:
            advisor_stmt = advisor_stmt.where(
                func.lower(InvestmentAdvisor.name).like(needle)
            )
        for r in (await db.execute(advisor_stmt)).all():
            rows.append(
                OutreachContactsFirmRow(
                    entity_kind="advisor",
                    entity_id=r.id,
                    name=r.name or "",
                    contact_count=int(r.contact_count or 0),
                    with_email_count=int(r.with_email_count or 0),
                    with_phone_count=int(r.with_phone_count or 0),
                    last_enriched_at=r.last_enriched_at,
                    last_gap_fill_attempt_at=r.last_gap_fill_attempt_at,
                    gap_fill_in_progress=False,
                )
            )

    if entity_kind in (None, "institutional_investor"):
        investor_stmt = (
            select(
                InstitutionalInvestor.id,
                InstitutionalInvestor.name,
                investor_contact_count.label("contact_count"),
                investor_email_count.label("with_email_count"),
                investor_phone_count.label("with_phone_count"),
                investor_last_enriched.label("last_enriched_at"),
                InstitutionalInvestor.last_gap_fill_attempt_at,
            )
            .where(investor_contact_count > 0)
            .order_by(InstitutionalInvestor.name.asc())
        )
        if needle:
            investor_stmt = investor_stmt.where(
                func.lower(InstitutionalInvestor.name).like(needle)
            )
        for r in (await db.execute(investor_stmt)).all():
            rows.append(
                OutreachContactsFirmRow(
                    entity_kind="institutional_investor",
                    entity_id=r.id,
                    name=r.name or "",
                    contact_count=int(r.contact_count or 0),
                    with_email_count=int(r.with_email_count or 0),
                    with_phone_count=int(r.with_phone_count or 0),
                    last_enriched_at=r.last_enriched_at,
                    last_gap_fill_attempt_at=r.last_gap_fill_attempt_at,
                    gap_fill_in_progress=False,
                )
            )

    rows.sort(key=lambda r: (r.name.lower(), r.entity_kind))
    total = len(rows)
    start = (page - 1) * limit
    paged = rows[start : start + limit]

    # Attach gap_fill_in_progress only to rows in the current page.
    if paged:
        in_flight = await _in_flight_firm_keys(db)
        for r in paged:
            if (r.entity_kind, r.entity_id) in in_flight:
                r.gap_fill_in_progress = True

    return OutreachContactsFirmsResponse(items=paged, total=total)


@router.get(
    "/contacts/firms/{entity_kind}/{entity_id}/persons",
    response_model=OutreachContactsFirmDetailResponse,
)
async def list_outreach_contacts_firm_persons(
    entity_kind: Literal[
        "broker_dealer", "advisor", "institutional_investor"
    ],
    entity_id: int,
    _: AuthenticatedUser = Depends(_require_outreach_contacts),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachContactsFirmDetailResponse:
    """All contacts at a single firm, regardless of channel availability.

    Unlike ``/firms/contacts`` (the send-flow endpoint, which filters to
    email-bearing rows), this one returns every contact on file with the
    full shape (email, phone, linkedin_url, enriched_at) so the persons
    accordion can show e.g. a phone-only contact for a firm whose email
    discovery missed.
    """
    if entity_kind == "broker_dealer":
        firm = await db.get(BrokerDealer, entity_id)
        if firm is None:
            raise HTTPException(status_code=404, detail="firm_not_found")
        contact_stmt = (
            select(
                ExecutiveContact.id,
                ExecutiveContact.name,
                ExecutiveContact.title,
                ExecutiveContact.email,
                ExecutiveContact.phone,
                ExecutiveContact.linkedin_url,
                ExecutiveContact.enriched_at,
            )
            .where(ExecutiveContact.bd_id == entity_id)
            .order_by(ExecutiveContact.name.asc())
        )
    elif entity_kind == "advisor":
        firm = await db.get(InvestmentAdvisor, entity_id)
        if firm is None:
            raise HTTPException(status_code=404, detail="firm_not_found")
        contact_stmt = (
            select(
                AdvisorContact.id,
                AdvisorContact.name,
                AdvisorContact.title,
                AdvisorContact.email,
                AdvisorContact.phone,
                AdvisorContact.linkedin_url,
                AdvisorContact.enriched_at,
            )
            .where(AdvisorContact.advisor_id == entity_id)
            .order_by(AdvisorContact.name.asc())
        )
    else:
        firm = await db.get(InstitutionalInvestor, entity_id)
        if firm is None:
            raise HTTPException(status_code=404, detail="firm_not_found")
        contact_stmt = (
            select(
                InvestorContact.id,
                InvestorContact.name,
                InvestorContact.title,
                InvestorContact.email,
                InvestorContact.phone,
                InvestorContact.linkedin_url,
                InvestorContact.enriched_at,
            )
            .where(InvestorContact.investor_id == entity_id)
            .order_by(InvestorContact.name.asc())
        )

    items: list[OutreachContactPerson] = []
    for r in (await db.execute(contact_stmt)).all():
        items.append(
            OutreachContactPerson(
                contact_id=r.id,
                name=r.name or "",
                title=r.title,
                email=r.email,
                phone=r.phone,
                linkedin_url=r.linkedin_url,
                enriched_at=r.enriched_at,
            )
        )

    return OutreachContactsFirmDetailResponse(
        entity_kind=entity_kind,
        entity_id=entity_id,
        entity_name=firm.name or "",
        items=items,
    )


@router.post(
    "/contacts/firms/{entity_kind}/{entity_id}/gap-fill",
    response_model=GapFillFirmResponse,
)
async def dispatch_firm_gap_fill(
    entity_kind: Literal[
        "broker_dealer", "advisor", "institutional_investor"
    ],
    entity_id: int,
    background_tasks: BackgroundTasks,
    response: Response,
    current_user: AuthenticatedUser = Depends(_require_outreach_contacts),
    db: AsyncSession = Depends(get_db_session),
) -> GapFillFirmResponse:
    """Trigger a per-firm contact gap-fill across any of the three entity
    kinds. Returns 202 + run_id for FE polling against
    ``/pipeline-runs/{id}``.

    Coexists with the legacy per-kind endpoints
    (``/broker-dealers/{id}/enrich`` -- full-replace path,
    ``/investment-advisors/{id}/gap-fill-contacts`` -- single-firm route
    used by the advisor detail page). The 30-day ``last_gap_fill_attempt_at``
    cooldown is independent of the legacy BD ``last_enrich_attempt_at``
    cooldown -- a user can gap-fill 24h after a fresh ``/enrich`` since the
    point of gap-fill is to retry channels the initial enrichment missed.
    """
    # Load the parent row + map the kind to its runner inputs.
    if entity_kind == "broker_dealer":
        firm: BrokerDealer | InvestmentAdvisor | InstitutionalInvestor | None = (
            await db.get(BrokerDealer, entity_id)
        )
        pipeline_name = GAP_FILL_BD_CONTACTS_PIPELINE_NAME
        marker_key = "bd_id"
        background_runner = run_gap_fill_bd_contacts_background
    elif entity_kind == "advisor":
        firm = await db.get(InvestmentAdvisor, entity_id)
        pipeline_name = GAP_FILL_ADVISOR_CONTACTS_PIPELINE_NAME
        marker_key = "advisor_id"
        background_runner = run_gap_fill_advisor_contacts_background
    else:
        firm = await db.get(InstitutionalInvestor, entity_id)
        pipeline_name = GAP_FILL_II_CONTACTS_PIPELINE_NAME
        marker_key = "investor_id"
        background_runner = run_gap_fill_ii_contacts_background

    if firm is None:
        raise HTTPException(status_code=404, detail="firm_not_found")

    # DEMO: the 30-day gap-fill cooldown gate is removed so "Enrich all" on the
    # /outreach/contacts page is always actionable for the client demo (mirrors
    # the /investors always-enabled override). Re-enrichment now fires
    # regardless of last_gap_fill_attempt_at. The in-flight guard below still
    # prevents duplicate concurrent runs for the same firm. To restore
    # production gating, re-instate the block that compared
    # firm.last_gap_fill_attempt_at against a _GAP_FILL_COOLDOWN_DAYS cutoff and
    # raised HTTP 429 here, and revert the matching FE gate in
    # components/outreach-contacts/firm-row.tsx + the dispatch cooldown test.

    marker = f'"{marker_key}": {entity_id}'
    in_flight_stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == pipeline_name)
        .where(PipelineRun.status.in_(("queued", "running")))
        .where(PipelineRun.notes.ilike(f"%{marker}%"))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    in_flight = (await db.execute(in_flight_stmt)).scalar_one_or_none()
    if in_flight is not None and _is_recent_pipeline_run(in_flight.started_at):
        response.status_code = status.HTTP_202_ACCEPTED
        return GapFillFirmResponse(
            run_id=in_flight.id,
            status="in_flight",
            entity_kind=entity_kind,
            entity_id=entity_id,
            reason="A gap-fill run is already in flight for this firm.",
        )

    trigger_source = f"outreach_contacts_gap_fill:{current_user.email}"
    run = PipelineRun(
        pipeline_name=pipeline_name,
        trigger_source=trigger_source,
        status="queued",
        total_items=1,
        processed_items=0,
        success_count=0,
        failure_count=0,
        notes=json.dumps({marker_key: entity_id, "stage": "queued"}),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(background_runner, run.id, entity_id, trigger_source)

    response.status_code = status.HTTP_202_ACCEPTED
    return GapFillFirmResponse(
        run_id=run.id,
        status=run.status,
        entity_kind=entity_kind,
        entity_id=entity_id,
        reason=None,
    )
