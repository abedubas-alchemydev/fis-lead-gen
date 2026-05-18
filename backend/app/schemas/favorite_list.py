"""Pydantic shapes for /favorite-lists (#17 phases 1-2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    """One row in ``GET /api/v1/favorite-lists/{list_id}/items``.

    Polymorphic shape across three firm types: broker_dealer, advisor,
    institutional_investor. Each row sets exactly one id/name pair
    matching its ``entity_type`` discriminator; the other two pairs are
    ``None``. Keeping legacy BD + advisor field names populated preserves
    backwards compatibility with the FE contracts shipped before
    institutional investors landed.
    """

    model_config = ConfigDict(from_attributes=True)

    entity_type: Literal["broker_dealer", "advisor", "institutional_investor"]
    broker_dealer_id: int | None = None
    broker_dealer_name: str | None = None
    advisor_id: int | None = None
    advisor_name: str | None = None
    institutional_investor_id: int | None = None
    institutional_investor_name: str | None = None
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


class FavoriteListItemBatchCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/items/batch``.

    Cap of 200 is double the masterlist's max page size so a user selecting
    a full page never trips 422; also bounds the single-statement DB work.
    """

    broker_dealer_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator("broker_dealer_ids")
    @classmethod
    def _dedupe_and_validate(cls, value: list[int]) -> list[int]:
        deduped = list(dict.fromkeys(value))
        for bd_id in deduped:
            if bd_id < 1:
                raise ValueError("broker_dealer_ids must be positive integers")
        return deduped


class FavoriteListItemBatchResponse(BaseModel):
    """Result of a batch-add to a favorite list.

    ``added`` + ``skipped_existing`` + ``len(skipped_unknown)`` always equals
    the unique-id count in the request, so the FE can render an accurate toast
    like "Added 12 (3 already in list, 1 unknown)".
    """

    added: int
    skipped_existing: int
    skipped_unknown: list[int]


class FavoriteListAdvisorItemCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/advisor-items``."""

    advisor_id: int = Field(ge=1)


class FavoriteListAdvisorItemBatchCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/advisor-items/batch``."""

    advisor_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator("advisor_ids")
    @classmethod
    def _dedupe_and_validate(cls, value: list[int]) -> list[int]:
        deduped = list(dict.fromkeys(value))
        for advisor_id in deduped:
            if advisor_id < 1:
                raise ValueError("advisor_ids must be positive integers")
        return deduped


class FavoriteListAdvisorItemResponse(BaseModel):
    """Response shape for single-advisor add to a favorite list."""

    model_config = ConfigDict(from_attributes=True)

    advisor_id: int
    advisor_name: str
    added_at: datetime


class FavoriteListInvestorItemCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/investor-items``."""

    institutional_investor_id: int = Field(ge=1)


class FavoriteListInvestorItemBatchCreate(BaseModel):
    """Request body for ``POST /api/v1/favorite-lists/{list_id}/investor-items/batch``."""

    institutional_investor_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator("institutional_investor_ids")
    @classmethod
    def _dedupe_and_validate(cls, value: list[int]) -> list[int]:
        deduped = list(dict.fromkeys(value))
        for investor_id in deduped:
            if investor_id < 1:
                raise ValueError(
                    "institutional_investor_ids must be positive integers"
                )
        return deduped


class FavoriteListInvestorItemResponse(BaseModel):
    """Response shape for single-investor add to a favorite list."""

    model_config = ConfigDict(from_attributes=True)

    institutional_investor_id: int
    institutional_investor_name: str
    added_at: datetime


class FavoriteListWithMembership(FavoriteListResponse):
    """One row in ``GET /api/v1/broker-dealers/{firm_id}/favorite-lists``.

    Extends ``FavoriteListResponse`` with ``is_member`` so the FE list-picker
    can render checked states for lists that already contain ``firm_id``
    without a second round-trip per list.
    """

    is_member: bool
