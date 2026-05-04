"""Endpoint for the Outreach modal's "Generate Draft" button.

Single ``POST /outreach/draft`` route. The handler verifies the caller
owns the requested vault folder, the broker-dealer + executive contact
exist (and the contact belongs to that BD), then calls
``services.outreach.generate_outreach_draft`` and returns the
``{subject, body}`` payload. Draft-only — no send path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.vault import OutreachDraftRequest, OutreachDraftResponse
from app.services.auth import get_current_user
from app.services.outreach import (
    ContactContext,
    FirmContext,
    OutreachConfigurationError,
    OutreachDraftError,
    ServiceContext,
    generate_outreach_draft,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach")


@router.post("/draft", response_model=OutreachDraftResponse)
async def create_outreach_draft(
    payload: OutreachDraftRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> OutreachDraftResponse:
    """Generate a cold-email draft for a (firm, contact, service) tuple.

    All three validation failures (folder not yours, BD missing, contact
    missing-or-wrong-firm) collapse to ``404 outreach_inputs_not_found`` so
    a leaked id can't confirm "this folder/contact exists". Misconfigured
    Gemini key is a 503; any other Gemini failure is a 502.
    """
    folder_stmt = select(VaultFolder).where(
        VaultFolder.id == payload.folder_id,
        VaultFolder.user_id == current_user.id,
    )
    folder = (await db.execute(folder_stmt)).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    bd_stmt = select(BrokerDealer).where(BrokerDealer.id == payload.broker_dealer_id)
    broker_dealer = (await db.execute(bd_stmt)).scalar_one_or_none()
    if broker_dealer is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

    contact_stmt = select(ExecutiveContact).where(
        ExecutiveContact.id == payload.contact_id,
        ExecutiveContact.bd_id == payload.broker_dealer_id,
    )
    contact = (await db.execute(contact_stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="outreach_inputs_not_found")

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
    service_ctx = ServiceContext(
        name=folder.name,
        description=folder.description,
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
