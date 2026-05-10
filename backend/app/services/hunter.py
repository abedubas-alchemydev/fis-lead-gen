"""Hunter.io company-by-name lookup used as the second tier of the
firm-website resolver chain.

Distinct from ``services.contact_discovery.hunter`` — that module wraps
``/v2/email-finder`` and ``/v2/domain-search`` for email enrichment and
needs a known domain. This client is the *opposite* problem: we have a
firm name and need to find the domain. Hits the company-finder endpoint
with the firm name and trims the response to ``domain`` + ``name`` so
nothing else can leak through. The website resolver prefixes the bare
domain with ``https://`` and runs it through HEAD + blocklist + title
validation before persisting.

Optional/missing key path: when ``HUNTER_API_KEY`` is unset the resolver
should skip Hunter entirely without raising. The caller (resolver chain)
handles that — instantiating ``HunterClient`` with an empty key raises,
so the resolver checks ``settings.hunter_api_key`` first and only
constructs the client when it has one.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Final

import httpx

logger = logging.getLogger(__name__)


_HUNTER_COMPANY_FIND_URL: Final = "https://api.hunter.io/v2/companies/find"

_DEFAULT_TIMEOUT_S: Final = 5.0
_DEFAULT_MAX_ATTEMPTS: Final = 3
_BACKOFF_BASE_S: Final = 0.5
_BACKOFF_JITTER_S: Final = 0.25


class HunterError(Exception):
    """Raised when Hunter returns a non-recoverable error after retries."""


@dataclass(slots=True, frozen=True)
class HunterCompany:
    """Trimmed view of a Hunter company-find response.

    Hunter's company payload carries industry, employee bands, social
    handles, and other enrichment fields — none of that survives this
    module. The resolver only needs ``domain`` (to build the candidate
    URL) and ``name`` (logging context).
    """

    domain: str
    name: str


class HunterClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if not api_key:
            raise ValueError("Hunter API key is required")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)

    async def find_company(self, firm_name: str) -> HunterCompany | None:
        """Look up the matched company for ``firm_name``.

        Returns ``None`` when Hunter responds 200 with no match — that's
        the normal "we don't know this firm" path. Raises ``HunterError``
        on retries-exhausted 5xx/429 or a non-retryable 4xx so the
        resolver can record provider-error semantics rather than caching
        a false miss.
        """
        firm_name = firm_name.strip()
        if not firm_name:
            return None

        params = {"company": firm_name, "api_key": self._api_key}

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(
                        _HUNTER_COMPANY_FIND_URL, params=params
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Hunter company-find network error for '%s' (attempt %d/%d): %s",
                    firm_name,
                    attempt,
                    self._max_attempts,
                    exc,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            if response.status_code == 200:
                return self._parse(response.json())

            if response.status_code == 404:
                # Hunter returns 404 for unknown firms; treat as a clean
                # miss rather than an error so the resolver can fall
                # through without provider-error semantics.
                return None

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = HunterError(
                    f"Hunter returned {response.status_code}"
                )
                logger.warning(
                    "Hunter company-find transient error %d for '%s' (attempt %d/%d)",
                    response.status_code,
                    firm_name,
                    attempt,
                    self._max_attempts,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            raise HunterError(
                f"Hunter returned {response.status_code} for '{firm_name}'"
            )

        raise HunterError(
            f"Hunter retries exhausted for '{firm_name}': {last_error}"
        )

    @staticmethod
    def _parse(payload: object) -> HunterCompany | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        domain_raw = data.get("domain")
        if not domain_raw:
            return None
        domain = str(domain_raw).strip().lower()
        if not domain:
            return None
        name = str(data.get("name") or "").strip() or domain
        return HunterCompany(domain=domain, name=name)

    @staticmethod
    async def _backoff(attempt: int) -> None:
        base = _BACKOFF_BASE_S * (2 ** (attempt - 1))
        await asyncio.sleep(base + random.uniform(0, _BACKOFF_JITTER_S))
