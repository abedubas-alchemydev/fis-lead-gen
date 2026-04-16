from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AlertListItem(BaseModel):
    id: int
    bd_id: int
    firm_name: str
    form_type: str
    priority: str
    filed_at: datetime
    summary: str
    source_filing_url: str | None
    is_read: bool


class AlertListMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


class AlertListResponse(BaseModel):
    items: list[AlertListItem]
    meta: AlertListMeta


class AlertReadResponse(BaseModel):
    id: int
    is_read: bool


class AlertsBulkReadResponse(BaseModel):
    updated_count: int


class FilingMonitorRunResponse(BaseModel):
    run_id: int
    total_items: int
    success_count: int
    failure_count: int
    status: str
