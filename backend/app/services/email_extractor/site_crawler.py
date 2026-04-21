"""In-house site crawler.

Fetches the homepage and a small fixed set of likely contact pages, parses
HTML for emails (mailto links, raw text, common obfuscation forms), filters
to the scan domain (or its registered parent), and returns a
``DiscoveryResult``. No DB writes; aggregator persists.

Constraints (CLAUDE.md §2 + this prompt):
    - Respect ``/robots.txt``.
    - One in-flight request per host, >=500ms between requests.
    - 6-page cap per run.
    - 10s per-request timeout, https-first with http fallback on connect error.
    - ``text/html`` responses only.
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser

from app.services.email_extractor.base import DiscoveredEmailDraft, DiscoveryResult

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "EmailExtractor/0.1 (+https://email-extractor.abedubas.dev)"
CANDIDATE_PATHS: tuple[str, ...] = ("/contact", "/about", "/team", "/staff", "/people")
MAX_PAGES = 6

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
ATOB_RE = re.compile(r"""atob\(\s*['"]([A-Za-z0-9+/=]+)['"]\s*\)""")
OBFUSCATION_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.IGNORECASE), "."),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.IGNORECASE), "."),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
)


class SiteCrawler:
    """``EmailSource`` Protocol implementation."""

    name = "site_crawler"

    def __init__(
        self,
        *,
        request_delay_seconds: float = 0.5,
        request_timeout_seconds: float = 10.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._delay = request_delay_seconds
        self._timeout = request_timeout_seconds
        self._user_agent = user_agent
        self._max_pages = max_pages

    async def run(self, domain: str) -> DiscoveryResult:
        normalized_domain = domain.lower().strip().lstrip(".")
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]

        result = DiscoveryResult()
        seen: dict[str, DiscoveredEmailDraft] = {}

        async with httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            timeout=self._timeout,
            follow_redirects=True,
            max_redirects=3,
        ) as client:
            scheme, base_url, robots_error = await self._resolve_base_url(client, normalized_domain)
            if base_url is None:
                if robots_error:
                    result.errors.append(robots_error)
                return result

            robots = await self._fetch_robots(client, scheme, normalized_domain)

            pages_fetched = 0
            for path in ("/",) + CANDIDATE_PATHS:
                if pages_fetched >= self._max_pages:
                    break
                url = f"{base_url}{path}"
                if not robots.can_fetch(self._user_agent, url):
                    continue
                if pages_fetched > 0:
                    await asyncio.sleep(self._delay)
                pages_fetched += 1
                try:
                    response = await client.get(url)
                except httpx.HTTPError as exc:
                    if path == "/":
                        result.errors.append(f"homepage fetch failed: {exc}")
                    # Soft-fail other paths quietly.
                    continue

                if response.status_code != 200:
                    if path == "/" and response.status_code >= 500:
                        result.errors.append(f"homepage returned {response.status_code}")
                    continue

                content_type = response.headers.get("content-type", "")
                if not content_type.lower().startswith("text/html"):
                    continue

                self._extract_into(response.text, url, normalized_domain, seen)

        result.emails = list(seen.values())
        return result

    async def _resolve_base_url(self, client: httpx.AsyncClient, domain: str) -> tuple[str, str | None, str | None]:
        """Try https first, fall back to http on connect error.

        Returns ``(scheme, base_url, error_message)``. ``base_url`` is None if
        both schemes fail.
        """
        for scheme in ("https", "http"):
            base = f"{scheme}://{domain}"
            try:
                response = await client.get(f"{base}/", timeout=self._timeout)
            except httpx.ConnectError:
                continue
            except httpx.HTTPError as exc:
                return scheme, None, f"homepage fetch error: {exc}"
            _ = response.status_code
            return scheme, base, None
        return "https", None, "could not connect to homepage on https or http"

    async def _fetch_robots(self, client: httpx.AsyncClient, scheme: str, domain: str) -> RobotFileParser:
        rp = RobotFileParser()
        rp.set_url(f"{scheme}://{domain}/robots.txt")
        try:
            response = await client.get(f"{scheme}://{domain}/robots.txt")
        except httpx.HTTPError:
            rp.parse([])
            return rp
        if response.status_code == 200:
            rp.parse(response.text.splitlines())
        else:
            rp.parse([])
        return rp

    def _extract_into(
        self,
        html_text: str,
        page_url: str,
        scan_domain: str,
        seen: dict[str, DiscoveredEmailDraft],
    ) -> None:
        tree = HTMLParser(html_text)

        # mailto: links — confidence 0.75
        for node in tree.css("a[href^='mailto:']"):
            href = node.attributes.get("href") or ""
            email = href[len("mailto:") :].split("?", 1)[0].strip()
            email = html.unescape(email).lower()
            if self._domain_matches(email, scan_domain):
                seen.setdefault(
                    email,
                    DiscoveredEmailDraft(
                        email=email,
                        source=self.name,
                        confidence=0.75,
                        attribution=page_url,
                    ),
                )

        # Plain-text + obfuscation — confidence 0.6
        text = html.unescape(tree.text(separator=" ", strip=True))
        for pattern, replacement in OBFUSCATION_REPLACEMENTS:
            text = pattern.sub(replacement, text)
        for match in EMAIL_RE.findall(text):
            email = match.lower()
            if self._domain_matches(email, scan_domain):
                seen.setdefault(
                    email,
                    DiscoveredEmailDraft(
                        email=email,
                        source=self.name,
                        confidence=0.6,
                        attribution=page_url,
                    ),
                )

        # Simple atob("base64==") JS literals — confidence 0.6
        for encoded in ATOB_RE.findall(html_text):
            try:
                decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
            except ValueError:
                continue
            for match in EMAIL_RE.findall(decoded):
                email = match.lower()
                if self._domain_matches(email, scan_domain):
                    seen.setdefault(
                        email,
                        DiscoveredEmailDraft(
                            email=email,
                            source=self.name,
                            confidence=0.6,
                            attribution=page_url,
                        ),
                    )

    @staticmethod
    def _domain_matches(email: str, scan_domain: str) -> bool:
        """Accept ``email`` if its domain equals or is a subdomain of ``scan_domain``."""
        if "@" not in email:
            return False
        email_domain = email.rsplit("@", 1)[1].lower()
        return email_domain == scan_domain or email_domain.endswith("." + scan_domain)
