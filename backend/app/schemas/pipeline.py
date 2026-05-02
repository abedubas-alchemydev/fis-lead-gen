from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.unknown_reason import UnknownReason


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
    # Populated when ``clearing_partner`` is None — explains *why* the cell
    # shows Unknown so the FE can render an info-icon tooltip without a
    # separate round-trip. Always None when a partner is named.
    unknown_reason: UnknownReason | None = None


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


class PipelineRunStatusResponse(BaseModel):
    """Lightweight per-run status used by the FE to poll a queued
    PipelineRun (e.g. the per-firm ``refresh-financials`` background
    task). Mirrors ``PipelineRunItem`` but exposes a polling-shaped
    contract — distinct so it can grow polling-only fields (progress
    percentages, ETA) without churning the trigger surface.
    """

    model_config = ConfigDict(from_attributes=True)

    run_id: int
    pipeline_name: str
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int
    notes: str | None
    started_at: datetime
    completed_at: datetime | None


class ClearingProviderShare(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    count: int
    percentage: float
    is_competitor: bool


class ClearingDistributionResponse(BaseModel):
    items: list[ClearingProviderShare]


class WipeBdDataRequest(BaseModel):
    confirmation: str


class WipeBdDataResponse(BaseModel):
    affected_tables: list[str]
    rows_deleted: int
    audit_log_id: str
    wiped_at: datetime


class SetFilesApiFlagRequest(BaseModel):
    enabled: bool


class SetFilesApiFlagResponse(BaseModel):
    previous_state: bool
    new_state: bool
    revision_name: str
    ready_at: datetime
