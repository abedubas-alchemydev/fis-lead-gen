"""Pydantic schemas for the Vault + Outreach feature."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# Which transport actually ran (or should run) an outreach send. New
# wire field on the three send-request schemas + the historical
# OutreachSendItem rows so admins can see which provider was used.
# Defaults to "google" on request shapes so existing FE callers stay
# functional during the staged rollout.
EmailProviderId = Literal["google", "microsoft", "yahoo"]


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
    # Optional per-folder default sender. When set, the Outreach modal
    # preselects this account when this folder is chosen. Soft reference
    # to ``account.id`` — modal falls back if the account is unlinked.
    default_sender_account_id: str | None = None
    created_at: datetime
    updated_at: datetime


class VaultFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=20_000)
    outreach_instructions: str = Field(default="", max_length=10_000)
    default_sender_account_id: str | None = Field(default=None, max_length=255)


class VaultFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20_000)
    outreach_instructions: str | None = Field(default=None, max_length=10_000)
    # Send ``null`` to clear, a value to set, omit the key entirely to
    # leave alone (handled by ``model_dump(exclude_unset=True)`` in the
    # endpoint -- explicit ``None`` writes through, missing key skips).
    default_sender_account_id: str | None = Field(default=None, max_length=255)


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
    # Legacy field kept on the wire for back-compat. Once
    # ``sender_account_id`` is set the server derives the provider from
    # the account row and this is ignored.
    provider: EmailProviderId = "google"
    # PK of the ``account`` row to send from. Optional so legacy callers
    # work: the server applies the 3-tier fallback (folder default →
    # first send-scoped account → first linked account) when omitted.
    sender_account_id: str | None = Field(default=None, max_length=255)


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
    provider: EmailProviderId = "google"
    sender_account_id: str | None = Field(default=None, max_length=255)


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
    provider: EmailProviderId = "google"
    sender_account_id: str | None = Field(default=None, max_length=255)


class OutreachAdhocSendRequest(BaseModel):
    """Body for ``POST /outreach/adhoc-send``.

    Used by the ``/outreach/sent?tab=create`` workspace when the user
    sends to a free-form email address with no contact / firm record
    in the system. AI draft generation is available via
    ``POST /outreach/adhoc-draft`` (see ``OutreachAdhocDraftRequest``);
    the send path itself just accepts whatever subject + body the user
    submits.

    ``folder_id`` is optional metadata: when provided, it lets the
    sent-history table label the service the send was about; when
    omitted (e.g. user wrote a totally one-off message), the row
    shows ``(deleted)`` / ``—`` for service the same way it would
    for any send whose folder has since been deleted.
    """

    recipient_email: EmailStr
    recipient_name: str | None = Field(default=None, max_length=255)
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1, max_length=100_000)
    sender_account_id: str | None = Field(default=None, max_length=255)
    folder_id: int | None = Field(default=None, gt=0)


class OutreachAdhocDraftRequest(BaseModel):
    """Body for ``POST /outreach/adhoc-draft``.

    Drafts a cold email for the free-form-email path on
    ``/outreach/sent?tab=create``. Unlike the contact-keyed draft
    endpoints, this one has no firm / contact context — Gemini works
    from the service folder's description + RAG excerpts plus the
    optional recipient name.

    ``folder_id`` is required (unlike on the send path): without a
    service folder there's nothing concrete for the LLM to pitch, so
    the FE only enables the Generate button once a service is picked.
    """

    folder_id: int = Field(..., gt=0)
    recipient_email: EmailStr
    recipient_name: str | None = Field(default=None, max_length=255)


class RecipientSearchResult(BaseModel):
    """One contact row in the recipient autocomplete dropdown.

    Returned by ``GET /outreach/contacts/search``. UNIONs the three
    contact tables (executive_contacts, advisor_contacts,
    investor_contacts) with their parent firm joined for display.
    Excludes rows with no email -- you can't send outreach to a
    contact without an address, and the dropdown shouldn't tease
    options the user can't pick.
    """

    entity_kind: Literal["broker_dealer", "advisor", "institutional_investor"]
    entity_id: int
    entity_name: str
    contact_id: int
    contact_name: str
    contact_title: str | None
    contact_email: str


class RecipientSearchResponse(BaseModel):
    items: list[RecipientSearchResult]


class FirmSearchResult(BaseModel):
    """One firm row in the recipient autocomplete dropdown.

    Returned by ``GET /outreach/firms/search``. The autocomplete
    surfaces firm rows alongside contact rows so the user can either
    pick a known contact directly (no modal) or pick a firm to open
    a "pick a contact at this firm" modal. ``contact_count`` only
    counts contacts that have an email on file -- a firm whose
    contacts all lack emails can't be sent to and shouldn't appear.
    """

    entity_kind: Literal["broker_dealer", "advisor", "institutional_investor"]
    entity_id: int
    entity_name: str
    contact_count: int


class FirmSearchResponse(BaseModel):
    items: list[FirmSearchResult]


class FirmContactsResponse(BaseModel):
    """All email-bearing contacts at a single firm.

    Returned by ``GET /outreach/firms/contacts`` after the user picks a
    firm row in the recipient autocomplete. Reuses the
    ``RecipientSearchResult`` shape since the modal renders one row per
    contact identically to how the autocomplete renders contact-hit
    rows, then picks one into the create-outreach form.
    """

    entity_kind: Literal["broker_dealer", "advisor", "institutional_investor"]
    entity_id: int
    entity_name: str
    items: list[RecipientSearchResult]


class FavoriteSearchResult(BaseModel):
    """One favorite-list row in the recipient autocomplete dropdown.

    Returned by ``GET /outreach/favorites/search``. ``firm_count``
    counts only the firms in the list whose contacts have at least
    one email -- a list whose firms are all un-emailable shouldn't
    appear in the dropdown since picking it would land in an empty
    drill-down modal.
    """

    list_id: int
    name: str
    firm_count: int


class FavoriteSearchResponse(BaseModel):
    items: list[FavoriteSearchResult]


class FavoriteFirmsResponse(BaseModel):
    """All email-bearing-contact firms in a single favorite list.

    Returned by ``GET /outreach/favorites/{list_id}/firms``. The drill-
    down modal renders one row per firm; picking a row swaps the modal
    to the firm-contacts view (``GET /outreach/firms/contacts``).
    """

    list_id: int
    name: str
    items: list[FirmSearchResult]


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
    broker_dealer / advisor / institutional_investor / adhoc. The
    matching pair of id/name fields is populated; the others are None.
    Legacy ``broker_dealer_id`` + ``broker_dealer_name`` stay populated
    for BD rows for FE backwards compatibility.

    Adhoc rows (``firm_type="adhoc"``) carry the destination address
    in ``recipient_email`` / ``recipient_name``; all firm + contact
    id/name fields are None. Contact-based rows leave the recipient_*
    fields None and the FE falls back to ``contact_email``.
    """

    id: int
    sent_at: datetime
    status: str
    subject: str
    # Transport that actually ran the send. Pre-PR-C rows are all
    # "google" by definition (Gmail was the only transport then) so
    # the migration backfilled them. Default keeps test fixtures that
    # build OutreachSendItem by hand still working.
    provider: EmailProviderId = "google"
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
    # Adhoc destination address + display name. Populated only for
    # ``firm_type="adhoc"`` rows; None for contact-based rows. The FE
    # treats ``recipient_email`` as the canonical address on adhoc
    # rows and falls back to ``contact_email`` on contact rows.
    recipient_email: str | None = None
    recipient_name: str | None = None
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


