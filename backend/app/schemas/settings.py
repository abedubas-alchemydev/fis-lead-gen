from __future__ import annotations

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
