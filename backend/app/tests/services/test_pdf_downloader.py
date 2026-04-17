"""Tests for pdf_downloader: SSRF guard (S-1) + error-path coverage (S-4).

Ref: .claude/focus-fix/diagnosis.md §9 tickets S-1 and S-4.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.pdf_downloader import PdfDownloaderService, _validate_sec_url


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.pdf_downloader.asyncio.sleep", _instant_sleep)


@pytest.fixture
def patch_sec_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sec_request_max_retries", 3)
    monkeypatch.setattr(settings, "sec_request_timeout_seconds", 5.0)


class TestValidateSecUrlHappyPath:
    """Valid SEC URLs must pass unchanged."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/primary.pdf",
            "https://data.sec.gov/submissions/CIK0001234567.json",
            "https://efts.sec.gov/LATEST/search-index?q=foo",
            # Port is allowed as long as the scheme/host are valid
            "https://www.sec.gov:443/Archives/",
            # Query strings and fragments don't change host or scheme
            "https://data.sec.gov/submissions/CIK.json?v=2&trace=1",
        ],
    )
    def test_allowed_hosts_pass(self, url: str) -> None:
        # Should not raise
        _validate_sec_url(url)


class TestValidateSecUrlRejectsBadSchemes:
    """Only HTTPS is permitted."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://www.sec.gov/Archives/",
            "ftp://www.sec.gov/file.pdf",
            "file:///etc/passwd",
            "gopher://169.254.169.254/",
        ],
    )
    def test_non_https_rejected(self, url: str) -> None:
        with pytest.raises(ValueError, match="Only HTTPS"):
            _validate_sec_url(url)


class TestValidateSecUrlRejectsNonSecHosts:
    """Allowlist is strict — all other hostnames must fail."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com/Archives/",
            "https://attacker.example.com/",
            # SEC-lookalike typosquats
            "https://www.sec.gov.evil.com/",
            "https://www-sec.gov/",
            "https://sec.gov/",  # missing www. / data. / efts. subdomain
            # Subdomain shenanigans
            "https://foo.www.sec.gov/",
            "https://www.sec.gov.attacker.com/",
        ],
    )
    def test_non_sec_host_rejected(self, url: str) -> None:
        with pytest.raises(ValueError, match="not in the SEC allowlist"):
            _validate_sec_url(url)


class TestValidateSecUrlRejectsPrivateIpLiterals:
    """IP literals in private/reserved ranges must be rejected even if the
    scheme check somehow passes (defense-in-depth: the allowlist check
    rejects all IP literals in practice, since the allowlist is hostnames)."""

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # GCP/AWS metadata server (link-local)
            "127.0.0.1",  # loopback
            "10.0.0.1",  # private RFC1918
            "172.16.0.1",  # private RFC1918
            "192.168.1.1",  # private RFC1918
            "224.0.0.1",  # multicast
            "::1",  # IPv6 loopback
            "fe80::1",  # IPv6 link-local
        ],
    )
    def test_private_ip_rejected_via_allowlist(self, ip: str) -> None:
        # Bracket IPv6 literals in URLs per RFC 3986.
        host = f"[{ip}]" if ":" in ip else ip
        url = f"https://{host}/anything"
        # First-line defense: not in allowlist.
        with pytest.raises(ValueError, match="not in the SEC allowlist"):
            _validate_sec_url(url)


class TestValidateSecUrlRejectsMalformedInput:
    """Defensive edge cases."""

    @pytest.mark.parametrize(
        "url",
        [
            "",  # empty string — urlparse gives scheme=''
            "https://",  # no host
            "not-a-url",  # urlparse gives scheme=''
            "https:///path-without-host",  # no host
        ],
    )
    def test_malformed_rejected(self, url: str) -> None:
        with pytest.raises(ValueError):
            _validate_sec_url(url)


