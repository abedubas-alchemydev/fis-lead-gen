from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.broker_dealer import BrokerDealerSummary


class VisitListItem(BrokerDealerSummary):
    """One row in ``GET /visits``.

    Carries the visit telemetry (``last_visited_at`` + ``visit_count``) so the
    Visited Firms page can show "last visited 2 hours ago · 4 visits" without
    a second round-trip.
    """

    model_config = ConfigDict(from_attributes=True)

    last_visited_at: datetime
    visit_count: int


class VisitListResponse(BaseModel):
    items: list[VisitListItem]
    total: int
    limit: int
    offset: int
