from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import logging
import tempfile
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.services.service_models import DownloadedPdfRecord

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def pdf_tempdir(prefix: str = "pdf_extract_") -> Iterator[Path]:
    """Yield a per-extraction working directory; auto-cleaned on exit.

    Replaces the persistent PDF cache that previously sat at
    ``settings.pdf_cache_dir``. Each download + parse + DB-write cycle owns
    one of these for its lifetime; when the ``with`` block exits, the
    directory and any PDFs inside it disappear.

    Honors ``settings.pdf_cache_dir`` as the *parent* directory when set —
    purely a local-debug knob so a developer can route the temp into a known
    location and inspect mid-flight. In production the setting is unset and
    we fall back to the system temp.
    """
    parent: Path | None = None
    if settings.pdf_cache_dir:
        parent = Path(settings.pdf_cache_dir)
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as tmp:
        yield Path(tmp)

# Allowlist of hosts this service may fetch from. All SEC-owned. Extend only
# after security review — DB-sourced URLs (broker_dealer.filings_index_url)
# flow through this validator, so a wider allowlist directly widens the SSRF
# attack surface. See .claude/focus-fix/diagnosis.md §9 ticket S-1.
_SEC_ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})


class _StreamingPdfTooLargeError(Exception):
    """Internal: the streamed PDF crossed the configured size ceiling.

    Raised by ``PdfDownloaderService._stream_to_path`` mid-stream so the
    caller can return a missing-PDF result rather than retrying. Lives at
    module scope so the streaming method and its callers share one type.
    """

    def __init__(self, byte_size: int) -> None:
        super().__init__(f"streamed PDF exceeded ceiling at {byte_size} bytes")
        self.byte_size = byte_size


# X-17A-5 multi-document filing packages — many large broker-dealers
# (Morgan Stanley, Goldman, Citi, etc.) file 3–5 PDFs under one accession:
# Statement of Financial Condition, Compliance Report, Exemption Report,
# Independent Auditor's Report, and sometimes a Cover Letter. The
# resolver scoring uses these substring markers (case-insensitive) to
# prefer the Statement of Financial Condition (which carries net capital
# + clearing narrative) and avoid the Compliance/Exemption sub-documents
# (which Gemini correctly identifies as not containing financial data).
# Why no \b boundaries: SEC filers concatenate firm codes with type stems
# without separators — e.g., "MSSOFC.pdf", "GSSOCOMP.pdf" — so a plain
# substring scan is the right primitive here.
_FINANCIAL_STATEMENT_MARKERS = (
    "sofc",                              # Morgan Stanley / Goldman convention
    "statementoffinancialcondition",
    "statement-of-financial-condition",
    "statementoffin",                    # truncated variants
    "financialcondition",
    "financial-condition",
    "stmt",                              # generic "Statement"
    "balancesheet",
    "annualaudit",
    "annual-audit",
)

_NON_FINANCIAL_MARKERS = (
    "comp",         # compliance report (MSSOCOMP, GSCOMP, etc.)
    "compliance",
    "exemp",        # truncated stem used in filenames like MSSOEXEMP, GSEXEMP
    "exempt",       # exemption report (full spelling)
    "exemption",
    "cover",        # cover letter / wrapper
    "letter",
    "wrap",
)


# Form ADV multi-document packages: Part 1A is the structured XML
# registration form, Part 2A is the customer-facing narrative brochure
# (PDF). When Doxie summarises an ADV filing, the brochure is the
# document the user actually wants — it has the firm description, fee
# schedule, AUM detail, disciplinary history, etc. The Part 1 / ADV-W
# documents are either XML or compliance-only forms with little
# narrative content. Same substring-scan approach as the X-17A-5
# markers (filers concatenate stems without separators).
_ADV_BROCHURE_MARKERS = (
    "brochure",
    "part2a",
    "part-2a",
    "p2a",
    "2a-brochure",
    "adv2a",
    "adv-2a",
)

