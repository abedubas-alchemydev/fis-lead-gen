"""Snov.io provider: Email Finder (person) + Domain Search (org).

Snov uses OAuth2 client-credentials. A single module-level token cache (with
TTL and 401-triggered refresh) keeps us from hitting ``/oauth/access_token``
on every request. Cache layout::

    {"token": str, "expires_at": float}  # UTC epoch seconds

On a ``401`` from either search endpoint the cache is invalidated and the
request is retried exactly once with a freshly-minted token. That covers
both the "token expired server-side" case and the much rarer "credentials
rotated between two calls" case.

Endpoints
  * ``POST /v1/oauth/access_token`` -- grant_type=client_credentials
  * ``POST /v1/get-emails-from-names`` -- person lookup (name + domain)
  * ``POST /v1/get-domain-emails-with-info`` -- org-level lookup

Confidence
  Snov returns ``probability`` on email rows (0..100). We pass that through
  unchanged. If Snov doesn't provide one (free-tier response) we fall back
  to a conservative 50 so the caller has to deliberately lower the threshold
  to act on it.

As with the other providers, every exception / timeout / non-200 yields
``None`` so the orchestrator can move on cleanly.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryResult,
)

logger = logging.getLogger(__name__)


OAUTH_URL = "https://api.snov.io/v1/oauth/access_token"
EMAIL_FINDER_URL = "https://api.snov.io/v1/get-emails-from-names"
DOMAIN_SEARCH_URL = "https://api.snov.io/v1/get-domain-emails-with-info"

# Snov tokens nominally live for 3600s; shave 60s off to avoid a race between
# "still valid when we checked" and "expired by the time Snov parsed the
# header." Token refresh is cheap (one extra POST) so being conservative here
# has negligible cost.
_TOKEN_SAFETY_MARGIN_SECONDS = 60.0

# Module-level token cache. Not thread-safe, but we only run inside asyncio
# and the worst-case race (two coroutines refreshing simultaneously) produces
# two valid tokens -- the second write simply wins, no correctness impact.
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _reset_token_cache_for_tests() -> None:
    """Hook used by tests to guarantee a fresh token fetch."""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


class SnovProvider(ContactDiscoveryProvider):
    name = "snov"

    async def find_person(
        self,
        first_name: str,
        last_name: str,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        client_id = settings.snov_client_id
        client_secret = settings.snov_client_secret
        if not client_id or not client_secret or not domain:
            return None

        token = await _get_token(client_id, client_secret)
        if token is None:
            return None

        body = {
            "firstName": first_name,
            "lastName": last_name,
            "domain": domain,
        }
        response = await _post_with_refresh(EMAIL_FINDER_URL, body, token, client_id, client_secret)
        if response is None or response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        data = payload.get("data")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None

        email = data.get("email")
        if not email:
            return None

        confidence = _coerce_probability(data.get("probability"))
        return DiscoveryResult(
            email=str(email).strip(),
            phone=None,
            linkedin_url=None,
            confidence=confidence,
            provider=self.name,
            raw=data,
        )

    async def find_org(
        self,
        org_name: str,
        domain: str | None,
    ) -> DiscoveryResult | None:
        client_id = settings.snov_client_id
        client_secret = settings.snov_client_secret
        if not client_id or not client_secret or not domain:
            return None

        token = await _get_token(client_id, client_secret)
        if token is None:
            return None

        body = {"domain": domain, "type": "all", "limit": 10}
        response = await _post_with_refresh(DOMAIN_SEARCH_URL, body, token, client_id, client_secret)
        if response is None or response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        emails = payload.get("emails")
        if not isinstance(emails, list) or not emails:
            return None

        pick = _pick_public_inbox(emails)
        if pick is None:
            return None

        email = pick.get("email")
        if not email:
            return None

        confidence = _coerce_probability(pick.get("probability"))
        return DiscoveryResult(
            email=str(email).strip(),
            phone=None,
            linkedin_url=None,
            confidence=confidence,
            provider="snov_domain",
            raw={"picked": pick, "payload": payload},
        )


async def _get_token(client_id: str, client_secret: str) -> str | None:
    cached = _token_cache.get("token")
    expires_at = float(_token_cache.get("expires_at") or 0.0)
    if cached and expires_at > time.time() + _TOKEN_SAFETY_MARGIN_SECONDS:
        return cached
    return await _refresh_token(client_id, client_secret)


async def _refresh_token(client_id: str, client_secret: str) -> str | None:
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
            response = await client.post(OAUTH_URL, json=body)
    except httpx.HTTPError as exc:
        logger.warning("Snov OAuth request failed: %s", exc)
        return None

    if response.status_code != 200:
        logger.info("Snov OAuth returned %d", response.status_code)
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        return None

    ttl_raw = payload.get("expires_in") if isinstance(payload, dict) else None
    try:
        ttl = float(ttl_raw) if ttl_raw is not None else 3600.0
    except (TypeError, ValueError):
        ttl = 3600.0

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + ttl
    return token


async def _post_with_refresh(
    url: str,
    body: dict[str, Any],
    token: str,
    client_id: str,
    client_secret: str,
) -> httpx.Response | None:
    """POST with bearer token; on 401 refresh the token and retry once."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Snov %s failed: %s", url, exc)
        return None

    if response.status_code != 401:
        return response

    # Token rejected -- invalidate the cache and try exactly one more time.
    _reset_token_cache_for_tests()
    new_token = await _refresh_token(client_id, client_secret)
    if new_token is None:
        return response
    headers = {"Authorization": f"Bearer {new_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.contact_discovery_timeout) as client:
            return await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Snov %s retry failed: %s", url, exc)
        return None


def _coerce_probability(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    # Free-tier responses often omit probability. 50 is a conservative
    # "we found something but don't know how good" stand-in.
    return 50.0


def _pick_public_inbox(emails: list[Any]) -> dict[str, Any] | None:
    """Prefer generic inboxes (info@, contact@) for org-level hits."""
    generic: dict[str, Any] | None = None
    first: dict[str, Any] | None = None
    for entry in emails:
        if not isinstance(entry, dict):
            continue
        if first is None:
            first = entry
        if str(entry.get("type") or "").lower() == "generic":
            generic = entry
            break
    return generic or first
