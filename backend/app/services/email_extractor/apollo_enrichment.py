"""Backwards-compatible shim for the discovered-email enrichment entrypoint.

The email-extractor "Enrich" flow moved from Apollo-only to a configurable,
gap-filling provider chain (Apollo -> PDL -> Hunter -> Snov -> name lookup ->
web scraper); see
``app/services/email_extractor/enrichment/``. This module re-exports the
orchestrator entrypoint, the error type, and ``APOLLO_MATCH_URL`` so existing
importers (``bulk_enrichment``, the endpoint, and tests) keep working without
edits. Prefer importing from ``app.services.email_extractor.enrichment``
directly in new code.
"""

from __future__ import annotations

from app.services.email_extractor.enrichment import (
    EnrichmentError,
    enrich_discovered_email,
)
from app.services.email_extractor.enrichment.apollo import APOLLO_MATCH_URL

__all__ = ["APOLLO_MATCH_URL", "EnrichmentError", "enrich_discovered_email"]
