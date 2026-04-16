"""Tests for the SSRF guard in `pdf_downloader._validate_sec_url`.

Ref: .claude/focus-fix/diagnosis.md §9 ticket S-1.
"""

from __future__ import annotations

import pytest

from app.services.pdf_downloader import _validate_sec_url


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
