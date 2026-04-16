from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ClearingArrangementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bd_id: int
    filing_year: int
    report_date: date | None
    source_filing_url: str | None
    source_pdf_url: str | None
    clearing_partner: str | None
    clearing_type: str | None
    agreement_date: date | None
    extraction_confidence: float | None
    extraction_status: str
    extraction_notes: str | None
    is_competitor: bool
    is_verified: bool
    extracted_at: datetime | None
    created_at: datetime


class ClearingArrangementsResponse(BaseModel):
    items: list[ClearingArrangementItem]


class CompetitorProviderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    aliases: list[str]
    priority: int
    is_active: bool


class CompetitorProvidersResponse(BaseModel):
    items: list[CompetitorProviderItem]


class PipelineRunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_name: str
    trigger_source: str
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int
    notes: str | None
    started_at: datetime
    completed_at: datetime | None


class PipelineStatusResponse(BaseModel):
    latest_run: PipelineRunItem | None
    recent_runs: list[PipelineRunItem]
    recent_failures: list[ClearingArrangementItem]


class PipelineTriggerResponse(BaseModel):
    run_id: int
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int


class ClearingProviderShare(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    count: int
    percentage: float
    is_competitor: bool


class ClearingDistributionResponse(BaseModel):
    items: list[ClearingProviderShare]
