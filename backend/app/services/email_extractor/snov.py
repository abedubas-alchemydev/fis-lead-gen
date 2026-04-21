"""Snov.io provider — OAuth2 client_credentials → domain-emails-with-info.

Two-step flow per ``run()``:
1. ``POST /v1/oauth/access_token`` with ``grant_type=client_credentials`` →
   short-lived bearer token.
2. ``GET /v2/domain-emails-with-info?domain=<d>&limit=<n>`` with the token.

Token is fetched per-run (no caching). At Email Extractor's current volume the
extra HTTP round-trip is negligible and skipping the cache eliminates state
correctness risk; revisit if Snov rate-limits OAuth.

Per ADR 0002, error strings are emitted **bare** — no internal ``"snov: "``
prefix. The aggregator wraps each error with ``f"{provider.name}: {err}"``
exactly once. Intra-string prefixes ``"oauth:"`` and ``"search:"`` distinguish
which step failed but never the provider name itself.

Drafts have ``source="snov"``, ``confidence`` derived from Snov's
``probability`` field (0..100 → 0.0..1.0; absent on free tier → ``None``), and
``attribution`` of the form ``"snov: <status> | <type> | src=<first_source_url>"``
(``status`` ∈ ``verified`` / ``notVerified`` / ``-``) capped at 500 chars.
Emails are lowercased. Non-string emails and entries lacking ``@`` are filtered.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.email_extractor.base import DiscoveredEmailDraft, DiscoveryResult

logger = logging.getLogger(__name__)

OAUTH_URL = "https://api.snov.io/v1/oauth/access_token"
DOMAIN_EMAILS_URL = "https://api.snov.io/v2/domain-emails-with-info"
REQUEST_TIMEOUT_SECONDS = 30.0
ATTRIBUTION_CHAR_CAP = 500


class Snov:
    """``EmailSource`` Protocol implementation backed by Snov.io."""

    name = "snov"

    async def run(self, domain: str) -> DiscoveryResult:
        client_id = settings.snov_client_id
        client_secret = settings.snov_client_secret
        if not client_id or not client_secret:
            return DiscoveryResult(errors=["credentials not configured"])

        token, oauth_err = await _fetch_token(client_id, client_secret)
        if oauth_err is not None or token is None:
            return DiscoveryResult(errors=[oauth_err or "oauth: no access_token in response"])
        return await _search_domain(token, domain, settings.snov_limit)


async def _fetch_token(client_id: str, client_secret: str) -> tuple[str | None, str | None]:
    """Fetch an OAuth access token. Returns ``(token, None)`` on success or
    ``(None, error_message)`` on any failure. Error strings are bare per ADR 0002
    with an ``oauth:`` intra-string prefix to distinguish the failing step.
    """
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(OAUTH_URL, json=body)
    except httpx.TimeoutException:
        return None, "oauth: timeout"
    except Exception as exc:  # noqa: BLE001
        return None, f"oauth: {exc.__class__.__name__}"

    if response.status_code == 401:
        return None, "oauth: invalid credentials"
    if response.status_code >= 500:
        return None, f"oauth: upstream error {response.status_code}"
    if response.status_code != 200:
        return None, f"oauth: bad request {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return None, "oauth: invalid json"

    if not isinstance(payload, dict):
        return None, "oauth: payload not a dict"

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        return None, "oauth: no access_token in response"

    return token, None


async def _search_domain(token: str, domain: str, limit: int) -> DiscoveryResult:
    """Call Snov's domain-emails-with-info endpoint and map results to drafts."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {"domain": domain, "type": "all", "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(DOMAIN_EMAILS_URL, headers=headers, params=params)
    except httpx.TimeoutException:
        return DiscoveryResult(errors=["search: timeout"])
    except Exception as exc:  # noqa: BLE001
        return DiscoveryResult(errors=[f"search: {exc.__class__.__name__}"])

    if response.status_code == 401:
        return DiscoveryResult(errors=["search: token rejected"])
    if response.status_code == 402:
        return DiscoveryResult(errors=["search: out of credits"])
    if response.status_code == 429:
        return DiscoveryResult(errors=["search: rate limited"])
    if response.status_code >= 500:
        return DiscoveryResult(errors=[f"search: upstream error {response.status_code}"])
    if response.status_code != 200:
        return DiscoveryResult(errors=[f"search: bad request {response.status_code}"])

    try:
        payload = response.json()
    except ValueError:
        return DiscoveryResult(errors=["search: invalid json"])

    if not isinstance(payload, dict):
        return DiscoveryResult(errors=["payload not a dict"])

    if not payload.get("success", False):
        message = payload.get("message") or "unknown"
        return DiscoveryResult(errors=[f"search failed: {message}"])

    raw_emails = payload.get("emails", [])
    if not isinstance(raw_emails, list):
        return DiscoveryResult(errors=["emails field not a list"])

    drafts: list[DiscoveredEmailDraft] = []
    for entry in raw_emails:
        draft = _entry_to_draft(entry)
        if draft is not None:
            drafts.append(draft)
    return DiscoveryResult(emails=drafts)


def _entry_to_draft(entry: Any) -> DiscoveredEmailDraft | None:
    if not isinstance(entry, dict):
        return None
    email = entry.get("email")
    if not isinstance(email, str) or not email or "@" not in email:
        return None

    raw_prob = entry.get("probability")
    confidence = float(raw_prob) / 100.0 if isinstance(raw_prob, (int, float)) else None

    status = entry.get("status") or "-"
    email_type = entry.get("type") or "-"
    sources = entry.get("sources") or []
    first_url = "-"
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, dict):
            first_url = str(first.get("url") or "-")

    attribution = f"snov: {status} | {email_type} | src={first_url}"[:ATTRIBUTION_CHAR_CAP]

    return DiscoveredEmailDraft(
        email=email.lower(),
        source="snov",
        confidence=confidence,
        attribution=attribution,
    )
