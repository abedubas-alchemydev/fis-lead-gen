"""Snov.io reverse-email enrichment provider (``/v1/get-profile-by-email``).

Third link in the default chain. Snov's "get profile by email" returns the
person behind an address synchronously (no polling, unlike Snov's name->email
finder used in contact-discovery). It bills 1 credit only when it actually
finds a profile. The response is flat (not wrapped in ``data``):

    name / firstName / lastName
    currentJobs[].position      -> title
    currentJobs[].companyName   -> company
    social[].link (type "linkedIn")  -> linkedin_url

No phone is returned by this endpoint, so the provider leaves it ``None`` for a
later link (or Apollo's webhook) to fill.

Snov uses OAuth2 client-credentials. A small module-level token cache (with TTL
+ 401-triggered refresh) keeps us off ``/oauth/access_token`` on every call --
mirroring ``contact_discovery/snov.py`` but kept separate so the enrichment
package stays self-contained. Requires ``snov_client_id`` + ``snov_client_secret``;
without them the provider is skipped.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services.email_extractor.enrichment.base import (
    EmailEnrichmentProvider,
    EnrichmentData,
    EnrichmentError,
    clean_str,
)

logger = logging.getLogger(__name__)

OAUTH_URL = "https://api.snov.io/v1/oauth/access_token"
PROFILE_BY_EMAIL_URL = "https://api.snov.io/v1/get-profile-by-email"

# Snov tokens nominally live 3600s; shave 60s to avoid an expiry race.
_TOKEN_SAFETY_MARGIN_SECONDS = 60.0

# Module-level token cache. Same single-flight-ish reasoning as the discovery
# provider: the worst-case race just mints two valid tokens, last write wins.
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _reset_token_cache_for_tests() -> None:
    """Hook used by tests to force a fresh token fetch."""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


class SnovEnrichProvider(EmailEnrichmentProvider):
    name = "snov"

    def is_configured(self) -> bool:
        return bool(settings.snov_client_id and settings.snov_client_secret)

    async def enrich(self, email: str, needed: set[str] | None = None) -> EnrichmentData | None:
        # One call returns the whole profile; ``needed`` is irrelevant.
        client_id = settings.snov_client_id
        client_secret = settings.snov_client_secret
        if not client_id or not client_secret:
            return None

        token = await _get_token(client_id, client_secret)
        if token is None:
            raise EnrichmentError("snov: oauth failed")

        response = await _post_profile(email, token, client_id, client_secret)
        if response is None:
            raise EnrichmentError("snov: network error")
        # 404 / not-found -> clean miss; other non-200 -> retryable error.
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise EnrichmentError(f"snov http {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("success") is False:
            return None

        title, company = _job(payload)
        result = EnrichmentData(
            name=_name(payload),
            title=title,
            company=company,
            linkedin_url=_linkedin(payload),
        )
        return result if result.has_any() else None


async def _post_profile(
    email: str, token: str, client_id: str, client_secret: str
) -> httpx.Response | None:
    """POST get-profile-by-email with the bearer token; refresh + retry once on
    401 (token expired server-side or credentials rotated mid-flight)."""
    body = {"email": email}
    try:
        async with httpx.AsyncClient(timeout=settings.snov_request_timeout) as client:
            response = await client.post(
                PROFILE_BY_EMAIL_URL, json=body, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        logger.warning("Snov get-profile-by-email failed: %r", exc)
        return None

    if response.status_code != 401:
        return response

    _reset_token_cache_for_tests()
    new_token = await _refresh_token(client_id, client_secret)
    if new_token is None:
        return response
    try:
        async with httpx.AsyncClient(timeout=settings.snov_request_timeout) as client:
            return await client.post(
                PROFILE_BY_EMAIL_URL, json=body, headers={"Authorization": f"Bearer {new_token}"}
            )
    except httpx.HTTPError as exc:
        logger.warning("Snov get-profile-by-email retry failed: %r", exc)
        return None


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
        async with httpx.AsyncClient(timeout=settings.snov_request_timeout) as client:
            response = await client.post(OAUTH_URL, json=body)
    except httpx.HTTPError as exc:
        logger.warning("Snov OAuth request failed: %r", exc)
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


def _name(payload: dict[str, Any]) -> str | None:
    full = clean_str(payload.get("name"))
    if full:
        return full
    first, last = clean_str(payload.get("firstName")), clean_str(payload.get("lastName"))
    if first and last:
        return f"{first} {last}"
    return first or last


def _job(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    jobs = payload.get("currentJobs")
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict):
                position = clean_str(job.get("position"))
                company = clean_str(job.get("companyName"))
                if position or company:
                    return position, company
    return None, None


def _linkedin(payload: dict[str, Any]) -> str | None:
    socials = payload.get("social")
    if isinstance(socials, list):
        for entry in socials:
            if isinstance(entry, dict) and "linkedin" in str(entry.get("type") or "").lower():
                link = clean_str(entry.get("link"))
                if link:
                    return link
    return None
