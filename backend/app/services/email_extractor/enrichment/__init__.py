"""Discovered-email enrichment: a configurable, gap-filling provider chain.

Public surface used by the API + bulk loop:

* :func:`enrich_discovered_email` -- enrich one row by walking the chain.
* :func:`any_provider_configured` -- gate before queuing enrich work.
* :class:`EnrichmentError` + :data:`NOT_FOUND_MESSAGE` / :data:`NOT_CONFIGURED_MESSAGE`
  -- error type + sentinels the endpoint maps to HTTP status codes.
"""

from __future__ import annotations

from app.services.email_extractor.enrichment.base import (
    NOT_CONFIGURED_MESSAGE,
    NOT_FOUND_MESSAGE,
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
)
from app.services.email_extractor.enrichment.orchestrator import (
    any_provider_configured,
    configured_providers,
    enrich_discovered_email,
)

__all__ = [
    "NOT_CONFIGURED_MESSAGE",
    "NOT_FOUND_MESSAGE",
    "EmailEnrichmentProvider",
    "EnrichmentData",
    "EnrichmentError",
    "any_provider_configured",
    "configured_providers",
    "enrich_discovered_email",
]
