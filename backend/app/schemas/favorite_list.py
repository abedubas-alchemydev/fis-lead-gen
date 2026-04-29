"""Pydantic shapes for /favorite-lists (#17 phases 1-2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class FavoriteListCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists``."""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class FavoriteListUpdate(BaseModel):
    """Request body for ``PUT /api/v1/favorite-lists/{list_id}``."""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class FavoriteListItemCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/items``."""

    broker_dealer_id: int = Field(ge=1)
