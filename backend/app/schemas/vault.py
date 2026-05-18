"""Pydantic schemas for the Vault + Outreach feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VaultFolderResponse(BaseModel):
    """A single Vault folder owned by the authenticated user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    # Permanent per-service prompt guidance — fed verbatim into Gemini on
    # every Outreach draft for this folder. Default '' means "no extra
    # guidance, prompt the AI with description + retrieved files only".
    outreach_instructions: str = ""
    created_at: datetime
    updated_at: datetime


class VaultFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=20_000)
    outreach_instructions: str = Field(default="", max_length=10_000)


class VaultFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20_000)
    outreach_instructions: str | None = Field(default=None, max_length=10_000)


class OutreachDraftRequest(BaseModel):
    broker_dealer_id: int = Field(..., gt=0)
    contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)


class OutreachDraftResponse(BaseModel):
    subject: str
    body: str


class OutreachSendRequest(BaseModel):
    broker_dealer_id: int = Field(..., gt=0)
    contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)
    # 998 is RFC 5322's hard line limit; subjects practically never get
    # close to that, but the FE doesn't constrain the field so guard at
    # the API boundary. The body cap matches outreach-modal's textarea
    # ceiling — no realistic draft is anywhere near it.
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1, max_length=100_000)


# ── Advisor + Investor parallels ──────────────────────────────────────
# Same shape as OutreachDraftRequest / OutreachSendRequest, just keyed
# on the right (firm, contact) pair for the advisor and investor
# detail-page surfaces. Kept as parallel schemas rather than a
# discriminated union so the existing BD wire format stays unchanged.


class OutreachAdvisorDraftRequest(BaseModel):
    advisor_id: int = Field(..., gt=0)
    advisor_contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)


class OutreachAdvisorSendRequest(BaseModel):
    advisor_id: int = Field(..., gt=0)
    advisor_contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1, max_length=100_000)


class OutreachInvestorDraftRequest(BaseModel):
    institutional_investor_id: int = Field(..., gt=0)
    investor_contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)


class OutreachInvestorSendRequest(BaseModel):
    institutional_investor_id: int = Field(..., gt=0)
    investor_contact_id: int = Field(..., gt=0)
    folder_id: int = Field(..., gt=0)
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1, max_length=100_000)


class OutreachSendResponse(BaseModel):
    id: int
    gmail_message_id: str
    sent_at: datetime
    status: str


class OutreachSendItem(BaseModel):
    """One row in the per-user "sent outreach" list.

    Excludes ``body`` to keep the list payload small — fetch the full
    body on demand via ``GET /outreach/sends/{send_id}`` when the user
    expands a row.

    Polymorphic across firm types: ``firm_type`` discriminates
    broker_dealer / advisor / institutional_investor. The matching pair
    of id/name fields is populated; the others are None. Legacy
    ``broker_dealer_id`` + ``broker_dealer_name`` stay populated for BD
    rows for FE backwards compatibility.
    """

    id: int
    sent_at: datetime
    status: str
    subject: str
    gmail_message_id: str | None
    error: str | None
    firm_type: str = "broker_dealer"
    broker_dealer_id: int | None = None
    broker_dealer_name: str | None = None
    advisor_id: int | None = None
    advisor_name: str | None = None
    institutional_investor_id: int | None = None
    institutional_investor_name: str | None = None
    contact_type: str = "executive_contact"
    contact_id: int | None = None
    advisor_contact_id: int | None = None
    investor_contact_id: int | None = None
    contact_name: str = ""
    contact_email: str | None = None
    # Folder may be NULL if the service folder was deleted after the
    # send. The send row stays so the audit history doesn't lose
    # entries.
    folder_id: int | None
    folder_name: str | None
    # Populated only on the admin "all users" scope so the FE can show a
    # Sender column. Omitted (None) on the per-user "mine" scope to keep
    # the response shape backwards-compatible.
    user_id: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None


class OutreachSendsListResponse(BaseModel):
    items: list[OutreachSendItem]
    total: int
    limit: int
    offset: int


class OutreachSendDetailResponse(OutreachSendItem):
    body: str
