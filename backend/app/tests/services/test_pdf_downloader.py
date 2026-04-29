"""Tests for pdf_downloader: SSRF guard (S-1) + error-path coverage (S-4)
+ per-extraction tempdir lifecycle (Sprint 2 task #20).

Ref: .claude/focus-fix/diagnosis.md §9 tickets S-1 and S-4;
plans/be-kill-pdf-cache-tempdir-2026-04-28.md.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.pdf_downloader import (
    PdfDownloaderService,
    _StreamingPdfTooLargeError,
    _validate_sec_url,
    pdf_tempdir,
)


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


# ─────────────────── Sprint 2 task #20: tempdir lifecycle ─────────────


class TestPdfTempdirLifecycle:
    """``pdf_tempdir`` replaces the persistent PDF_CACHE_DIR. Every call
    site owns one via ``with``; on exit the directory and any PDFs inside
    it disappear. These tests pin the contract."""

    def test_yields_existing_directory(self) -> None:
        """The yielded path must exist and be a directory while the
        ``with`` block is open — pdfplumber and pypdfium2 expect a
        real directory to write into."""
        with pdf_tempdir() as tmp_dir:
            assert isinstance(tmp_dir, Path)
            assert tmp_dir.exists()
            assert tmp_dir.is_dir()

    def test_cleans_up_on_exit(self) -> None:
        """The directory and its contents must be gone after ``with``
        exits. This is the core promise — without it the persistent
        cache that grew to ~9 GB on prod would just come back."""
        with pdf_tempdir() as tmp_dir:
            captured = tmp_dir
            # Drop a synthetic PDF inside, simulating a downloaded filing.
            sample = tmp_dir / "1234567-000123456725000001.pdf"
            sample.write_bytes(b"%PDF-1.7 synthetic")
            assert sample.exists()

        # Sanity: tempdir context exited.
        assert not captured.exists()
        assert not sample.exists()

    def test_honors_prefix(self) -> None:
        """The prefix kwarg must land on the directory name so logs and
        ``ls /tmp`` reveal which extraction owns a given tempdir
        mid-flight."""
        with pdf_tempdir(prefix="testprefix_") as tmp_dir:
            assert tmp_dir.name.startswith("testprefix_")

    def test_honors_settings_pdf_cache_dir_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When ``settings.pdf_cache_dir`` is set, tempdirs land inside
        it (local-debug knob). When unset, the system temp is used.
        Both behaviors are tested here so neither is silently broken."""
        # Set the setting to a known parent directory.
        custom_parent = tmp_path / "fis-pdf-debug"
        monkeypatch.setattr(settings, "pdf_cache_dir", str(custom_parent))

        with pdf_tempdir(prefix="under_setting_") as tmp_dir:
            # Tempdir lives directly under the configured parent.
            assert tmp_dir.parent == custom_parent
            assert tmp_dir.exists()
            assert tmp_dir.name.startswith("under_setting_")

        # Cleanup still happens; the parent survives.
        assert custom_parent.exists()
        assert not tmp_dir.exists()

    def test_falls_back_to_system_temp_when_setting_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production runs without the setting — the system temp is the
        intended landing place."""
        monkeypatch.setattr(settings, "pdf_cache_dir", None)

        with pdf_tempdir() as tmp_dir:
            # Just assert it works and the directory is real. The exact
            # location of the system temp is OS-dependent and not worth
            # asserting on.
            assert tmp_dir.exists()


# ─────────────────── Sprint 2 task #20: dest_dir routing ──────────────

_SEC_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/index.json"
)
_SEC_PRIMARY_PDF_URL = (
    "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/primary.pdf"
)


class TestDownloadFilingPdfWritesToDestDir:
    """``_download_filing_pdf`` accepts a caller-supplied ``dest_dir``
    instead of pulling a fixed cache location from config. These tests
    pin that contract; if a future refactor accidentally re-introduces a
    global cache lookup, the assertions on path location fail loudly."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_writes_pdf_into_supplied_dest_dir(
        self, tmp_path: Path
    ) -> None:
        """Mock the SEC index.json + PDF GETs and assert the downloader
        wrote the file at ``dest_dir / "{cik}-{accession_slug}.pdf"``,
        not anywhere else."""
        from app.models.broker_dealer import BrokerDealer

        # SEC index.json — picks ``primary.pdf`` as the filing's main doc.
        respx.get(_SEC_INDEX_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "directory": {
                        "item": [
                            {"name": "primary.pdf"},
                        ]
                    }
                },
            )
        )
        # Synthetic PDF body — the validator only requires the bytes path
        # have ``.pdf`` suffix or content-type ``application/pdf``.
        respx.get(_SEC_PRIMARY_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"%PDF-1.7 synthetic body",
                headers={"content-type": "application/pdf"},
            )
        )

        bd = BrokerDealer()
        bd.id = 1
        bd.cik = "0001234567"

        filing = {
            "form": "X-17A-5",
            "accession_number": "0001234567-25-000001",
            "primary_document": "primary.pdf",
            "filing_date": "2025-03-31",
            "filing_index_url": "https://data.sec.gov/submissions/CIK0001234567.json",
        }

        downloader = PdfDownloaderService()
        record = await downloader._download_filing_pdf(bd, filing, tmp_path)

        assert record is not None
        # Path landed in the caller-supplied directory, NOT in
        # settings.pdf_cache_dir or anywhere else.
        expected = tmp_path / "0001234567-000123456725000001.pdf"
        assert Path(record.local_document_path) == expected
        assert expected.exists()
        assert expected.read_bytes().startswith(b"%PDF")

    @respx.mock
    @pytest.mark.asyncio
    async def test_temp_pdf_is_gone_after_caller_tempdir_exits(self) -> None:
        """End-to-end check of the new contract: the caller wraps the
        download in ``with pdf_tempdir()``; once the block exits, the
        downloaded PDF file is gone. This is the property that makes
        the 9 GB → < 100 MB footprint claim true."""
        from app.models.broker_dealer import BrokerDealer

        respx.get(_SEC_INDEX_URL).mock(
            return_value=httpx.Response(
                200,
                json={"directory": {"item": [{"name": "primary.pdf"}]}},
            )
        )
        respx.get(_SEC_PRIMARY_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"%PDF-1.7 ephemeral",
                headers={"content-type": "application/pdf"},
            )
        )

        bd = BrokerDealer()
        bd.id = 1
        bd.cik = "0001234567"

        filing = {
            "form": "X-17A-5",
            "accession_number": "0001234567-25-000001",
            "primary_document": "primary.pdf",
            "filing_date": "2025-03-31",
            "filing_index_url": "https://data.sec.gov/submissions/CIK0001234567.json",
        }

        downloader = PdfDownloaderService()
        with pdf_tempdir(prefix="lifecycle_") as tmp_dir:
            record = await downloader._download_filing_pdf(bd, filing, tmp_dir)
            assert record is not None
            written_path = Path(record.local_document_path)
            assert written_path.exists()  # file exists during the with block

        # On ``with`` exit the tempdir and its contents must be gone.
        assert not written_path.exists()
        assert not tmp_dir.exists()