_ADV_NON_BROCHURE_MARKERS = (
    "part1",
    "part-1",
    "p1a",
    "p1b",
    "adv-w",
    "advw",
)


def score_pdf_candidate(
    name: str,
    primary_document: str,
    *,
    form_type: str | None = None,
) -> tuple[int, ...]:
    """Sort key for picking the right PDF inside an EDGAR filing package.

    Lower tuples sort first. The shape of the tuple depends on the form
    type so each filing family can apply the heuristic that makes sense
    for its multi-document layout.

    Profiles (selected by ``form_type``):

    - ``X-17A-5`` (BD audited financials): prefer Statement of Financial
      Condition, demote Compliance / Exemption / Cover sub-documents.
      This is the original ranking — byte-for-byte unchanged so the
      financial-extraction cron keeps picking the same document.

    - ``ADV`` / ``ADV-W`` / ``ADV-NR``: prefer the Part 2A brochure PDF,
      demote Part 1 sub-documents and ADV-W withdrawal forms.

    - Anything else (``None``, ``13F-HR``, ``SC 13D``, etc.): generic
      ranking — exact-match on ``primary_document`` first, then shortest
      filename. No semantic filename heuristic since we don't have one
      for those forms today.
    """
    lower = name.lower()
    form_upper = (form_type or "").upper()

    if form_upper.startswith("X-17"):
        has_non_fin = any(marker in lower for marker in _NON_FINANCIAL_MARKERS)
        has_fin = any(marker in lower for marker in _FINANCIAL_STATEMENT_MARKERS)
        return (
            0 if (has_fin and not has_non_fin) else 1,
            1 if has_non_fin else 0,
            0 if ("x-17" in lower or "x17" in lower) else 1,
            0 if "audit" in lower else 1,
            0 if name == primary_document else 1,
            len(name),
        )

    if form_upper.startswith("ADV"):
        has_brochure = any(marker in lower for marker in _ADV_BROCHURE_MARKERS)
        has_non_brochure = any(marker in lower for marker in _ADV_NON_BROCHURE_MARKERS)
        return (
            0 if (has_brochure and not has_non_brochure) else 1,
            1 if has_non_brochure else 0,
            # Loose ADV marker — any filename containing "adv" wins over
            # generic names. Most filers prefix the brochure with the
            # firm CRD + "adv" (e.g. "111111_adv_part_2a_2026.pdf").
            0 if "adv" in lower else 1,
            0 if name == primary_document else 1,
            len(name),
        )

    # Generic fallback: trust EDGAR's primary_document pointer when it
    # exists, otherwise prefer the shortest filename (heuristic that
    # tends to surface the headline doc over signed-pdfs / exhibits).
    return (
        0 if name == primary_document else 1,
        len(name),
    )


def _score_pdf_candidate(name: str, primary_document: str) -> tuple[int, ...]:
    """Back-compat alias for the X-17A-5 ranking.

    The financial-extraction cron and existing test suite call this name
    expecting the original X-17A-5 ordering, so keep it as a thin
    delegate. New code should call ``score_pdf_candidate`` directly with
    an explicit ``form_type``.
    """
    return score_pdf_candidate(name, primary_document, form_type="X-17A-5")


