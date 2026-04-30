"""Pydantic DTO for the typed ``unknown_reason`` envelope.

Every nullable field on the master list and firm-detail responses ships
with an optional ``unknown_reason`` object. ``None`` ⇒ value is present and
the FE renders the cell normally. Non-None ⇒ value is missing and the FE
renders an info icon whose tooltip uses ``category`` for the headline copy
and ``note`` for the optional free-text narrative.

Keep this schema and ``app.services.unknown_reasons.UnknownReasonResult`` in
sync — the service emits the dataclass; the endpoint maps it to this DTO
just before serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

UnknownReasonCategory = Literal[
    "firm_does_not_disclose",
    "no_filing_available",
    "low_confidence_extraction",
    "pdf_unparseable",
    "provider_error",
    "not_yet_extracted",
    "data_not_present",
]


class UnknownReason(BaseModel):
    """Why a value came back NULL for the FE info-icon tooltip."""

    model_config = ConfigDict(from_attributes=True)

    category: UnknownReasonCategory
    note: str | None = None
    extracted_at: datetime | None = None
    confidence: float | None = None
