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

from app.services.email_extractor.base import (
    DiscoveredEmailDraft,
    DiscoveredPhoneDraft,
    DiscoveryResult,
    PageText,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "EmailExtractor/0.1 (+https://email-extractor.abedubas.dev)"
CANDIDATE_PATHS: tuple[str, ...] = ("/contact", "/about", "/team", "/staff", "/people")
MAX_PAGES = 6

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Conservative US/NANP phone matcher: optional +1, then 3-3-4 digit groups that
# MUST carry a separator between groups (space, dot, or hyphen; area code may be
# parenthesized). Requiring separators avoids matching long opaque digit runs
# (IDs, CRDs, ZIP+route strings). ``_clean_phone`` validates the digit count.
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}"
)
ATOB_RE = re.compile(r"""atob\(\s*['"]([A-Za-z0-9+/=]+)['"]\s*\)""")
OBFUSCATION_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.IGNORECASE), "."),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.IGNORECASE), "."),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
)


def _clean_phone(raw: str) -> str | None:
    """Normalize a matched phone to canonical US format, or None if not NANP.

    Strips to digits; accepts 10-digit numbers (or 11 starting with the US
    country code ``1``) and renders ``(AAA) PPP-NNNN`` so different spacings of
    the same number dedupe. Anything else (too few/many digits, intl) → None.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


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
        collect_page_text: bool = False,
    ) -> None:
        self._delay = request_delay_seconds
        self._timeout = request_timeout_seconds
        self._user_agent = user_agent
        self._max_pages = max_pages
        # When True, ``run`` retains each page's normalized text in
        # ``result.pages`` so callers can do co-location (attach a phone to a
        # person named on the same page). Off by default to avoid holding text.
        self._collect_page_text = collect_page_text

    async def run(self, domain: str) -> DiscoveryResult:
        normalized_domain = domain.lower().strip().lstrip(".")
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]

        result = DiscoveryResult()
        seen: dict[str, DiscoveredEmailDraft] = {}
        seen_phones: dict[str, DiscoveredPhoneDraft] = {}

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

                self._extract_into(
                    response.text, url, normalized_domain, seen, seen_phones, result
                )

        result.emails = list(seen.values())
        result.phones = list(seen_phones.values())
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
        seen_phones: dict[str, DiscoveredPhoneDraft],
        result: DiscoveryResult,
    ) -> None:
        tree = HTMLParser(html_text)
        raw_text = html.unescape(tree.text(separator=" ", strip=True))

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

        # tel: links — confidence 0.75
        for node in tree.css("a[href^='tel:']"):
            href = node.attributes.get("href") or ""
            raw = href[len("tel:") :].split("?", 1)[0].strip()
            phone = _clean_phone(html.unescape(raw))
            if phone:
                seen_phones.setdefault(
                    phone,
                    DiscoveredPhoneDraft(
                        value=phone,
                        source=self.name,
                        confidence=0.75,
                        attribution=page_url,
                    ),
                )

        # Plain-text emails + obfuscation — confidence 0.6. Work on a copy so the
        # at/dot substitutions don't perturb the raw text used for phones/pages.
        email_text = raw_text
        for pattern, replacement in OBFUSCATION_REPLACEMENTS:
            email_text = pattern.sub(replacement, email_text)
        for match in EMAIL_RE.findall(email_text):
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

        # Plain-text phones — confidence 0.6. Unlike emails, phones aren't
        # domain-filterable, so precision rests on the conservative PHONE_RE +
        # _clean_phone validation; person attribution happens downstream.
        for match in PHONE_RE.findall(raw_text):
            phone = _clean_phone(match)
            if phone:
                seen_phones.setdefault(
                    phone,
                    DiscoveredPhoneDraft(
                        value=phone,
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

        if self._collect_page_text:
            result.pages.append(PageText(url=page_url, text=raw_text))

    @staticmethod
    def _domain_matches(email: str, scan_domain: str) -> bool:
        """Accept ``email`` if its domain equals or is a subdomain of ``scan_domain``."""
        if "@" not in email:
            return False
        email_domain = email.rsplit("@", 1)[1].lower()
        return email_domain == scan_domain or email_domain.endswith("." + scan_domain)