def _validate_sec_url(url: str) -> None:
    """Reject non-SEC, non-HTTPS, or private-IP targets before any network call.

    Raises ValueError with a non-sensitive message. The hostname and scheme are
    safe to log because they originate from the DB (broker_dealer.filings_index_url)
    or from settings, not from end-user input.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS is allowed; got scheme={parsed.scheme!r}.")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}.")
    if host not in _SEC_ALLOWED_HOSTS:
        raise ValueError(f"Host {host!r} is not in the SEC allowlist.")
    # Defense-in-depth: if the host is ever an IP literal (via misconfigured
    # allowlist or DNS rebind), reject private / loopback / link-local /
    # reserved / multicast ranges. GCP metadata (169.254.169.254) is caught
    # by is_link_local below.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname is not an IP literal — already passed the allowlist, OK.
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError(f"IP literal {host!r} targets a private or reserved range.")


def _sec_retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a SEC ``429 Too Many Requests``.

    SEC EDGAR throttles by source IP at ~10 req/s, shared across the whole
    Cloud Run project. When that budget is momentarily exhausted, SEC/Akamai
    answers with a 429 and (usually) a ``Retry-After`` header. Honor it so a
    request-path call rides out the throttle instead of giving up after the
    thin default backoff and surfacing a raw error to the user. Mirrors the
    proven handling in ``services/edgar.py``.

    Falls back to capped exponential backoff when the header is absent or
    malformed. Capped at 60s so a hostile / bogus header cannot wedge a
    request-path call for minutes.
    """
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return min(float(raw), 60.0)
        except ValueError:
            pass
    return float(min(2**attempt, 30))


# In-process cache for the per-firm submissions document (the
# ``data.sec.gov/submissions/CIK{...}.json`` payload). Because SEC throttles
# the shared Cloud Run egress IP, the focus-report endpoint cannot re-fetch
# SEC on every click — rapid clicks plus concurrent extraction jobs blow the
# 10 req/s budget and the user gets a 429. Caching the parsed submissions doc
# for a few minutes absorbs repeat clicks and same-firm concurrency while
# staying well under SEC's daily filing cadence. Mirrors the _FILINGS_CACHE
# pattern in services/edgar.py. Keyed by the submissions URL; values are
# (monotonic_stamped_at, payload) tuples. Per-process (per Cloud Run worker),
# size-bounded so a long-lived instance cannot grow one entry per firm forever.
_SUBMISSIONS_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_SUBMISSIONS_CACHE_TTL_SECONDS = 10 * 60
_SUBMISSIONS_CACHE_MAX_ENTRIES = 1024


