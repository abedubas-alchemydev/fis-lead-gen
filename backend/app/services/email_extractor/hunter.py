"""Hunter.io Domain Search provider.

Hits ``GET https://api.hunter.io/v2/domain-search?domain=...&api_key=...&limit=...``
and translates the response into ``DiscoveredEmailDraft`` rows. Never raises;
every failure mode (missing key, 4xx/5xx, timeout, parse error) is captured
as a structured string in ``DiscoveryResult.errors`` so the aggregator can
keep running other providers and finalise the scan cleanly.

Error strings are emitted **bare** — no internal ``"hunter: "`` prefix. The
aggregator wraps them via ``f"{provider.name}: {err}"`` when writing to
``ExtractionRun.error_message``, which gives a single ``"hunter: "`` prefix
in the user-visible message. (Earlier versions double-prefixed.)

Limit comes from ``settings.hunter_limit`` (default 10, validated 1..100 in
``core/config.py``). Hunter returns HTTP 400 with a ``pagination_error`` body
if ``limit`` exceeds the plan cap (10 on free, 100 on paid). That branch is
caught and surfaced as a clear plan-limit error so the run completes cleanly
instead of looking like a generic upstream failure.

Hunter's own auto-verification status (when present) is recorded in
``attribution`` for human inspection but does NOT replace our own
``email-validator`` syntax + MX check, which the aggregator runs inline on
every persisted draft.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.email_extractor.base import DiscoveredEmailDraft, DiscoveryResult

logger = logging.getLogger(__name__)

DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
REQUEST_TIMEOUT_SECONDS = 30.0
ATTRIBUTION_CHAR_CAP = 500
_PLAN_LIMIT_HINTS = ("limited to", "current plan")


class Hunter:
    """``EmailSource`` Protocol implementation backed by Hunter.io."""

    name = "hunter"

    async def run(self, domain: str) -> DiscoveryResult:
        api_key = settings.hunter_api_key
        if not api_key:
            return DiscoveryResult(errors=["api_key not configured"])

        limit = settings.hunter_limit
        params = {"domain": domain, "api_key": api_key, "limit": limit}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(DOMAIN_SEARCH_URL, params=params)
        except httpx.TimeoutException:
            return DiscoveryResult(errors=["timeout"])
        except Exception as exc:  # noqa: BLE001
            return DiscoveryResult(errors=[exc.__class__.__name__])

        if response.status_code == 400:
            if _looks_like_plan_limit(response):
                return DiscoveryResult(errors=[f"free-tier plan limit exceeded (configured limit={limit})"])
            return DiscoveryResult(errors=[f"bad request {response.status_code}"])
        if response.status_code == 401:
            return DiscoveryResult(errors=["invalid api key"])
        if response.status_code == 402:
            return DiscoveryResult(errors=["out of credits"])
        if response.status_code == 403:
            return DiscoveryResult(errors=["account forbidden"])
        if response.status_code == 429:
            return DiscoveryResult(errors=["rate limited"])
        if response.status_code >= 500:
            return DiscoveryResult(errors=[f"upstream error {response.status_code}"])
        if response.status_code != 200:
            return DiscoveryResult(errors=[f"unexpected status {response.status_code}"])

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            return DiscoveryResult(errors=[f"invalid json: {exc}"])

        data = payload.get("data") or {}
        raw_emails = data.get("emails") or []
        drafts: list[DiscoveredEmailDraft] = []
        for entry in raw_emails:
            draft = _entry_to_draft(entry)
            if draft is not None:
                drafts.append(draft)

        return DiscoveryResult(emails=drafts)


def _looks_like_plan_limit(response: httpx.Response) -> bool:
    """True if the 400 body's `errors[*].details` mentions a plan cap."""
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return False
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        details = str(entry.get("details") or "").lower()
        if any(hint in details for hint in _PLAN_LIMIT_HINTS):
            return True
    return False


def _entry_to_draft(entry: dict[str, Any]) -> DiscoveredEmailDraft | None:
    email = entry.get("value")
    if not isinstance(email, str) or not email:
        return None

    raw_confidence = entry.get("confidence")
    confidence = float(raw_confidence) / 100.0 if isinstance(raw_confidence, (int, float)) else None

    attribution = _format_attribution(entry)
    return DiscoveredEmailDraft(
        email=email.lower(),
        source="hunter",
        confidence=confidence,
        attribution=attribution,
    )


def _format_attribution(entry: dict[str, Any]) -> str:
    position = entry.get("position") or "-"
    email_type = entry.get("type") or "-"
    verification = entry.get("verification") or {}
    verified = verification.get("status") if isinstance(verification, dict) else None
    sources = entry.get("sources") or []
    first_uri = "-"
    if sources and isinstance(sources, list):
        first = sources[0]
        if isinstance(first, dict):
            first_uri = str(first.get("uri") or "-")

    text = f"hunter: {position} | {email_type} | verified={verified or 'unknown'} | src={first_uri}"
    return text[:ATTRIBUTION_CHAR_CAP]