# ─────────────────── ADR-0001 phase 2: streaming download ─────────────


class TestStreamToPath:
    """``_stream_to_path`` is the new flag-on download path. Bytes never
    aggregate in process memory — they flow through ``aiter_bytes`` straight
    to the caller-supplied target file. These tests pin the contract."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_chunked_bytes_match_what_was_streamed(
        self, tmp_path: Path, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """The bytes written to disk must equal the concatenation of the
        chunks the upstream produced. Trivially true for a single-chunk
        response, but locks in the contract for future multi-chunk respx
        mocks."""
        full_body = b"%PDF-1.7\n" + (b"abcdefgh" * 4096) + b"\n%%EOF"
        respx.get(_SEC_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=full_body,
                headers={"content-type": "application/pdf"},
            )
        )

        target = tmp_path / "streamed.pdf"
        downloader = PdfDownloaderService()

        byte_size = await downloader._stream_to_path(
            _SEC_PDF_URL, target, max_size_bytes=10 * 1024 * 1024
        )

        assert byte_size == len(full_body)
        assert target.exists()
        assert target.read_bytes() == full_body

    @respx.mock
    @pytest.mark.asyncio
    async def test_target_file_deleted_on_caller_tempdir_exit(
        self, patch_sec_retries: None, no_backoff_sleep: None
    ) -> None:
        """End-to-end with ``pdf_tempdir``: the streamed PDF disappears once
        the caller's ``with`` block exits. This is the property that makes
        the 9 GB → 0 GB persistent-cache claim true under the streaming
        path too."""
        body = b"%PDF-1.7 streamed-then-deleted"
        respx.get(_SEC_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/pdf"},
            )
        )

        downloader = PdfDownloaderService()
        with pdf_tempdir(prefix="stream_lifecycle_") as tmp_dir:
            target = tmp_dir / "streamed.pdf"
            await downloader._stream_to_path(_SEC_PDF_URL, target, max_size_bytes=1024)
            assert target.exists()

        assert not target.exists()
        assert not tmp_dir.exists()

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_propagates_after_retries(
        self,
        tmp_path: Path,
        patch_sec_retries: None,
        no_backoff_sleep: None,
    ) -> None:
        """4xx still surfaces an HTTP error after retries are exhausted —
        the streaming refactor does not change the existing failure
        contract."""
        route = respx.get(_SEC_PDF_URL).mock(return_value=httpx.Response(403))
        downloader = PdfDownloaderService()

        with pytest.raises(httpx.HTTPStatusError):
            await downloader._stream_to_path(
                _SEC_PDF_URL, tmp_path / "streamed.pdf", max_size_bytes=1024 * 1024
            )

        assert route.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_5xx_propagates_after_retries(
        self,
        tmp_path: Path,
        patch_sec_retries: None,
        no_backoff_sleep: None,
    ) -> None:
        """5xx is also retried then surfaced. Mirrors the contract on
        ``_download_bytes_with_retries``."""
        route = respx.get(_SEC_PDF_URL).mock(return_value=httpx.Response(503))
        downloader = PdfDownloaderService()

        with pytest.raises(httpx.HTTPStatusError):
            await downloader._stream_to_path(
                _SEC_PDF_URL, tmp_path / "streamed.pdf", max_size_bytes=1024 * 1024
            )

        assert route.call_count == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_oversize_raises_streaming_too_large_and_drops_partial_file(
        self,
        tmp_path: Path,
        patch_sec_retries: None,
        no_backoff_sleep: None,
    ) -> None:
        """If the streamed payload crosses ``max_size_bytes`` mid-flight,
        the helper raises ``_StreamingPdfTooLargeError`` and removes the
        partial file. Caller treats this as a missing-PDF result rather
        than retrying — re-running the same fetch produces the same
        oversized payload."""
        body = b"X" * (256 * 1024)  # 256 KB
        respx.get(_SEC_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/pdf"},
            )
        )

        target = tmp_path / "streamed.pdf"
        downloader = PdfDownloaderService()

        with pytest.raises(_StreamingPdfTooLargeError):
            await downloader._stream_to_path(
                _SEC_PDF_URL, target, max_size_bytes=64 * 1024  # below body size
            )

        # Partial file must be cleaned up.
        assert not target.exists()

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_pdf_content_raises_runtime(
        self,
        tmp_path: Path,
        patch_sec_retries: None,
        no_backoff_sleep: None,
    ) -> None:
        """Same content-type / .pdf-suffix heuristic as the legacy path."""
        non_pdf_url = (
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000001/weird"
        )
        respx.get(non_pdf_url).mock(
            return_value=httpx.Response(
                200,
                content=b"<html/>",
                headers={"content-type": "text/html"},
            )
        )

        downloader = PdfDownloaderService()
        with pytest.raises(RuntimeError, match="did not resolve to a PDF"):
            await downloader._stream_to_path(
                non_pdf_url, tmp_path / "streamed.pdf", max_size_bytes=1024 * 1024
            )


class TestDownloadFilingPdfFlagBranching:
    """``_download_filing_pdf`` branches on ``settings.llm_use_files_api``.
    These tests pin both branches so a future regression cannot silently
    change behavior on either side of the flag."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_flag_off_populates_bytes_base64(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default-off path: byte-for-byte identical to today's behavior.
        ``bytes_base64`` is populated; ``file_id`` is None."""
        from app.models.broker_dealer import BrokerDealer

        monkeypatch.setattr(settings, "llm_use_files_api", False)

        respx.get(_SEC_INDEX_URL).mock(
            return_value=httpx.Response(
                200,
                json={"directory": {"item": [{"name": "primary.pdf"}]}},
            )
        )
        respx.get(_SEC_PRIMARY_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"%PDF-1.7 legacy-path-body",
                headers={"content-type": "application/pdf"},
            )
        )

        bd = BrokerDealer()
        bd.id = 1
        bd.cik = "0001234567"
        filing = {
            "form": "X-17A-5",
            "accession_number": "0001234567-25-000001",
            "primary_document": "primary.pdf",
            "filing_date": "2025-03-31",
            "filing_index_url": "https://data.sec.gov/submissions/CIK0001234567.json",
        }

        downloader = PdfDownloaderService()
        record = await downloader._download_filing_pdf(bd, filing, tmp_path)

        assert record is not None
        assert record.bytes_base64  # populated under flag-off
        assert record.file_id is None
        assert record.accession_number == "0001234567-25-000001"

    @respx.mock
    @pytest.mark.asyncio
    async def test_flag_on_streams_and_leaves_bytes_base64_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag-on path: PDF is streamed to disk, ``bytes_base64`` stays
        empty, ``accession_number`` is stamped on the record so the LLM
        client can use it as the LRU key. ``file_id`` is None at this
        layer — the LLM client populates it after upload."""
        from app.models.broker_dealer import BrokerDealer

        monkeypatch.setattr(settings, "llm_use_files_api", True)

        respx.get(_SEC_INDEX_URL).mock(
            return_value=httpx.Response(
                200,
                json={"directory": {"item": [{"name": "primary.pdf"}]}},
            )
        )
        respx.get(_SEC_PRIMARY_PDF_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"%PDF-1.7 streamed-flag-on",
                headers={"content-type": "application/pdf"},
            )
        )

        bd = BrokerDealer()
        bd.id = 1
        bd.cik = "0001234567"
        filing = {
            "form": "X-17A-5",
            "accession_number": "0001234567-25-000001",
            "primary_document": "primary.pdf",
            "filing_date": "2025-03-31",
            "filing_index_url": "https://data.sec.gov/submissions/CIK0001234567.json",
        }

        downloader = PdfDownloaderService()
        record = await downloader._download_filing_pdf(bd, filing, tmp_path)

        assert record is not None
        assert record.bytes_base64 == ""  # empty under flag-on
        assert record.file_id is None
        assert record.accession_number == "0001234567-25-000001"
        # PDF was actually written to disk by the streaming path.
        assert Path(record.local_document_path).exists()
        assert Path(record.local_document_path).read_bytes() == b"%PDF-1.7 streamed-flag-on"

