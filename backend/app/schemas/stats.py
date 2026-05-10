from datetime import date

from pydantic import BaseModel

from app.schemas.pipeline import ClearingProviderShare


class TotalBrokerDealersResponse(BaseModel):
    total_bds: int


class DashboardStatsResponse(BaseModel):
    total_active_bds: int
    new_bds_30_days: int
    deficiency_alerts: int
    high_value_leads: int


class ClearingDistributionResponse(BaseModel):
    items: list[ClearingProviderShare]


class TimeSeriesBucketResponse(BaseModel):
    date: date
    registrations: int
    alerts: int


class TimeSeriesResponse(BaseModel):
    range: str
    buckets: list[TimeSeriesBucketResponse]
