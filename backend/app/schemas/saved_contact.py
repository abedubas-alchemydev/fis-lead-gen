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
