"""Apollo people-search client used as a name-only fallback when FOCUS
extraction returns no executives.

PRD constraint (CRITICAL): NAMES ONLY. We do not pull, parse, or persist
email, phone, LinkedIn, or any other contact channel from Apollo. The
upstream response is rich; this module trims to ``first_name`` +
``last_name`` + ``officer_rank`` at the parser boundary so downstream
code can never see the rest. The CSV export rule allows executive names
only and the new fallback path must respect that even though the legacy
``ExecutiveContactService`` (services/contacts.py) takes the wider read.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Final

import httpx

logger = logging.getLogger(__name__)


# Apollo people-search endpoint. We deliberately use the search endpoint
# (and not /people/match or /enrich) because we only need names — match
# and enrich return contact channels that violate the names-only rule.
_APOLLO_PEOPLE_SEARCH_URL: Final = "https://api.apollo.io/api/v1/mixed_people/search"

# Apollo organizations-search endpoint, used as the fallback for
# ``broker_dealer.website`` when FINRA's Form BD Web Address is empty. The
# response is rich (location, employee_count, founded_year, technologies,
# etc.); the parser below trims to ``name`` + ``website_url`` + primary
# domain so nothing else can leak through this module.
_APOLLO_ORGS_SEARCH_URL: Final = "https://api.apollo.io/api/v1/organizations/search"

# Officer-rank titles passed to Apollo. Kept tight to senior officer titles
# so the search returns the people the FOCUS extraction *would* have
# returned if the PDF had named them.
_OFFICER_TITLES: Final = (
    "CEO",
    "Chief Executive Officer",
    "President",
    "COO",
    "Chief Operating Officer",
    "CFO",
    "Chief Financial Officer",
)

# Map a free-text Apollo title to a normalized officer-rank slug. The slug
# powers the FE "officer rank" badge and is the only title information we
# persist (we do not store the full Apollo title verbatim).
_RANK_MAP: Final = (
    ("chief executive officer", "ceo"),
    ("ceo", "ceo"),
    ("president", "president"),
    ("chief operating officer", "coo"),
    ("coo", "coo"),
    ("chief financial officer", "cfo"),
    ("cfo", "cfo"),
)

_DEFAULT_TIMEOUT_S: Final = 10.0
_DEFAULT_MAX_ATTEMPTS: Final = 3
_BACKOFF_BASE_S: Final = 0.5
_BACKOFF_JITTER_S: Final = 0.25
_RESULT_LIMIT: Final = 10


class ApolloError(Exception):
    """Raised when Apollo returns a non-recoverable error.

    The caller catches this to mark the firm's executive enrichment as
    ``provider_error`` instead of silently treating it as "no executives
    found" — which would otherwise hide a transient outage behind the
    same empty-result UI as a genuine no-match.
    """


@dataclass(slots=True, frozen=True)
class ApolloExecutive:
    """Name-only view of an Apollo person.

    Trimmed at the parser boundary — email, phone, linkedin_url and the
    rest of Apollo's payload never escape this module. ``officer_rank``
    is one of ``ceo`` | ``president`` | ``coo`` | ``cfo`` | ``other``.
    """

    first_name: str
    last_name: str
    officer_rank: str


@dataclass(slots=True, frozen=True)
class ApolloOrganization:
    """Website-only view of an Apollo organization.

    Used to backfill ``broker_dealer.website`` when FINRA's Form BD Web
    Address is empty. Trimmed at the parser boundary — Apollo's response
    includes phone, address, employee_count, founded_year, technologies,
    funding history, and more; none of that survives this module.
    ``website_url`` may be None when the matched org has no public site,
    in which case ``domain`` may still carry the primary domain (often a
    bare ``example.com`` we can prefix with ``https://``).
    """

    name: str
    website_url: str | None
    domain: str | None


class ApolloClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if not api_key:
            raise ValueError("Apollo API key is required")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)

    async def search_executives(
        self,
        firm_name: str,
        crd: str | None = None,
    ) -> list[ApolloExecutive]:
        """Search Apollo for officer-rank people at ``firm_name``.

        Retries on 429 + 5xx + network errors with exponential backoff +
        jitter. Raises ``ApolloError`` after retries are exhausted (or
        immediately on a 4xx that isn't 429) so the caller can mark the
        firm as ``provider_error``. Returns names + officer rank only —
        no contact channels ever leave this module.
        """
        firm_name = firm_name.strip()
        if not firm_name:
            return []

        payload: dict[str, object] = {
            "q_organization_name": firm_name,
            "page": 1,
            "per_page": _RESULT_LIMIT,
            "person_titles": list(_OFFICER_TITLES),
        }
        # CRD is informational on the people-search endpoint; passing it as
        # a free-text keyword improves matching when several firms share a
        # name. Apollo silently ignores unknown query params, so this is
        # safe even on plans where ``q_keywords`` isn't first-class.
        if crd:
            payload["q_keywords"] = f"CRD {crd}"

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self._api_key,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(
                        _APOLLO_PEOPLE_SEARCH_URL,
                        headers=headers,
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Apollo search network error for '%s' (attempt %d/%d): %s",
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

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = ApolloError(
                    f"Apollo returned {response.status_code}"
                )
                logger.warning(
                    "Apollo search transient error %d for '%s' (attempt %d/%d)",
                    response.status_code,
                    firm_name,
                    attempt,
                    self._max_attempts,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            raise ApolloError(
                f"Apollo returned {response.status_code} for '{firm_name}'"
            )

        raise ApolloError(
            f"Apollo retries exhausted for '{firm_name}': {last_error}"
        )

    async def search_organization(
        self,
        firm_name: str,
        crd: str | None = None,
    ) -> ApolloOrganization | None:
        """Search Apollo for the organization matching ``firm_name``.

        Hits ``POST /v1/organizations/search`` and returns the top match's
        name + website url + primary domain. Returns ``None`` when Apollo
        responds with no organizations matching the query — that is the
        normal "we just don't know this firm" path; the firm's website
        stays NULL on the broker_dealer row.

        Retries on 429 + 5xx + network errors with the same exponential
        backoff + jitter as :meth:`search_executives`. Raises
        ``ApolloError`` after retries are exhausted (or immediately on a
        non-retryable 4xx) so the caller can mark the firm as a
        provider-error review item rather than caching the empty result.

        ``crd`` is appended as a free-text keyword so we get a sharper
        match when several firms share a name. Apollo silently ignores
        unknown query params, so passing it is safe even on plans where
        ``q_keywords`` isn't first-class.
        """
        firm_name = firm_name.strip()
        if not firm_name:
            return None

        payload: dict[str, object] = {
            "q_organization_name": firm_name,
            "page": 1,
            "per_page": _RESULT_LIMIT,
        }
        if crd:
            payload["q_keywords"] = f"CRD {crd}"

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self._api_key,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(
                        _APOLLO_ORGS_SEARCH_URL,
                        headers=headers,
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Apollo organizations network error for '%s' (attempt %d/%d): %s",
                    firm_name,
                    attempt,
                    self._max_attempts,
                    exc,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            if response.status_code == 200:
                return self._parse_organization(response.json(), firm_name)

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = ApolloError(
                    f"Apollo organizations returned {response.status_code}"
                )
                logger.warning(
                    "Apollo organizations transient error %d for '%s' (attempt %d/%d)",
                    response.status_code,
                    firm_name,
                    attempt,
                    self._max_attempts,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            raise ApolloError(
                f"Apollo organizations returned {response.status_code} for '{firm_name}'"
            )

        raise ApolloError(
            f"Apollo organizations retries exhausted for '{firm_name}': {last_error}"
        )

    @staticmethod
    def _parse_organization(
        payload: object, firm_name: str
    ) -> ApolloOrganization | None:
        """Trim the Apollo organizations payload to name + website + domain.

        Apollo returns the matched org under ``organizations`` (list);
        ``accounts`` is also present on some plans for org-search, with the
        same shape. We pick the first entry. If neither key is populated we
        return None so the caller treats it as "no match" (website stays
        NULL on the broker_dealer row, identical to the FINRA-empty path).
        """
        if not isinstance(payload, dict):
            return None
        orgs = payload.get("organizations")
        if not isinstance(orgs, list) or not orgs:
            orgs = payload.get("accounts")
        if not isinstance(orgs, list):
            return None
        for org in orgs:
            if not isinstance(org, dict):
                continue
            name = str(org.get("name") or firm_name).strip() or firm_name
            website_raw = org.get("website_url")
            domain_raw = org.get("primary_domain") or org.get("domain")
            website = str(website_raw).strip() if website_raw else None
            domain = str(domain_raw).strip() if domain_raw else None
            return ApolloOrganization(
                name=name,
                website_url=website or None,
                domain=domain or None,
            )
        return None

    @staticmethod
    async def _backoff(attempt: int) -> None:
        base = _BACKOFF_BASE_S * (2 ** (attempt - 1))
        await asyncio.sleep(base + random.uniform(0, _BACKOFF_JITTER_S))

    @staticmethod
    def _parse(payload: object) -> list[ApolloExecutive]:
        people = payload.get("people") if isinstance(payload, dict) else None
        if not isinstance(people, list):
            return []

        results: list[ApolloExecutive] = []
        seen: set[tuple[str, str]] = set()
        for person in people:
            if not isinstance(person, dict):
                continue

            first = str(person.get("first_name") or "").strip()
            last = str(person.get("last_name") or "").strip()
            if not first or not last:
                # Apollo sometimes returns only a combined ``name`` field.
                full = str(person.get("name") or "").strip()
                if not full:
                    continue
                parts = full.split(maxsplit=1)
                first = parts[0]
                last = parts[1] if len(parts) > 1 else ""
                if not last:
                    continue

            key = (first.lower(), last.lower())
            if key in seen:
                continue
            seen.add(key)

            results.append(
                ApolloExecutive(
                    first_name=first,
                    last_name=last,
                    officer_rank=_classify_rank(person.get("title")),
                )
            )

        return results


def _classify_rank(title: object) -> str:
    if not isinstance(title, str):
        return "other"
    lowered = title.strip().lower()
    if not lowered:
        return "other"
    for needle, slug in _RANK_MAP:
        if needle in lowered:
            return slug
    return "other"
