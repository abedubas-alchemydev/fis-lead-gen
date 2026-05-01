"""Pydantic DTO for the typed ``unknown_reason`` envelope.

Each nullable *cluster* on the master list and firm-detail responses
ships with an optional ``unknown_reason`` object — one reason per FE
info-icon, not one reason per column. The two clusters today:

  - clearing cluster: ``current_clearing_partner`` + ``current_clearing_type``
    (surfaced as ``current_clearing_unknown_reason``)
  - financial-health cluster: ``latest_net_capital`` +
    ``latest_excess_net_capital`` + ``yoy_growth`` + ``health_status``
    (surfaced as ``financial_unknown_reason``)

The reason is populated whenever **any** field in the cluster is null;
``note`` is prepended with ``[Triggered by missing: <a>, <b>, ...]``
listing every null cluster field in declared order so the FE tooltip
can name the specific column(s). ``None`` ⇒ every field in the cluster
is populated and the FE renders the block normally.

Keep this schema and ``app.services.unknown_reasons.UnknownReasonResult``
in sync — the service emits the dataclass; the endpoint maps it to this
DTO just before serialization.
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