class TestValidateSecUrlUserinfoEdge:
    """RFC 3986 userinfo handling. `urlparse` splits on the LAST `@`, so
    ``https://user@evil.com@www.sec.gov/`` yields hostname ``www.sec.gov``.
    That is also the host DNS/httpx will resolve — so this URL genuinely
    targets SEC and the allowlist accept is correct. Lock this behavior in
    with an explicit test so a future refactor (e.g. a switch to a stricter
    URL parser) does not accidentally drop it or, worse, mis-parse it."""

    def test_userinfo_with_sec_host_accepts(self) -> None:
        # DNS resolves the final hostname, not the credentials — this is SEC.
        _validate_sec_url("https://user@www.sec.gov/Archives/")

    def test_userinfo_masquerade_rejected(self) -> None:
        # No matter how credentials are stuffed in, if the FINAL hostname
        # is attacker.com, DNS resolves attacker.com and the allowlist rejects.
        with pytest.raises(ValueError, match="not in the SEC allowlist"):
            _validate_sec_url("https://www.sec.gov@attacker.com/")


# ─────────────────── S-4: error-path integration tests ────────────────


_SEC_JSON_URL = "https://data.sec.gov/submissions/CIK0001234567.json"
_SEC_PDF_URL = "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/primary.pdf"


class TestGetJsonWithRetriesErrorPaths:
    """SEC JSON endpoint failure modes: 404, 403, timeout, malformed JSON."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_exhausts_retries_then_raises(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        route = respx.get(_SEC_JSON_URL).mock(return_value=httpx.Response(404))
        downloader = PdfDownloaderService()

        with pytest.raises(httpx.HTTPStatusError):
            await downloader._get_json_with_retries(_SEC_JSON_URL)

        # Retry loop runs all attempts for HTTPStatusError (no retryable-only
        # filter in this method), so call_count == max_retries.
        assert route.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_read_timeout_propagates(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        respx.get(_SEC_JSON_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        downloader = PdfDownloaderService()

        with pytest.raises(httpx.ReadTimeout):
            await downloader._get_json_with_retries(_SEC_JSON_URL)

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_dict_json_raises_runtime(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        respx.get(_SEC_JSON_URL).mock(return_value=httpx.Response(200, json=["not", "a", "dict"]))
        downloader = PdfDownloaderService()

        with pytest.raises(RuntimeError, match="unexpected JSON payload"):
            await downloader._get_json_with_retries(_SEC_JSON_URL)

    @pytest.mark.asyncio
    async def test_validator_fires_before_any_http_call(
        self, patch_sec_retries: None
    ) -> None:
        """A bad URL must raise ValueError at the top of the method,
        NEVER reaching httpx. This is the SSRF guard's promise."""
        downloader = PdfDownloaderService()
        # assert_all_called=False because we EXPECT the route not to be hit —
        # if the validator ever regresses and lets the call through, the
        # route WILL be called and we can assert count == 0 separately.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.route(url__regex=r".*")
            with pytest.raises(ValueError, match="not in the SEC allowlist"):
                await downloader._get_json_with_retries("https://attacker.com/")
            assert route.call_count == 0  # No HTTP call happened.


class TestDownloadBytesErrorPaths:
    """SEC PDF download failure modes."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_exhausts_retries(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        route = respx.get(_SEC_PDF_URL).mock(return_value=httpx.Response(403))
        downloader = PdfDownloaderService()

        with pytest.raises(httpx.HTTPStatusError):
            await downloader._download_bytes_with_retries(_SEC_PDF_URL)

        assert route.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_pdf_content_raises_runtime(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """SEC occasionally returns an HTML interstitial instead of the PDF.
        The bytes path detects this by content-type + .pdf suffix heuristic."""
        respx.get("https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/weird")\
            .mock(return_value=httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html/>"))
        downloader = PdfDownloaderService()

        with pytest.raises(RuntimeError, match="did not resolve to a PDF"):
            await downloader._download_bytes_with_retries(
                "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/weird"
            )

    @respx.mock
    @pytest.mark.asyncio
    async def test_follow_redirects_false_surfaces_302_as_error(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """Per S-1, follow_redirects=False is now set. A 302 is now a 3xx
        status without body — `raise_for_status()` does NOT raise on 3xx,
        but the content-type / .pdf suffix heuristic will fire because
        the 302 response body won't contain PDF bytes."""
        respx.get(_SEC_PDF_URL).mock(
            return_value=httpx.Response(
                302,
                headers={"location": "https://evil.com/pdf", "content-type": "text/html"},
                content=b"",
            )
        )
        downloader = PdfDownloaderService()

        # Either the content-type check raises RuntimeError, or ultimately
        # we don't silently follow to evil.com. Either outcome is correct.
        with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
            await downloader._download_bytes_with_retries(_SEC_PDF_URL)