class LinkedProviderItem(BaseModel):
    """One linked OAuth account for the calling user.

    Returned by ``GET /api/v1/outreach/linked-providers`` so the
    Outreach modal can render an email-address dropdown — one entry
    per account, not per provider type, since a user can link
    multiple Google accounts (or any provider combo).
    """

    # PK of the ``account`` row. The FE passes this back as
    # ``sender_account_id`` on the send call.
    account_id: str
    # OAuth provider's external user id (Google's ``sub``, MS Graph's
    # object id, Yahoo's user GUID). Required by Better Auth's
    # ``/unlink-account`` to disambiguate which Google account to
    # unlink when the user has multiple.
    provider_account_id: str
    provider: EmailProviderId
    # The actual mailbox the OAuth token is bound to. Captured on link
    # via the databaseHooks in ``frontend/lib/auth.ts``; may be None
    # for legacy accounts linked before the hook existed (lazy backfill
    # on first send covers that case).
    email_address: str | None
    scope: str | None
    # True iff the linked account has the send scope already granted
    # (``gmail.send`` / ``Mail.Send`` / ``mail-w`` per provider). FE
    # uses this to decide between a normal Send call and a re-consent
    # ``linkSocial`` first.
    has_send_scope: bool
    linked_at: datetime


class LinkedProvidersResponse(BaseModel):
    items: list[LinkedProviderItem]
