from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScoringSettingsItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    settings_key: str
    net_capital_growth_weight: int = Field(ge=0, le=100)
    clearing_arrangement_weight: int = Field(ge=0, le=100)
    financial_health_weight: int = Field(ge=0, le=100)
    registration_recency_weight: int = Field(ge=0, le=100)


class ScoringSettingsUpdate(BaseModel):
    net_capital_growth_weight: int = Field(ge=0, le=100)
    clearing_arrangement_weight: int = Field(ge=0, le=100)
    financial_health_weight: int = Field(ge=0, le=100)
    registration_recency_weight: int = Field(ge=0, le=100)


class CompetitorProviderCreate(BaseModel):
    name: str
    aliases: list[str]
    priority: int = Field(ge=1, le=999)


class CompetitorProviderUpdate(BaseModel):
    aliases: list[str]
    priority: int = Field(ge=1, le=999)
    is_active: bool


class DataRefreshResponse(BaseModel):
    filing_monitor_run_id: int
    clearing_pipeline_run_id: int
    refreshed_broker_dealers: int


class ClearingPartnerMergeSuggestionItem(BaseModel):
    """One cluster of raw clearing-partner variants awaiting admin review."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_signature: str
    variants: list[str]
    suggested_name: str
    min_score: int = Field(ge=0, le=100)
    status: Literal["pending", "accepted", "rejected"]
    accepted_provider_id: int | None
    created_at: datetime
    resolved_at: datetime | None


class ClearingPartnerMergeSuggestionList(BaseModel):
    items: list[ClearingPartnerMergeSuggestionItem]
    pending_count: int


class ClearingPartnerClusteringRunResponse(BaseModel):
    """Result of a /run pass — exposes new vs already-known counts so the
    admin UI can show a meaningful toast even when nothing new surfaced.
    """

    new_pending_count: int
    total_pending_count: int


class ClearingPartnerMergeSuggestionAccept(BaseModel):
    """Admin-edited acceptance payload.

    ``canonical_name`` and ``variants`` may differ from the original
    suggestion (admin renames, drops a variant they don't want merged).
    Min one variant or the request is 400.
    """

    canonical_name: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=50)
    variants: list[str] = Field(min_length=1)
    priority: int = Field(default=90, ge=1, le=999)
