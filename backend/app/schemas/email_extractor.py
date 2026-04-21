from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScanCreateRequest(BaseModel):
    """Inbound payload for POST /api/v1/email-extractor/scans."""

    domain: str = Field(min_length=1, max_length=255, description="Domain to scan, e.g. 'example.com'")
    person_name: str | None = Field(default=None, max_length=255)


class EmailVerificationResponse(BaseModel):
    id: int
    syntax_valid: bool | None
    mx_record_present: bool | None
    smtp_status: str
    smtp_message: str | None
    checked_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DiscoveredEmailResponse(BaseModel):
    id: int
    email: str
    domain: str
    source: str
    confidence: float | None
    attribution: str | None
    created_at: datetime
    verifications: list[EmailVerificationResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ScanResponse(BaseModel):
    """Returned by both POST and GET /scans endpoints."""

    id: int
    pipeline_name: str
    domain: str
    person_name: str | None
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    discovered_emails: list[DiscoveredEmailResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class VerifyRequest(BaseModel):
    """Inbound payload for POST /api/v1/email-extractor/verify."""

    email_ids: list[int] = Field(min_length=1, description="DiscoveredEmail.id values to verify")

    @model_validator(mode="after")
    def _no_duplicates(self) -> VerifyRequest:
        if len(self.email_ids) != len(set(self.email_ids)):
            raise ValueError("email_ids must not contain duplicates")
        return self


class VerifyResultItem(BaseModel):
    """One row in the /verify response, in request order."""

    email_id: int
    email: str | None
    smtp_status: str
    smtp_message: str | None
    checked_at: datetime


class VerificationRunCreateResponse(BaseModel):
    """Returned by POST /api/v1/email-extractor/verify (202 Accepted).

    Results are fetched by polling
    GET /api/v1/email-extractor/verify-runs/{verify_run_id}.
    """

    verify_run_id: int
    status: str


class VerificationRunResponse(BaseModel):
    """Returned by GET /api/v1/email-extractor/verify-runs/{run_id}.

    `results` carries the latest `EmailVerification` per requested email_id
    in the same order as the run's input `email_ids`.
    """

    id: int
    status: str
    total_items: int
    processed_items: int
    success_count: int
    failure_count: int
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    results: list[VerifyResultItem] = Field(default_factory=list)