async def _fetch_edgar_index_json(url: str) -> dict[str, object]:
    """Module-level twin of ``PdfDownloaderService._get_json_with_retries``.

    Doxie's chat tools need to resolve EDGAR filing-index URLs without
    instantiating the full ``PdfDownloaderService`` (and re-implementing
    the SSRF allowlist + identity-encoding + retry loop in a sibling
    module). This is the shared primitive.

    Same retry / SSRF semantics as the instance method — both call into
    each other-shaped helpers; we keep two surfaces because the
    PdfDownloaderService method predates this extraction and the
    financial-extraction pipeline still depends on the instance shape.
    """
    _validate_sec_url(url)
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "application/json",
        # Defence in depth — see ``_get_json_with_retries`` comment.
        "Accept-Encoding": "identity",
    }
    last_error: Exception | None = None
    for attempt in range(1, settings.sec_request_max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=settings.sec_request_timeout_seconds,
                headers=headers,
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
            if response.status_code == 429:
                # SEC throttle: honor Retry-After and retry rather than
                # collapsing the 429 into the generic backoff below (which
                # ignores the header and caps at 8s). See _sec_retry_after_seconds.
                if attempt == settings.sec_request_max_retries:
                    response.raise_for_status()
                await asyncio.sleep(_sec_retry_after_seconds(response, attempt))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("SEC endpoint returned an unexpected JSON payload.")
            return payload
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt == settings.sec_request_max_retries:
                raise
            await asyncio.sleep(min(2**attempt, 8))
    raise RuntimeError("Unable to retrieve SEC JSON payload.") from last_error


async def resolve_filing_pdf_url(
    *,
    cik: str,
    accession_number: str,
    primary_document: str | None = None,
    form_type: str | None = None,
) -> str | None:
    """Walk an EDGAR filing's ``index.json`` and pick the best PDF inside.

    Returns the fully-qualified PDF URL (one of the documents in the
    filing package) or ``None`` if the package contains no PDFs at all
    (most Form 4 / 13F-HR filings — those are XML-primary).

    The ``form_type`` argument biases the candidate scoring:
    X-17A-5 → Statement of Financial Condition, ADV → Part 2A brochure,
    everything else → generic ranking. See ``score_pdf_candidate`` for
    the per-profile heuristics.

    ``primary_document`` is optional: when present we fall back to it
    if ``index.json`` fetch fails AND the primary doc itself is a PDF
    (legacy behaviour from the X-17A-5 path); otherwise we return None
    on fetch failure.
    """
    accession_slug = accession_number.replace("-", "")
    try:
        cik_slug = str(int(cik))
    except (TypeError, ValueError):
        return None
    filing_directory_url = f"{settings.sec_archives_base_url}/{cik_slug}/{accession_slug}/"
    index_url = f"{filing_directory_url}index.json"

    try:
        index_payload = await _fetch_edgar_index_json(index_url)
    except Exception:
        if primary_document and primary_document.lower().endswith(".pdf"):
            return f"{filing_directory_url}{primary_document}"
        return None

    directory = index_payload.get("directory", {}) if isinstance(index_payload, dict) else {}
    items = directory.get("item", []) if isinstance(directory, dict) else []
    pdf_candidates: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.lower().endswith(".pdf"):
            pdf_candidates.append(name)

    if not pdf_candidates and primary_document and primary_document.lower().endswith(".pdf"):
        pdf_candidates.append(primary_document)
    if not pdf_candidates:
        return None

    primary_for_score = primary_document or ""
    selected_name = sorted(
        pdf_candidates,
        key=lambda n: score_pdf_candidate(n, primary_for_score, form_type=form_type),
    )[0]
    return f"{filing_directory_url}{selected_name}"


class PdfDownloaderService:
    """Thin SEC PDF fetcher.

    The service no longer owns disk state. Each call accepts a ``dest_dir``
    chosen by the caller — typically a ``tempfile.TemporaryDirectory()``
    yielded by ``pdf_tempdir`` — and writes the PDF there. When the caller's
    ``with`` block exits, the file is gone.
    """

    async def download_latest_x17a5_pdf(
        self, broker_dealer: BrokerDealer, dest_dir: Path
    ) -> DownloadedPdfRecord | None:
        return await self._download_live_pdf(broker_dealer, dest_dir)

    async def download_recent_x17a5_pdfs(
        self, broker_dealer: BrokerDealer, dest_dir: Path, count: int = 2
    ) -> list[DownloadedPdfRecord]:
        """Download the N most recent X-17A-5 PDFs for multi-year financial data."""
        if not broker_dealer.filings_index_url:
            return []

        submissions_payload = await self._get_submissions_json_cached(broker_dealer.filings_index_url)
        filings = list(self._iter_submission_filings(submissions_payload, broker_dealer.filings_index_url or ""))
        filings_section = submissions_payload.get("filings")
        additional_files = filings_section.get("files", []) if isinstance(filings_section, dict) else []

        for item in additional_files:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.endswith(".json"):
                continue
            history_url = urljoin(broker_dealer.filings_index_url or "", name)
            try:
                history_payload = await self._get_json_with_retries(history_url)
                filings.extend(self._iter_submission_filings(history_payload, history_url))
            except Exception:
                continue

        filings.sort(key=lambda item: str(item["filing_date"]), reverse=True)
        # Dedupe by filing_date: some firms (Morgan Stanley, e.g.) file two
        # X-17A-5 accessions on the same day for the same fiscal year — one
        # carries the audited financials, the other a paired compliance
        # filing. Without this step, count=3 burns two of its three slots on
        # the same fiscal year and we never reach a 3rd year of data, which
        # in turn keeps three_year_cagr NULL on the broker-dealer rollup.
        # The first accession at any given filing_date wins (sort above is
        # by filing_date alone, so callers cannot rely on a deterministic
        # tie-break between same-day filings; whichever appears first in
        # EDGAR's submissions JSON is what we keep). If a same-day pair
        # really did represent two distinct fiscal years, this would drop
        # one — extremely rare and acceptable given how much more often the
        # opposite pattern shows up.
        seen_dates: set[str] = set()
        unique_filings: list[dict[str, object]] = []
        for filing in filings:
            filing_date = str(filing["filing_date"])
            if filing_date in seen_dates:
                continue
            seen_dates.add(filing_date)
            unique_filings.append(filing)
        top_filings = unique_filings[:count]

        results: list[DownloadedPdfRecord] = []
        for filing in top_filings:
            try:
                record = await self._download_filing_pdf(broker_dealer, filing, dest_dir)
                if record:
                    results.append(record)
            except Exception:
                continue
        return results

    async def _download_filing_pdf(
        self,
        broker_dealer: BrokerDealer,
        filing: dict[str, object],
        dest_dir: Path,
    ) -> DownloadedPdfRecord | None:
        """Download a single filing's PDF into ``dest_dir``."""
        filing_date = date.fromisoformat(str(filing["filing_date"]))
        accession_number = str(filing["accession_number"])
        accession_slug = accession_number.replace("-", "")
        pdf_path = dest_dir / f"{broker_dealer.cik}-{accession_slug}.pdf"

        pdf_url = await self._resolve_pdf_url(
            cik=broker_dealer.cik,
            accession_number=accession_number,
            primary_document=str(filing["primary_document"]),
        )
        if pdf_url is None:
            return None

        max_size_mb = settings.gemini_inline_pdf_max_size_mb if settings.llm_provider == "gemini" else settings.openai_max_pdf_size_mb
        max_pdf_size_bytes = max_size_mb * 1024 * 1024

        if settings.llm_use_files_api:
            # Streaming path (ADR-0001 phase 2). The PDF lands on disk
            # chunk-by-chunk via httpx.stream + aiter_bytes; bytes never
            # aggregate in memory during the SEC fetch. The downstream LLM call
            # consumes ``local_document_path`` and uploads to the provider Files
            # API, also from disk in chunks. ``bytes_base64`` is left empty —
            # consumers that still rely on it (focus-report endpoint at
            # api/v1/endpoints/broker_dealers, multi-year financial pipeline)
            # are out of scope for this PR and must remain on the flag-off path
            # until a follow-up migrates them.
            try:
                byte_size = await self._stream_to_path(
                    pdf_url, pdf_path, max_pdf_size_bytes
                )
            except _StreamingPdfTooLargeError:
                return None
            if byte_size == 0:
                return None
            return DownloadedPdfRecord(
                bd_id=broker_dealer.id, filing_year=filing_date.year,
                report_date=filing_date,
                source_filing_url=str(filing["filing_index_url"]),
                source_pdf_url=pdf_url, local_document_path=str(pdf_path),
                bytes_base64="",
                accession_number=accession_number,
            )

        # Legacy path (default-off): byte-for-byte identical to today's
        # behavior. Buffers response.content in memory, base64-encodes for
        # downstream inline LLM calls and the focus-report endpoint.
        pdf_bytes = await self._download_bytes_with_retries(pdf_url)
        if len(pdf_bytes) > max_pdf_size_bytes:
            return None

        pdf_path.write_bytes(pdf_bytes)
        return DownloadedPdfRecord(
            bd_id=broker_dealer.id, filing_year=filing_date.year,
            report_date=filing_date, source_filing_url=str(filing["filing_index_url"]),
            source_pdf_url=pdf_url, local_document_path=str(pdf_path),
            bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
            accession_number=accession_number,
        )

    async def _download_live_pdf(
        self, broker_dealer: BrokerDealer, dest_dir: Path
    ) -> DownloadedPdfRecord | None:
        if not broker_dealer.filings_index_url:
            return None

        submissions_payload = await self._get_submissions_json_cached(broker_dealer.filings_index_url)
        filing = await self._find_latest_x17a5_filing(broker_dealer, submissions_payload)
        if filing is None:
            return None

        filing_date = date.fromisoformat(str(filing["filing_date"]))
        accession_number = str(filing["accession_number"])
        accession_slug = accession_number.replace("-", "")
        pdf_path = dest_dir / f"{broker_dealer.cik}-{accession_slug}.pdf"

        pdf_url = await self._resolve_pdf_url(
            cik=broker_dealer.cik,
            accession_number=accession_number,
            primary_document=str(filing["primary_document"]),
        )
        if pdf_url is None:
            return None

        max_size_mb = settings.gemini_inline_pdf_max_size_mb if settings.llm_provider == "gemini" else settings.openai_max_pdf_size_mb
        max_pdf_size_bytes = max_size_mb * 1024 * 1024

        if settings.llm_use_files_api:
            # Streaming path (ADR-0001 phase 2). See docstring on
            # ``_download_filing_pdf`` for the rationale and the consumer
            # caveats. Mirrors that path here for the latest-filing entry
            # point used by the clearing pipeline.
            try:
                byte_size = await self._stream_to_path(
                    pdf_url, pdf_path, max_pdf_size_bytes
                )
            except _StreamingPdfTooLargeError as exc:
                raise RuntimeError(
                    f"Downloaded PDF exceeds the configured {max_size_mb}MB inline ingestion limit for the selected provider."
                ) from exc
            if byte_size == 0:
                return None
            logger.debug(
                "PDF streamed for BD %d: %s (%dKB)",
                broker_dealer.id, pdf_path.name, byte_size // 1024,
            )
            return DownloadedPdfRecord(
                bd_id=broker_dealer.id,
                filing_year=filing_date.year,
                report_date=filing_date,
                source_filing_url=str(filing["filing_index_url"]),
                source_pdf_url=pdf_url,
                local_document_path=str(pdf_path),
                bytes_base64="",
                accession_number=accession_number,
            )

        # Legacy path (default-off): byte-for-byte identical to today's
        # behavior.
        pdf_bytes = await self._download_bytes_with_retries(pdf_url)
        if len(pdf_bytes) > max_pdf_size_bytes:
            raise RuntimeError(
                f"Downloaded PDF exceeds the configured {max_size_mb}MB inline ingestion limit for the selected provider."
            )

        pdf_path.write_bytes(pdf_bytes)
        logger.debug(
            "PDF downloaded for BD %d: %s (%dKB)",
            broker_dealer.id, pdf_path.name, len(pdf_bytes) // 1024,
        )

        return DownloadedPdfRecord(
            bd_id=broker_dealer.id,
            filing_year=filing_date.year,
            report_date=filing_date,
            source_filing_url=str(filing["filing_index_url"]),
            source_pdf_url=pdf_url,
            local_document_path=str(pdf_path),
            bytes_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
            accession_number=accession_number,
        )

    async def _find_latest_x17a5_filing(
        self,
        broker_dealer: BrokerDealer,
        submissions_payload: dict[str, object],
    ) -> dict[str, object] | None:
        filings = list(self._iter_submission_filings(submissions_payload, broker_dealer.filings_index_url or ""))
        filings_section = submissions_payload.get("filings")
        additional_files = filings_section.get("files", []) if isinstance(filings_section, dict) else []

        for item in additional_files:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.endswith(".json"):
                continue
            history_url = urljoin(broker_dealer.filings_index_url or "", name)
            history_payload = await self._get_json_with_retries(history_url)
            filings.extend(self._iter_submission_filings(history_payload, history_url))

        filings.sort(key=lambda item: str(item["filing_date"]), reverse=True)
        return filings[0] if filings else None

    def _iter_submission_filings(self, payload: dict[str, object], submission_url: str) -> list[dict[str, object]]:
        filings_section = payload.get("filings")
        if not isinstance(filings_section, dict):
            return []

        recent = filings_section.get("recent")
        if not isinstance(recent, dict):
            return []

        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])

        results: list[dict[str, object]] = []
        for form, accession_number, primary_document, filing_date in zip(
            forms, accession_numbers, primary_documents, filing_dates, strict=False
        ):
            if not isinstance(form, str) or "X-17A-5" not in form.upper():
                continue
            if not isinstance(accession_number, str) or not isinstance(primary_document, str) or not isinstance(filing_date, str):
                continue
            results.append(
                {
                    "form": form,
                    "accession_number": accession_number,
                    "primary_document": primary_document,
                    "filing_date": filing_date,
                    "filing_index_url": submission_url,
                }
            )

        return results

    async def _resolve_pdf_url(self, *, cik: str, accession_number: str, primary_document: str) -> str | None:
        """X-17A-5 PDF resolver.

        Delegates to the module-level ``resolve_filing_pdf_url`` so the
        chat tools (which can't easily instantiate this service) share
        the same primitive. The ``form_type="X-17A-5"`` argument keeps
        the byte-for-byte ranking the financial-extraction pipeline
        depends on.
        """
        return await resolve_filing_pdf_url(
            cik=cik,
            accession_number=accession_number,
            primary_document=primary_document,
            form_type="X-17A-5",
        )

    async def _get_json_with_retries(self, url: str) -> dict[str, object]:
        # URL validation runs BEFORE the retry loop, so a rejected URL does not
        # consume a retry slot — it raises ValueError directly to the caller.
        # The retry loop's ValueError handler below exists solely for
        # response.json() decode failures on otherwise-successful HTTP fetches.
        _validate_sec_url(url)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/json",
            # Defence in depth: SEC's Akamai POPs from GCP egress send bogus
            # gzip headers; force identity to avoid decoder errors on JSON too.
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None

        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.sec_request_timeout_seconds,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url)
                if response.status_code == 429:
                    # SEC throttle: honor Retry-After and retry rather than
                    # collapsing the 429 into the generic backoff below (which
                    # ignores the header and caps at 8s). See _sec_retry_after_seconds.
                    if attempt == settings.sec_request_max_retries:
                        response.raise_for_status()
                    await asyncio.sleep(_sec_retry_after_seconds(response, attempt))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("SEC endpoint returned an unexpected JSON payload.")
                return payload
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

        raise RuntimeError("Unable to retrieve SEC JSON payload.") from last_error

    async def _get_submissions_json_cached(self, url: str) -> dict[str, object]:
        """Fetch a per-firm submissions JSON doc with a short in-process cache.

        Thin wrapper over ``_get_json_with_retries`` for the *top-level*
        ``data.sec.gov/submissions/CIK{...}.json`` document — the single call
        that backs the focus-report button and the financial pipelines. The
        cache is what stops a rage-clicked focus-report link (or two pipelines
        touching the same firm) from each re-hitting SEC and tripping the
        shared-IP 429. The paginated history sub-files and the per-accession
        ``index.json`` are intentionally NOT cached here — they still go
        through ``_get_json_with_retries`` directly. See ``_SUBMISSIONS_CACHE``.
        """
        now = time.monotonic()
        cached = _SUBMISSIONS_CACHE.get(url)
        if cached is not None and (now - cached[0]) < _SUBMISSIONS_CACHE_TTL_SECONDS:
            return cached[1]

        payload = await self._get_json_with_retries(url)

        # Evict the oldest entry when the cache is full and this is a new key.
        # O(n) over a tiny bounded dict, and only when saturated.
        if (
            len(_SUBMISSIONS_CACHE) >= _SUBMISSIONS_CACHE_MAX_ENTRIES
            and url not in _SUBMISSIONS_CACHE
        ):
            oldest_url = min(_SUBMISSIONS_CACHE, key=lambda key: _SUBMISSIONS_CACHE[key][0])
            _SUBMISSIONS_CACHE.pop(oldest_url, None)
        _SUBMISSIONS_CACHE[url] = (now, payload)
        return payload

    async def _download_bytes_with_retries(self, url: str) -> bytes:
        _validate_sec_url(url)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            # SEC EDGAR's Akamai POPs serving GCP egress IPs reply with bogus
            # Content-Encoding: gzip on PDF responses (PDFs are already
            # application-layer compressed; the transport-layer wrapper is wrong).
            # response.content auto-decompresses and surfaces "Data-loss while
            # decompressing corrupted data" per chunk. Use streaming + aiter_raw
            # to read bytes verbatim. Same root cause as #276.
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None

        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.sec_request_timeout_seconds,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    async with client.stream("GET", url) as response:
                        if response.status_code == 429:
                            # SEC throttle: honor Retry-After before reading the
                            # body, instead of the generic 8s backoff below.
                            # See _sec_retry_after_seconds.
                            if attempt == settings.sec_request_max_retries:
                                response.raise_for_status()
                            await asyncio.sleep(_sec_retry_after_seconds(response, attempt))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                            raise RuntimeError("SEC filing did not resolve to a PDF document.")
                        # aiter_raw, not aiter_bytes — bypass auto-decompression.
                        chunks: list[bytes] = []
                        async for chunk in response.aiter_raw():
                            if chunk:
                                chunks.append(chunk)
                        return b"".join(chunks)
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

        raise RuntimeError("Unable to download SEC PDF after retries.") from last_error

    async def _stream_to_path(
        self, url: str, target_path: Path, max_size_bytes: int
    ) -> int:
        """Stream a SEC PDF to ``target_path`` chunk-by-chunk.

        Used under ``settings.llm_use_files_api == True`` (ADR-0001 phase 2).
        Memory ceiling per call is the chunk size (64 KB), regardless of the
        filing's total size — the response body never aggregates in process
        memory the way ``_download_bytes_with_retries`` does.

        Returns the total byte count written, or 0 if the response was empty.
        Raises ``_StreamingPdfTooLargeError`` if the streamed size exceeds
        ``max_size_bytes`` (the partial file is removed before raising). All
        other errors mirror ``_download_bytes_with_retries``: HTTP and
        transport failures retry with exponential backoff.
        """
        _validate_sec_url(url)
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            # See _download_bytes_with_retries above for the rationale. SEC's
            # Akamai POPs lie about compression; aiter_raw bypasses httpx's
            # auto-decoder and writes the body bytes verbatim.
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None

        for attempt in range(1, settings.sec_request_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.sec_request_timeout_seconds,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    async with client.stream("GET", url) as response:
                        if response.status_code == 429:
                            # SEC throttle: honor Retry-After before reading the
                            # body, instead of the generic 8s backoff below.
                            # See _sec_retry_after_seconds.
                            if attempt == settings.sec_request_max_retries:
                                response.raise_for_status()
                            await asyncio.sleep(_sec_retry_after_seconds(response, attempt))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                            raise RuntimeError("SEC filing did not resolve to a PDF document.")

                        byte_size = 0
                        with target_path.open("wb") as fh:
                            async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                                if not chunk:
                                    continue
                                byte_size += len(chunk)
                                if byte_size > max_size_bytes:
                                    fh.close()
                                    target_path.unlink(missing_ok=True)
                                    raise _StreamingPdfTooLargeError(byte_size)
                                fh.write(chunk)
                        return byte_size
            except _StreamingPdfTooLargeError:
                # Size guard exceeded: do not retry — re-running the same
                # request will produce the same oversized payload. Surface
                # to the caller so it can return a missing-PDF result.
                raise
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                # Drop any partial file from a failed attempt so the next
                # try starts clean and we never serve a truncated PDF to
                # a downstream consumer.
                target_path.unlink(missing_ok=True)
                if attempt == settings.sec_request_max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

        raise RuntimeError("Unable to stream SEC PDF after retries.") from last_error


