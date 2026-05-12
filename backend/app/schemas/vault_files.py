"""Pydantic schemas for the Vault file-upload feature.

Mirrors ``backend/app/models/vault_folder_file.py``. The FE polls this
schema's ``processing_status`` field while a file is non-terminal,
auto-stops on ``ready`` or ``failed``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VaultFolderFileResponse(BaseModel):
    """One uploaded file row, returned from upload + list + retry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    original_filename: str
    mime_type: str
    size_bytes: int
    # extracting | embedding | ready | failed
    processing_status: str
    processing_error: str | None
    processing_started_at: datetime
    processing_finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
