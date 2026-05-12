"""EDGAR EFTS query for Form 13F-HR filer enumeration.

Used by the advisor master-list ingest pipeline to flag IAPD records
whose CIK has filed a Form 13F-HR (institutional-investment-manager
holdings report) inside the configured lookback window.

The endpoint:

    https://efts.sec.gov/LATEST/search-index
        ?q=&forms=13F-HR
        &dateRange=custom&startdt=YYYY-MM-DD&enddt=YYYY-MM-DD
        &size=100&from=N

EFTS is the undocumented-but-public API behind EDGAR full-text search.
Page size is capped at 100; ``from + size`` is hard-capped at 10000
(Elasticsearch default). 13F-HR over 90 days easily exceeds that, so
the service partitions by ``thirteen_f_partition_days`` (default 7)
weekly windows and dedupes by CIK across windows.

Returns a ``dict[cik → latest filing date]`` so the merge step can
populate both ``files_13f`` and ``latest_13f_filing_date`` in one pass.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)


_PAGE_SIZE = 100
# Elasticsearch default deep-pagination cap. Hitting this is a signal
# that the partition window was too wide — we log a warning so the
# operator can shrink ``thirteen_f_partition_days`` if it ever fires.
_MAX_FROM_PLUS_SIZE = 10_000

_REQUEST_HEADERS = {
    "User-Agent": settings.sec_user_agent,
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}


class ThirteenFFilterService:
    """EDGAR EFTS-based 13F-HR filer enumeration."""

    async def fetch_recent_filer_ciks(
        self,
        *,
        lookback_days: int | None = None,
        partition_days: int | None = None,
        as_of: date | None = None,
    ) -> dict[str, date]:
        """Return a CIK → latest-filing-date map for recent 13F-HR filers.

        Walks weekly partitions across the lookback window so each EFTS
        query stays well under the 10k deep-pagination cap. Each CIK
        appearing in multiple partitions is kept once with the most
        recent ``file_date``.

        ``as_of`` defaults to today; passing a fixed date makes tests
        deterministic.
        """

        lookback = lookback_days or settings.thirteen_f_lookback_days
        partition = partition_days or settings.thirteen_f_partition_days
        end = as_of or date.today()
        start = end - timedelta(days=lookback)

        windows = list(_partition_window(start, end, partition))
        logger.info(
            "Fetching 13F-HR filers in %s..%s across %d weekly windows.",
            start.isoformat(),
            end.isoformat(),
            len(windows),
        )

        latest_by_cik: dict[str, date] = {}
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        sleep_between_requests = 1.0 / max(settings.edgar_rate_limit_per_second, 1)

        async with httpx.AsyncClient(
            timeout=timeout, headers=_REQUEST_HEADERS, follow_redirects=True
        ) as client:
            for window_start, window_end in windows:
                try:
                    window_results = await self._fetch_window(
                        client, window_start, window_end, sleep_between_requests
                    )
                except httpx.HTTPError as exc:
                    # EFTS occasionally 500s on deep-pagination requests
                    # for high-volume weeks (peak 13F-HR filing windows
                    # are Feb 14 / May 15 / Aug 14 / Nov 14). Treat the
                    # window as a partial loss rather than failing the
                    # whole pipeline — neighboring windows still cover
                    # most filers, and the next monthly run picks up
                    # whatever leaked.
                    logger.warning(
                        "13F-HR window %s..%s failed (%s); "
                        "skipping window — counts may underestimate.",
                        window_start.isoformat(),
                        window_end.isoformat(),
                        exc,
                    )
                    continue
                for cik, filing_date in window_results.items():
                    existing = latest_by_cik.get(cik)
                    if existing is None or filing_date > existing:
                        latest_by_cik[cik] = filing_date

        logger.info(
            "13F-HR filer enumeration complete — %d distinct CIKs.",
            len(latest_by_cik),
        )
        return latest_by_cik

    async def _fetch_window(
        self,
        client: httpx.AsyncClient,
        window_start: date,
        window_end: date,
        sleep_between_requests: float,
    ) -> dict[str, date]:
        """Page through one date window, return CIK → latest-date map."""

        results: dict[str, date] = {}
        offset = 0
        while True:
            if offset + _PAGE_SIZE > _MAX_FROM_PLUS_SIZE:
                # Should never happen with weekly partitions, but if it
                # does, log loudly so the operator can tighten
                # ``thirteen_f_partition_days``. We bail out of THIS
                # window — the next window's CIKs are not lost.
                logger.warning(
                    "13F-HR window %s..%s hit deep-pagination cap; "
                    "shrink thirteen_f_partition_days. Skipping remainder.",
                    window_start.isoformat(),
                    window_end.isoformat(),
                )
                break

            params = {
                "q": "",
                "forms": "13F-HR",
                "dateRange": "custom",
                "startdt": window_start.isoformat(),
                "enddt": window_end.isoformat(),
                "size": _PAGE_SIZE,
                "from": offset,
            }
            response = await self._get_with_retries(client, params)
            payload = response.json()
            hits = payload.get("hits", {}).get("hits", [])
            if not hits:
                break

            for hit in hits:
                source = hit.get("_source") or {}
                cik_list = source.get("ciks") or []
                file_date_raw = source.get("file_date")
                file_date = _parse_iso_date(file_date_raw)
                if file_date is None:
                    continue
                for cik in cik_list:
                    normalized = _normalize_cik(cik)
                    if not normalized:
                        continue
                    existing = results.get(normalized)
                    if existing is None or file_date > existing:
                        results[normalized] = file_date

            if len(hits) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
            await asyncio.sleep(sleep_between_requests)

        logger.debug(
            "13F-HR window %s..%s — %d CIKs.",
            window_start.isoformat(),
            window_end.isoformat(),
            len(results),
        )
        return results

    async def _get_with_retries(
        self,
        client: httpx.AsyncClient,
        params: dict[str, object],
    ) -> httpx.Response:
        """GET with exponential backoff on transient failures."""

        max_retries = settings.iapd_request_max_retries
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.get(settings.sec_efts_search_url, params=params)
                if response.status_code == 429:
                    # Rate-limited — back off and retry. EDGAR's published
                    # limit is 10 req/sec; 429 means we're either over or
                    # the EFTS gateway has tighter local limits.
                    retry_after = float(response.headers.get("retry-after", 2 * attempt))
                    logger.warning(
                        "EFTS 429 on attempt %d/%d; sleeping %.1fs.",
                        attempt,
                        max_retries,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TransportError) as exc:
                if attempt == max_retries:
                    raise
                wait = min(2**attempt, 30)
                logger.warning(
                    "EFTS request failed (%s) on attempt %d/%d; sleeping %ds.",
                    exc,
                    attempt,
                    max_retries,
                    wait,
                )
                await asyncio.sleep(wait)

        # ``raise_for_status`` would have raised inside the loop on the
        # final attempt; this branch is just to satisfy static analyzers
        # that the function always returns or raises.
        raise RuntimeError("EFTS retry loop exited without a response or exception.")


def _partition_window(
    start: date, end: date, partition_days: int
) -> list[tuple[date, date]]:
    """Split [start, end] into half-open partitions of ``partition_days``.

    The last window may be shorter. Returned dates are inclusive on both
    ends because EFTS's ``startdt`` / ``enddt`` semantics are inclusive.
    """

    if partition_days < 1:
        raise ValueError("partition_days must be >= 1")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=partition_days - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _normalize_cik(value: object) -> str | None:
    """Strip leading zeros from a CIK string for comparison.

    EFTS returns CIKs as zero-padded 10-digit strings (``"0001521951"``),
    but the IAPD CSV's ``CIK#`` column uses unpadded integers
    (``"1521951"``). The merge join needs both sides in the same
    canonical form, so we strip leading zeros (and reject non-digits).
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    stripped = text.lstrip("0")
    return stripped or "0"


def _parse_iso_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
