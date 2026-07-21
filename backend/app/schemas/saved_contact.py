"""Pydantic shapes for /saved-contacts (Save Contact feature)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SavedContactCreate(BaseModel):
    """Request body for ``POST /api/v1/saved-contacts``.

    Only the ``source`` discriminator and the ``contact_id`` reference are
    trusted from the client; the snapshot (name / title / email / company /
    phone / linkedin_url) is resolved SERVER-side from the source row so the
    client can't forge the saved contact's fields.
    """

    source: str
    contact_id: int


class SavedContactResponse(BaseModel):
    """One row in ``GET /api/v1/saved-contacts`` (and the ``POST`` echo)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    contact_id: int
    name: str | None = None
    title: str | None = None
    email: str | None = None
    company: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    created_at: datetime


class SavedContactWithOwner(SavedContactResponse):
    """One row in ``GET /api/v1/integrations/saved-contacts``.

    A saved contact PLUS the id / name / email of the user who saved it, so the
    sibling CRM can attribute each contact to its owner across ALL users --
    unlike the owner-scoped ``SavedContactResponse`` served to end users. Owner
    name/email are typed optional to stay forgiving of sparse ``user`` rows even
    though the inner join guarantees an owner id.
    """

    saved_by_id: str
    saved_by_name: str | None = None
    saved_by_email: str | None = None
