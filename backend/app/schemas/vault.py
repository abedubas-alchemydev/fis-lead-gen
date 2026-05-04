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
