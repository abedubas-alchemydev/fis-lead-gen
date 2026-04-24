from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.broker_dealer import BrokerDealerSummary


class FavoriteResponse(BaseModel):
    """Shape returned by ``POST /broker-dealers/{id}/favorite``.

    ``favorited`` is always ``True`` in the happy path; the field exists so a
    future "toggle" endpoint could return the new state without changing the
    client contract.
    """

    favorited: bool
    favorited_at: datetime


class FavoriteListItem(BrokerDealerSummary):
    """One row in ``GET /favorites``.

    Extends ``BrokerDealerSummary`` with the favourite-specific timestamp so
    the UI can render "added 3 days ago" without a second round-trip.
    """

    model_config = ConfigDict(from_attributes=True)

    favorited_at: datetime


class FavoriteListResponse(BaseModel):
    items: list[FavoriteListItem]
    total: int
    limit: int
    offset: int
