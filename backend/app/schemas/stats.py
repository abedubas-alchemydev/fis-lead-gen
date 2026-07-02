from datetime import date

from pydantic import BaseModel

from app.schemas.pipeline import ClearingProviderShare


class TotalBrokerDealersResponse(BaseModel):
    total_bds: int


class DashboardStatsResponse(BaseModel):
    total_active_bds: int
    new_bds_90_days: int
    # "Pending Approval BDs" — filed (CRD assigned, registration refresh
    # checked) but no SEC approval date observed, windowed to the same
    # 90 days as ``new_bds_90_days`` on the filed-date proxy. Replaced the
    # retired ``deficiency_alerts`` KPI (the alerts endpoints/page are
    # unchanged — only this dashboard tile moved).
    pending_approval_bds: int
    high_value_participants: int


class ClearingDistributionResponse(BaseModel):
    items: list[ClearingProviderShare]


class TimeSeriesBucketResponse(BaseModel):
    date: date
    registrations: int
    alerts: int


class TimeSeriesResponse(BaseModel):
    range: str
    buckets: list[TimeSeriesBucketResponse]
