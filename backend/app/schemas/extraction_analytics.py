"""Schemas for the admin Extraction Analytics page (`/settings/extractions`).

Read-only observability over what each external enrichment provider
(Apollo, Hunter, Snov, PDL, site-crawler, theHarvester, and the website
resolvers) extracted, grouped by firm. Three response shapes:

- ``ProviderSummaryResponse`` — the KPI strip + per-provider rollup.
- ``FirmExtractionListResponse`` — paginated firm-grouped list, each firm
  carrying per-provider counts.
- ``FirmExtractionDetailResponse`` — drill-down to the actual extracted
  values for one firm, each tagged with the provider that produced it.

Kept in its own namespace (not ``schemas/stats.py``) so this admin-only
surface can widen independently of the user-facing dashboard stats.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FirmType = Literal["broker_dealer", "advisor", "institutional_investor"]
Surface = Literal["contact", "discovered_email", "website"]
ValueKind = Literal["email", "phone", "linkedin", "website"]


class ProviderCount(BaseModel):
    """One provider's rollup within a surface (contacts / emails / websites).

    ``value_count`` is rows extracted (contacts won, emails discovered, or
    websites resolved); ``firm_count`` is the distinct firms touched.
    """

    provider: str
    label: str
    surface: Surface
    firm_count: int
    value_count: int


class ProviderSummaryTotals(BaseModel):
    total_contacts_attributed: int
    # Contacts whose ``discovery_source`` is NULL (legacy rows that predate
    # provider attribution). Surfaced so the count isn't silently dropped.
    total_contacts_unattributed: int
    total_discovered_emails: int
    total_enriched_emails: int
    total_firms_with_website: int


class ProviderSummaryResponse(BaseModel):
    contacts: list[ProviderCount]
    discovered_emails: list[ProviderCount]
    websites: list[ProviderCount]
    totals: ProviderSummaryTotals


class FirmProviderCount(BaseModel):
    provider: str
    label: str
    count: int


class FirmExtractionRow(BaseModel):
    """One firm in the grouped list, with per-provider extraction counts."""

    firm_type: FirmType
    firm_id: int
    name: str
    crd_number: str | None
    website_source: str | None
    contact_total: int
    discovered_email_total: int
    # Non-zero provider counts for this firm, highest first. Counts reflect
    # the winning provider per contact (``discovery_source``); expand the
    # firm for per-value provenance.
    provider_counts: list[FirmProviderCount]
    last_enriched_at: datetime | None


class FirmExtractionListResponse(BaseModel):
    items: list[FirmExtractionRow]
    total: int


class ExtractedValue(BaseModel):
    """A single extracted datum and the provider that produced it."""

    kind: ValueKind
    value: str
    provider: str
    label: str
    contact_id: int | None
    contact_name: str | None
    contact_title: str | None
    confidence: float | None
    source_table: Literal["contact", "discovered_email", "firm"]


class FirmExtractionDetailResponse(BaseModel):
    firm_type: FirmType
    firm_id: int
    name: str
    website: str | None
    website_source: str | None
    # Flat list; the FE groups by ``provider``.
    values: list[ExtractedValue]
