"""Pydantic response shapes for /favorite-lists (#17 phase 1, GET only)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FavoriteListResponse(BaseModel):
    """One row in ``GET /api/v1/favorite-lists``.

    ``item_count`` is computed via a sub-aggregate so the FE doesn't need a
    second round-trip to render "N firms" badges next to each list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool
    item_count: int
    created_at: datetime


class FavoriteListItemResponse(BaseModel):
    """One row in ``GET /api/v1/favorite-lists/{list_id}/items``."""

    model_config = ConfigDict(from_attributes=True)

    broker_dealer_id: int
    broker_dealer_name: str
    added_at: datetime


class PaginatedFavoriteListItems(BaseModel):
    items: list[FavoriteListItemResponse]
    total: int
    page: int
    page_size: int
