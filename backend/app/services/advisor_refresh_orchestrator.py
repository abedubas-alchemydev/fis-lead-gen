"""Per-advisor refresh-all orchestrator (IA analog of
``refresh_all_orchestrator.py``).

Backs ``POST /investment-advisors/{id}/refresh-all`` -- fires the subset
of per-advisor sub-pipelines whose target fields are still NULL on the
``InvestmentAdvisor`` row. The endpoint creates a parent ``PipelineRun``
and a FastAPI BackgroundTask invokes :func:`run_advisor_refresh` to
drive the children in parallel via :func:`asyncio.gather`.

Sub-pipelines in this initial scope:

- ``refresh_owners_officers`` -- runs when ``executive_officers`` is NULL
  on the IA row. Fetches the FINRA BrokerCheck PDF for the advisor's CRD
  and reuses ``brokercheck_pdf.fetch_form_bd_detail`` / ``_parse_form_bd_pdf``
  to extract the Direct Owners and Executive Officers section. SEC's
  Form ADV uses the same section header as Form BD per regulator
  alignment, so the parser anchors match for dual-registered firms and
  IA-only firms whose BrokerCheck PDF carries the section.
- ``resolve_advisor_website`` -- runs when ``website`` is NULL. Reuses
  ``services.website_resolver.resolve_website`` directly. Writes
  ``website`` + ``website_source`` to the IA row.

Each sub-pipeline writes its own child ``PipelineRun`` row with
``parent_run_id`` pointing at the orchestrator's parent row. Failure
modes (no PDF on file, parser returned nothing, website resolver
exhausted its fallbacks) are absorbed into the child's terminal
status string and never abort the parent run.

Mirrors the request/response contract of ``refresh_all_orchestrator``
exactly so the FE detail client at
``frontend/components/advisor-list/advisor-detail-client.tsx`` can
reuse the same polling + 409-conflict + 429-cooldown handling as the
BD detail client at
``frontend/components/master-list/broker-dealer-detail-client.tsx``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal
from urllib.parse import urlparse

import httpx

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.advisor_contact import AdvisorContact
from app.models.advisor_filing import AdvisorFiling
from app.models.investment_advisor import InvestmentAdvisor
from app.models.pipeline_run import PipelineRun
from app.services.apollo import ApolloClient
from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    FinraPdfNotFound,
)
from app.services.contact_discovery.orchestrator import discover_advisor_contact
from app.services.edgar import EdgarService, build_edgar_filing_url
from app.services.finra_pdf_service import fetch_brokercheck_pdf
from app.services.gemini_responses import (
    GeminiAdvisorProfileExtraction,
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.serpapi import SerpAPIClient
from app.services.serper import SerperClient
from app.services.website_resolver import is_blocklisted_host, resolve_website


# IA-only firms (Vanguard, BlackRock IA arms, etc.) have no BrokerCheck
# firm-PDF -- the FINRA endpoint returns 403/404 for them. SEC's IAPD
# system serves their Form ADV PDF instead. URL pattern verified
# against CRD 105958 (Vanguard) -- returns a 7.9 MB PDF with the same
# "Direct Owners and Executive Officers" section header the existing
# parser anchors on. Sections like "Firm Operations" / "Clearing
# Arrangements" don't exist in Form ADV; the parser falls through to
# None for those, which is fine because we only consume
# ``executive_officers`` from the result.
IAPD_PDF_URL_TEMPLATE = "https://reports.adviserinfo.sec.gov/reports/ADV/{crd}/PDF/{crd}.pdf"
IAPD_REQUEST_TIMEOUT_SECONDS = 30.0


async def _fetch_iapd_form_adv_pdf(crd: str) -> bytes:
    """Download the IAPD Form ADV PDF for ``crd``. Raises
    :class:`FinraPdfFetchError` on any non-2xx or transport error, and
    :class:`FinraPdfNotFound` when SEC returns 404 (no ADV on file).
    """
    url = IAPD_PDF_URL_TEMPLATE.format(crd=crd)
    headers = {
        "User-Agent": settings.sec_user_agent,
        "Accept": "application/pdf",
    }
    try:
        async with httpx.AsyncClient(
            timeout=IAPD_REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise FinraPdfFetchError(f"iapd network: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code == 404:
        raise FinraPdfNotFound(f"no IAPD PDF for CRD {crd}")
    if response.status_code != 200:
        raise FinraPdfFetchError(f"iapd http {response.status_code}")
    pdf_bytes = response.content
    if not pdf_bytes.startswith(b"%PDF"):
        raise FinraPdfFetchError("iapd response body is not a PDF")
    return pdf_bytes


RefreshScope = Literal["all"]

logger = logging.getLogger(__name__)


# Parent / child pipeline_name values. Distinct namespace from BD so the
# 409 "already in flight" check and audit trail stay clean.
REFRESH_ADVISOR_ALL_PIPELINE_NAME = "investment_advisor_refresh_all"
SUB_REFRESH_OWNERS_OFFICERS = "investment_advisor_refresh_owners_officers"
SUB_RESOLVE_ADVISOR_WEBSITE = "investment_advisor_resolve_website"
SUB_REFRESH_FILINGS = "investment_advisor_refresh_filings"
SUB_ENRICH_CONTACTS = "investment_advisor_enrich_contacts"

# Short human labels used in the parent's notes.summary toast string.
_SUB_LABEL: dict[str, str] = {
    SUB_REFRESH_OWNERS_OFFICERS: "owners",
    SUB_RESOLVE_ADVISOR_WEBSITE: "website",
    SUB_REFRESH_FILINGS: "filings",
    SUB_ENRICH_CONTACTS: "contacts",
}


# How many officers we hand to the discovery chain per advisor. The People
# panel rarely shows more than a handful of executives, and each entity walks
# the full Snov/Hunter/Apollo/PDL chain, so we cap the fan-out to keep one
# refresh from hammering every provider for a firm with a long Schedule A.
_MAX_OFFICERS_TO_ENRICH = 12


def _split_officer_name(raw: str) -> tuple[str, str] | None:
    """Split a Gemini-extracted officer name into ``(first_name, last_name)``.

    The ``executive_officers`` blob stores names in the Form ADV Schedule A
    "LAST, FIRST, MIDDLE" convention (e.g. ``"PEROLD, ANDRE, FRANCOIS"``). When
    a comma is present the part before the first comma is the surname and the
    next whitespace token is the given name (middle names / suffixes are
    dropped — the discovery providers match on first+last). With no comma we
    fall back to plain whitespace splitting: first token is the given name and
    the last token the surname. Result is title-cased. Returns ``None`` when we
    can't recover both halves (e.g. a single bare token, or blank input)."""
    name = (raw or "").strip()
    if not name:
        return None
    if "," in name:
        last_part, _, rest = name.partition(",")
        last = last_part.strip()
        # ``rest`` may carry the middle name behind a second comma
        # ("ANDRE, FRANCOIS"); take the first comma-delimited field, then its
        # leading whitespace token, so a trailing comma never sticks to the
        # given name.
        first_field = rest.split(",")[0]
        first_tokens = first_field.split()
        first = first_tokens[0].strip() if first_tokens else ""
    else:
        tokens = name.split()
        if len(tokens) < 2:
            return None
        first = tokens[0]
        last = tokens[-1]
    if not first or not last:
        return None
    return first.title(), last.title()


def _domain_from_website(website: str | None) -> str | None:
    """Extract a bare hostname from an advisor ``website`` for the discovery
    chain's domain anchor. Strips scheme and a leading ``www.``; returns
    ``None`` when there's no website or no parseable host (the chain still
    tries org/name providers without a domain)."""
    if not website:
        return None
    candidate = website.strip()
    if not candidate:
        return None
    # urlparse only populates ``netloc`` when a scheme is present; bare
    # "example.com" lands in ``path``. Prepend a scheme when none is given.
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    host = (urlparse(candidate).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


# The filings panel renders the 25 newest rows; cap stored history a little
# above that so we never persist a firm's full ~1000-filing EDGAR history.
_MAX_FILINGS_TO_STORE = 50


# Map an EDGAR form type to a filing-alert priority band. ADV amendments are
# the most material to an advisor's profile; 13F holdings reports are routine.
def _filing_priority(form: str) -> str:
    normalized = form.strip().upper()
    if normalized.startswith("ADV-W"):
        return "medium"
    if normalized.startswith("ADV"):
        return "medium"
    if normalized.startswith("13F"):
        return "low"
    return "low"

# Don't re-run the (paid) Gemini ADV extraction more than once per advisor in
# this window. Some fields (registration/formation dates) may simply not be
# present in the ADV PDF, so without a cooldown the gate would re-fire the
# extraction on every visit forever. ``last_enrich_attempt_at`` is stamped on
# each attempt; the gate treats a recent stamp as "already tried".
ADV_REEXTRACT_COOLDOWN = timedelta(days=7)

_ADV_PROFILE_PROMPT = """\
You are reading an SEC investment-adviser disclosure PDF (Form ADV from IAPD, \
or a FINRA BrokerCheck firm report). Extract the following about THIS firm:

1. executive_officers — each executive officer / management person: name and \
   title. From the "Direct Owners and Executive Officers" section / Schedule A.
2. direct_owners — each direct owner: name, title/status, and ownership band \
   (e.g. "25% but less than 50%") when shown. Schedule A.
3. indirect_owners — each indirect owner with the same fields. Schedule B.
4. client_types — the firm's client categories (Form ADV Item 5.D), e.g. \
   "Individuals", "High net worth individuals", "Investment companies", \
   "Pension and profit sharing plans", "Pooled investment vehicles".
5. registration_date — the firm's SEC/IARD registration date (YYYY-MM-DD) if stated.
6. formation_date — the date the firm was formed/organized (YYYY-MM-DD) if stated.
7. firm_operations_text — a concise (1-3 sentence) plain-English summary of what \
   the firm does, drawn from its described advisory business.

IMPORTANT:
- A person can be both an owner and an officer; list them under each section \
  that applies.
- Use null / empty list for anything not present in the document. Do NOT guess.
- The same "Direct Owners and Executive Officers" header appears on both Form \
  ADV and Form BD, so it is present whether this is an IA-only or dual-registered firm.
- confidence_score reflects overall extraction certainty (0.0-1.0); rationale \
  briefly notes where the data was found.
"""


@dataclass(frozen=True)
class GateDecision:
    """Which sub-pipelines the orchestrator should fire and which to skip."""

    to_run: tuple[str, ...]
    to_skip: tuple[str, ...]


def decide_pipelines(advisor: InvestmentAdvisor) -> GateDecision:
    """Inspect an ``InvestmentAdvisor`` row and decide which sub-pipelines
    have an open gate (their target column is NULL/empty) and which are
    already filled. Same per-firm gate predicate model as
    ``refresh_all_orchestrator.decide_pipelines``."""
    to_run: list[str] = []
    to_skip: list[str] = []

    # The owners/officers sub-pipeline now does a full Gemini ADV extraction
    # that fills the People panel, client types, and the firm-operations
    # summary in one call. Open the gate when any of those content fields is
    # empty — but suppress re-runs within ADV_REEXTRACT_COOLDOWN (tracked via
    # last_enrich_attempt_at) so a field the ADV simply doesn't carry can't
    # make the paid extraction fire on every single visit. Dates are NOT in
    # the predicate for the same reason (often absent from the ADV).
    profile_incomplete = (
        not advisor.executive_officers
        or not advisor.direct_owners
        or not advisor.client_types
        or not advisor.firm_operations_text
    )
    last_attempt = advisor.last_enrich_attempt_at
    if last_attempt is not None and last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    recently_attempted = (
        last_attempt is not None
        and last_attempt >= datetime.now(timezone.utc) - ADV_REEXTRACT_COOLDOWN
    )
    needs_owners = profile_incomplete and not recently_attempted
    if needs_owners:
        to_run.append(SUB_REFRESH_OWNERS_OFFICERS)
    else:
        to_skip.append(SUB_REFRESH_OWNERS_OFFICERS)

    # Open the website gate when there's no website OR the stored one is a
    # social/aggregator host. The IAPD bulk import populates ``website`` from
    # the firm's ADV "website" field, which firms frequently fill with a
    # social handle (Vanguard → x.com/vanguard_instl). is_blocklisted_host
    # is the same denylist the resolver uses, so re-running the resolver on a
    # blocklisted value lets it find the real corporate domain.
    needs_website = not advisor.website or is_blocklisted_host(advisor.website)
    if needs_website:
        to_run.append(SUB_RESOLVE_ADVISOR_WEBSITE)
    else:
        to_skip.append(SUB_RESOLVE_ADVISOR_WEBSITE)

    # Filings + contacts both write to child tables (advisor_filings /
    # advisor_contacts) that aren't visible from the advisor row, so we can't
    # gate on "is the panel already populated". Instead we gate on the same
    # last_enrich_attempt_at + ADV_REEXTRACT_COOLDOWN window the owners gate
    # uses: a recent attempt means we already filled (or tried to fill) these
    # panels, so don't re-run on every visit. Filings additionally needs a CIK
    # (the EDGAR submissions feed is keyed on it). Contacts has no hard
    # precondition — it falls back to executive_officers when Apollo is absent.
    needs_filings = advisor.cik is not None and not recently_attempted
    if needs_filings:
        to_run.append(SUB_REFRESH_FILINGS)
    else:
        to_skip.append(SUB_REFRESH_FILINGS)

    needs_contacts = not recently_attempted
    if needs_contacts:
        to_run.append(SUB_ENRICH_CONTACTS)
    else:
        to_skip.append(SUB_ENRICH_CONTACTS)

    return GateDecision(to_run=tuple(to_run), to_skip=tuple(to_skip))


def required_provider_keys(pipelines: Iterable[str]) -> list[str]:
    """Return the list of provider-key names the orchestrator needs but
    aren't configured. The endpoint surfaces this as 503 so the FE can
    show a sensible error instead of letting the BG task fail mid-run.

    ``resolve_advisor_website`` uses the same provider cascade as the BD
    website resolver (Apollo + Hunter/SerpAPI fallback). The owners/officers
    pipeline only hits FINRA's public BrokerCheck endpoint, which needs no
    key beyond the same SEC-compliant User-Agent the rest of the app already
    sets via ``settings.sec_user_agent``.
    """
    missing: list[str] = []
    pipelines = list(pipelines)
    if SUB_RESOLVE_ADVISOR_WEBSITE in pipelines:
        # SerpAPI is now the primary website source (Google entity resolution
        # is far more precise than Apollo's fuzzy org-name match for
        # common-word brands). Apollo is only a last-resort fallback, so gate
        # on the primary key. SerpAPI absent + Apollo present still resolves
        # via the fallback, but we surface SerpAPI as the expected dependency.
        if not getattr(settings, "serpapi_api_key", None) and not settings.apollo_api_key:
            missing.append("SERPAPI_API_KEY")
    if SUB_REFRESH_OWNERS_OFFICERS in pipelines:
        # The ADV profile extraction now runs through Gemini, so the key is
        # required for this sub-pipeline (the PDF fetch itself is keyless).
        if not settings.gemini_api_key:
            missing.append("GEMINI_API_KEY")
    return missing


# ---------------------------------------------------------------------------
# Child PipelineRun bookkeeping
# ---------------------------------------------------------------------------

async def _create_child_run(
    parent_run_id: int,
    advisor_id: int,
    pipeline_name: str,
    trigger_source: str,
) -> int:
    """Persist a child PipelineRun and return its id. Each sub-pipeline
    writes its own child so the audit trail mirrors BD's structure."""
    async with SessionLocal() as db:
        run = PipelineRun(
            pipeline_name=pipeline_name,
            trigger_source=trigger_source,
            status="running",
            total_items=1,
            processed_items=0,
            success_count=0,
            failure_count=0,
            notes=json.dumps({"advisor_id": advisor_id, "parent_run_id": parent_run_id}),
            parent_run_id=parent_run_id,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def _finalize_child(
    child_id: int,
    *,
    status: str,
    success: int,
    failure: int,
    summary: str,
) -> None:
    async with SessionLocal() as db:
        run = await db.get(PipelineRun, child_id)
        if run is None:
            return
        run.status = status
        run.processed_items = success + failure
        run.success_count = success
        run.failure_count = failure
        run.completed_at = datetime.now(timezone.utc)
        existing_notes = {}
        if run.notes:
            try:
                existing_notes = json.loads(run.notes)
            except json.JSONDecodeError:
                existing_notes = {}
        existing_notes["summary"] = summary
        run.notes = json.dumps(existing_notes)
        await db.commit()


# ---------------------------------------------------------------------------
# ADV extraction write helpers
# ---------------------------------------------------------------------------

_ADV_MIN_CONFIDENCE = 0.4
_FIRM_OPERATIONS_MAX_CHARS = 4000


def _people_to_json(people: list) -> list[dict[str, str]]:
    """Convert Gemini person objects to the JSONB shape the IA columns store."""
    rows: list[dict[str, str]] = []
    for person in people:
        name = (person.name or "").strip()
        if not name:
            continue
        row: dict[str, str] = {"name": name}
        if person.title:
            row["title"] = person.title.strip()
        if person.ownership:
            row["ownership"] = person.ownership.strip()
        rows.append(row)
    return rows


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except (ValueError, AttributeError):
        return None


async def _apply_adv_extraction(
    advisor_id: int, extraction: GeminiAdvisorProfileExtraction
) -> list[str] | None:
    """Write the extracted profile fields onto the IA row, filling only
    currently-empty fields (never clobbering existing good data). Always
    stamps ``last_enrich_attempt_at`` so the gate's re-extract cooldown holds
    even when the ADV didn't carry a given field. Returns the list of written
    field labels, or ``None`` if the advisor row disappeared.
    """
    written: list[str] = []
    async with SessionLocal() as db:
        advisor = await db.get(InvestmentAdvisor, advisor_id)
        if advisor is None:
            return None

        if extraction.confidence_score >= _ADV_MIN_CONFIDENCE:
            officers = _people_to_json(extraction.executive_officers)
            if officers and not advisor.executive_officers:
                advisor.executive_officers = officers
                written.append("officers")

            direct = _people_to_json(extraction.direct_owners)
            if direct and not advisor.direct_owners:
                advisor.direct_owners = direct
                written.append("owners")

            indirect = _people_to_json(extraction.indirect_owners)
            if indirect and not advisor.indirect_owners:
                advisor.indirect_owners = indirect
                written.append("indirect owners")

            if extraction.client_types and not advisor.client_types:
                advisor.client_types = [c.strip() for c in extraction.client_types if c and c.strip()]
                if advisor.client_types:
                    written.append("client types")

            if extraction.firm_operations_text and not advisor.firm_operations_text:
                advisor.firm_operations_text = extraction.firm_operations_text.strip()[:_FIRM_OPERATIONS_MAX_CHARS]
                written.append("operations")

            reg = _parse_iso_date(extraction.registration_date)
            if reg is not None and advisor.registration_date is None:
                advisor.registration_date = reg
                written.append("registration date")

            formed = _parse_iso_date(extraction.formation_date)
            if formed is not None and advisor.formation_date is None:
                advisor.formation_date = formed
                written.append("formation date")

        advisor.last_enrich_attempt_at = datetime.now(timezone.utc)
        await db.commit()
    return written


# ---------------------------------------------------------------------------
# Sub-pipeline runners
# ---------------------------------------------------------------------------

async def _run_refresh_owners_officers(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Fetch the firm PDF (BrokerCheck, then IAPD Form ADV fallback) for this
    advisor's CRD and run a Gemini structured extraction over it, writing
    officers, direct/indirect owners, client types, registration/formation
    dates, and a firm-operations summary onto the IA row. Only currently-empty
    fields are filled; ``last_enrich_attempt_at`` is stamped on every attempt
    so the gate's re-extract cooldown holds."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_REFRESH_OWNERS_OFFICERS, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            crd = advisor.crd_number

        if not crd:
            summary = "No CRD on record; cannot fetch BrokerCheck or IAPD PDF."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        # Two-tier fetch: BrokerCheck firm PDF (works for BDs + dual-
        # registered firms), then IAPD Form ADV PDF (works for IA-only
        # firms like Vanguard / BlackRock advisor arms). Both PDFs use
        # the same "Direct Owners and Executive Officers" section header
        # so the existing parser handles either.
        source = "brokercheck"
        pdf_bytes: bytes | None = None
        last_error: str | None = None
        try:
            pdf_bytes = await fetch_brokercheck_pdf(crd)
        except FinraPdfNotFound:
            last_error = "BrokerCheck 404"
        except FinraPdfFetchError as exc:
            # 403 from BrokerCheck for IA-only firms — fall through to IAPD.
            last_error = f"BrokerCheck: {str(exc)[:120]}"

        if pdf_bytes is None:
            source = "iapd"
            try:
                pdf_bytes = await _fetch_iapd_form_adv_pdf(crd)
            except FinraPdfNotFound:
                summary = "No PDF on file (BrokerCheck 404, IAPD 404)."
                await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
                return "completed_with_errors", summary
            except FinraPdfFetchError as exc:
                summary = f"PDF fetch failed (BrokerCheck: {last_error}; IAPD: {str(exc)[:120]})"
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

        # Gemini structured extraction over the whole PDF. ``_dispatch_pdf_extract``
        # routes the large IAPD ADV PDFs (Vanguard's is ~8 MB) through the
        # Files API automatically. One call fills officers, owners, client
        # types, dates, and the firm-operations summary — far more robust than
        # the old regex parser, which only ever recovered executive_officers.
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        try:
            extraction = await GeminiResponsesClient().extract_advisor_profile(
                pdf_bytes_base64=pdf_b64, prompt=_ADV_PROFILE_PROMPT
            )
        except GeminiConfigurationError as exc:
            summary = f"Gemini not configured: {str(exc)[:160]}"
            await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
            return "failed", summary
        except GeminiExtractionError as exc:
            summary = f"Gemini extraction failed ({source}): {str(exc)[:160]}"
            await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
            return "failed", summary

        written = await _apply_adv_extraction(advisor_id, extraction)
        if written is None:
            summary = "Advisor row disappeared between extract and write."
            await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
            return "failed", summary

        if not written:
            summary = (
                f"PDF ({source}) extracted but yielded no new profile fields "
                f"(confidence {extraction.confidence_score:.2f})."
            )
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        summary = f"Wrote {', '.join(written)} from {source} (confidence {extraction.confidence_score:.2f})."[:180]
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/owners-officers failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_resolve_advisor_website(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Run the existing Apollo/Hunter waterfall to find a website for this
    advisor. Mirrors the BD sub-pipeline's behaviour: the resolver writes
    nothing on its own; we apply the returned URL to the IA row only on a
    confident hit."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_RESOLVE_ADVISOR_WEBSITE, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            firm_name = advisor.name
            crd = advisor.crd_number

        if not firm_name:
            summary = "Advisor has no firm_name; cannot search providers."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        apollo = ApolloClient(settings.apollo_api_key) if settings.apollo_api_key else None
        serpapi = (
            SerpAPIClient(settings.serpapi_api_key)
            if getattr(settings, "serpapi_api_key", None)
            else None
        )
        serper = (
            SerperClient(settings.serper_api_key)
            if getattr(settings, "serper_api_key", None)
            else None
        )

        # Resolve via search engines FIRST (SerpAPI / serper). Google's entity
        # resolution plus the validator's high-confidence bypass picks the
        # firm's real domain — e.g. vanguard.com for "VANGUARD GROUP INC".
        # Apollo is demoted to a last-resort fallback here because its fuzzy
        # org-name match returns same-named *different* companies for
        # common-word brands (it returned vanguardstaffing.com for Vanguard).
        website, source, reason = await resolve_website(
            firm_name=firm_name,
            crd=crd,
            apollo=None,
            serpapi=serpapi,
            serper=serper,
        )
        if not website and apollo is not None:
            website, source, reason = await resolve_website(
                firm_name=firm_name,
                crd=crd,
                apollo=apollo,
                serpapi=None,
                serper=None,
            )

        if not website:
            # No valid candidate. If the row currently holds a blocklisted
            # social/aggregator URL (the reason we re-ran), clear it: a wrong
            # social link is worse than an empty field. Leave a genuine miss
            # (website already NULL) untouched. Don't clear on a provider
            # outage — that would discard data on a transient error.
            cleared = False
            if reason != "all_providers_errored" and not (reason or "").startswith("all_providers_errored"):
                async with SessionLocal() as db:
                    advisor = await db.get(InvestmentAdvisor, advisor_id)
                    if advisor is not None and advisor.website and is_blocklisted_host(advisor.website):
                        advisor.website = None
                        advisor.website_source = None
                        await db.commit()
                        cleared = True
            summary = (
                "Cleared blocklisted social/aggregator website; no valid replacement found."
                if cleared
                else f"Provider waterfall returned no website ({reason or 'unknown'})."
            )
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between resolve and write."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            advisor.website = website
            advisor.website_source = source
            await db.commit()

        summary = f"Wrote website from {source}."
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/website failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_refresh_filings(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Pull this advisor's full EDGAR filing history and upsert one
    ``AdvisorFiling`` row per filing (deduped on accession number) so the
    detail page's "Recent regulatory filings" panel fills. Re-runs are
    insert-or-skip on ``dedupe_key`` so we never duplicate. Needs a CIK; with
    none, finishes ``completed_with_errors``."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_REFRESH_FILINGS, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            cik = advisor.cik

        if not cik:
            summary = "No CIK on record; cannot fetch EDGAR filing history."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        filings = await EdgarService().list_all_filings_for_cik(cik)
        if not filings:
            summary = "EDGAR returned no filings for this CIK."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        cik_padded = (cik or "").strip().lstrip("0").zfill(10)
        inserted = 0
        registration_candidate: date | None = None
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between fetch and write."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

            existing_keys = set(
                (
                    await db.execute(
                        select(AdvisorFiling.dedupe_key).where(
                            AdvisorFiling.advisor_id == advisor_id
                        )
                    )
                ).scalars()
            )

            # Earliest ADV filing date is a cheap registration_date proxy. Scan
            # ALL filings for it (the earliest is the oldest, at the tail), but
            # only STORE the most recent _MAX_FILINGS_TO_STORE — the panel reads
            # 25 newest, so persisting a firm's full ~1000-filing history would
            # just bloat the table.
            for filing in filings:
                form = str(filing.get("form") or "").strip()
                filing_date = filing.get("filing_date")
                if (
                    form.upper().startswith("ADV")
                    and isinstance(filing_date, date)
                    and (registration_candidate is None or filing_date < registration_candidate)
                ):
                    registration_candidate = filing_date

            for filing in filings[:_MAX_FILINGS_TO_STORE]:
                form = str(filing.get("form") or "").strip()
                if not form:
                    continue
                filing_date = filing.get("filing_date")
                if not isinstance(filing_date, date):
                    # Skip undated filings — filed_at is NOT NULL and we won't
                    # fabricate a date.
                    continue

                accession = filing.get("accession_number")
                accession_str = str(accession).strip() if accession else ""
                dedupe_key = accession_str or f"{advisor_id}:{form}:{filing_date.isoformat()}"
                if dedupe_key in existing_keys:
                    continue
                existing_keys.add(dedupe_key)

                primary_doc = filing.get("primary_document")
                primary_doc_str = str(primary_doc).strip() if primary_doc else None
                source_url = build_edgar_filing_url(
                    cik_padded, accession_str or None, primary_doc_str
                )
                desc = filing.get("primary_doc_description")
                desc_str = str(desc).strip() if desc else ""
                summary_text = f"{form} filed {filing_date.isoformat()}"
                if desc_str:
                    summary_text = f"{summary_text} — {desc_str}"

                db.add(
                    AdvisorFiling(
                        advisor_id=advisor_id,
                        dedupe_key=dedupe_key[:255],
                        form_type=form[:64],
                        priority=_filing_priority(form),
                        filed_at=datetime(
                            filing_date.year,
                            filing_date.month,
                            filing_date.day,
                            tzinfo=timezone.utc,
                        ),
                        summary=summary_text,
                        source_filing_url=source_url,
                        is_read=False,
                    )
                )
                inserted += 1

            if registration_candidate is not None and advisor.registration_date is None:
                advisor.registration_date = registration_candidate

            await db.commit()

        summary = f"Added {inserted} new filing(s) ({len(filings)} on EDGAR)."
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/filings failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


async def _run_enrich_contacts(
    parent_run_id: int, advisor_id: int, trigger_source: str
) -> tuple[str, str]:
    """Fill the People panel with real contact CHANNELS by running each
    extracted officer through the shared multi-provider discovery chain
    (Snov/Hunter/Apollo/PDL) via ``discover_advisor_contact``. Officers the
    chain can't resolve still get a names-only row (source="adv") so the panel
    shows everyone. Falls back to a single org-level lookup when no officers
    were extracted.

    Idempotent: if any ``advisor_contacts`` row already exists, finishes
    without touching data. Provider failures inside the chain are swallowed by
    the chain itself, so a miss simply yields no channels rather than aborting
    the run."""
    child_id = await _create_child_run(
        parent_run_id, advisor_id, SUB_ENRICH_CONTACTS, trigger_source
    )
    try:
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and run."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary
            firm_name = advisor.name
            domain = _domain_from_website(advisor.website)
            executive_officers = advisor.executive_officers

            existing = (
                await db.execute(
                    select(AdvisorContact.id)
                    .where(AdvisorContact.advisor_id == advisor_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                summary = "Advisor already has contacts; nothing to enrich."
                await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
                return "completed", summary

        # Build the per-officer work list up front (parse + cap), so the write
        # session below only does DB work. Each entry carries the original
        # display name so we can fall back to a names-only row on a miss.
        officers: list[dict[str, str]] = (
            executive_officers if isinstance(executive_officers, list) else []
        )
        work: list[dict[str, str]] = []
        for officer in officers:
            if not isinstance(officer, dict):
                continue
            display_name = str(officer.get("name") or "").strip()
            split = _split_officer_name(display_name)
            if split is None:
                continue
            first_name, last_name = split
            title = str(officer.get("title") or "").strip() or "Executive Officer"
            work.append(
                {
                    "display_name": display_name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "title": title,
                }
            )
            if len(work) >= _MAX_OFFICERS_TO_ENRICH:
                break

        now = datetime.now(timezone.utc)
        with_channels = 0
        names_only = 0
        async with SessionLocal() as db:
            advisor = await db.get(InvestmentAdvisor, advisor_id)
            if advisor is None:
                summary = "Advisor row disappeared between queue and write."
                await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
                return "failed", summary

            # Re-check inside the write session to avoid a duplicate-insert race
            # with a concurrent run on the same advisor.
            existing = (
                await db.execute(
                    select(AdvisorContact.id)
                    .where(AdvisorContact.advisor_id == advisor_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                summary = "Advisor already has contacts; nothing to enrich."
                await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
                return "completed", summary

            if work:
                # Track names that received a discovered row so the names-only
                # backfill below doesn't duplicate them. ``discover_advisor_contact``
                # only adds a row on a chain HIT (provider found the person).
                resolved_names: set[str] = set()
                for entry in work:
                    entity = {
                        "type": "person",
                        "first_name": entry["first_name"],
                        "last_name": entry["last_name"],
                        "org_name": firm_name,
                        "domain": domain,
                        "title": entry["title"],
                    }
                    row = await discover_advisor_contact(
                        entity, advisor_id=advisor_id, session=db
                    )
                    if row is not None:
                        resolved_names.add(entry["display_name"])
                        with_channels += 1

                # Backfill: any officer the chain couldn't resolve still gets a
                # names-only row so the People panel shows the full roster. Skip
                # names already inserted by discovery (and de-dupe within this
                # loop) so we never write the same person twice.
                seen = set(resolved_names)
                for entry in work:
                    display_name = entry["display_name"]
                    if display_name in seen:
                        continue
                    seen.add(display_name)
                    db.add(
                        AdvisorContact(
                            advisor_id=advisor_id,
                            name=display_name[:255],
                            title=entry["title"][:255],
                            email=None,
                            phone=None,
                            linkedin_url=None,
                            source="adv",
                            enriched_at=now,
                        )
                    )
                    names_only += 1
            elif firm_name:
                # No officers extracted — try a single org-level discovery so
                # the panel can still surface a public/org contact channel.
                entity = {
                    "type": "organization",
                    "org_name": firm_name,
                    "domain": domain,
                }
                row = await discover_advisor_contact(
                    entity, advisor_id=advisor_id, session=db
                )
                if row is not None:
                    with_channels += 1

            inserted = with_channels + names_only
            if inserted:
                await db.commit()

        if not inserted:
            summary = "No contacts found (no resolvable officers and no org-level match)."
            await _finalize_child(child_id, status="completed_with_errors", success=0, failure=1, summary=summary)
            return "completed_with_errors", summary

        summary = f"Enriched {inserted} contact(s) ({with_channels} with channels) for advisor."[:180]
        await _finalize_child(child_id, status="completed", success=1, failure=0, summary=summary)
        return "completed", summary
    except Exception as exc:
        logger.exception("advisor-refresh/contacts failed for advisor %s", advisor_id)
        summary = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _finalize_child(child_id, status="failed", success=0, failure=1, summary=summary)
        return "failed", summary


_RUNNERS = {
    SUB_REFRESH_OWNERS_OFFICERS: _run_refresh_owners_officers,
    SUB_RESOLVE_ADVISOR_WEBSITE: _run_resolve_advisor_website,
    SUB_REFRESH_FILINGS: _run_refresh_filings,
    SUB_ENRICH_CONTACTS: _run_enrich_contacts,
}


# ---------------------------------------------------------------------------
# Parent orchestrator
# ---------------------------------------------------------------------------

async def run_advisor_refresh(
    parent_run_id: int,
    advisor_id: int,
    *,
    trigger_source: str,
    pipelines_to_run: tuple[str, ...],
    pipelines_to_skip: tuple[str, ...],
) -> None:
    """Drive the parent run through ``running -> completed`` (or
    ``completed_with_errors`` / ``failed``) by firing each child
    pipeline in parallel via ``asyncio.gather`` and aggregating the
    terminal states into the parent's notes."""

    if not pipelines_to_run:
        async with SessionLocal() as db:
            run = await db.get(PipelineRun, parent_run_id)
            if run is not None:
                run.status = "skipped"
                run.completed_at = datetime.now(timezone.utc)
                run.notes = json.dumps(
                    {"summary": "Already complete.", "ran": [], "skipped": list(pipelines_to_skip)}
                )
                await db.commit()
        return

    async with SessionLocal() as db:
        parent = await db.get(PipelineRun, parent_run_id)
        if parent is None:
            logger.error("advisor-refresh: parent run %d disappeared before start", parent_run_id)
            return
        parent.status = "running"
        parent.total_items = len(pipelines_to_run)
        parent.notes = json.dumps(
            {
                "advisor_id": advisor_id,
                "stage": "running",
                "ran": list(pipelines_to_run),
                "skipped": list(pipelines_to_skip),
            }
        )
        await db.commit()

    coros = [_RUNNERS[name](parent_run_id, advisor_id, trigger_source) for name in pipelines_to_run]
    results = await asyncio.gather(*coros, return_exceptions=True)

    children_summary: dict[str, dict[str, object]] = {}
    success = 0
    failure = 0
    label_ran: list[str] = []
    label_failed: list[str] = []

    for name, result in zip(pipelines_to_run, results):
        if isinstance(result, BaseException):
            child_status = "failed"
            child_summary = f"{type(result).__name__}: {str(result)[:200]}"
        else:
            child_status, child_summary = result
        children_summary[name] = {"status": child_status, "summary": child_summary}
        label = _SUB_LABEL.get(name, name)
        if child_status in ("completed", "completed_with_errors"):
            success += 1
            label_ran.append(label)
        else:
            failure += 1
            label_failed.append(label)

    if failure == 0:
        parent_status = "completed"
    elif success == 0:
        parent_status = "failed"
    else:
        parent_status = "completed_with_errors"

    summary_parts: list[str] = []
    if label_ran:
        summary_parts.append(f"Refreshed: {', '.join(label_ran)}")
    if label_failed:
        summary_parts.append(f"Failed: {', '.join(label_failed)}")
    if pipelines_to_skip:
        skipped_labels = [_SUB_LABEL.get(name, name) for name in pipelines_to_skip]
        summary_parts.append(f"Skipped: {', '.join(skipped_labels)}")
    summary = ". ".join(summary_parts) + "." if summary_parts else "No-op."
    summary = summary[:180]

    async with SessionLocal() as db:
        parent = await db.get(PipelineRun, parent_run_id)
        if parent is None:
            return
        parent.status = parent_status
        parent.processed_items = success + failure
        parent.success_count = success
        parent.failure_count = failure
        parent.completed_at = datetime.now(timezone.utc)
        parent.notes = json.dumps(
            {
                "summary": summary,
                "ran": list(pipelines_to_run),
                "skipped": list(pipelines_to_skip),
                "children": children_summary,
            }
        )
        # Stamp the advisor's last-attempt time so the cooldown-gated
        # sub-pipelines (owners, filings, contacts) all settle uniformly. The
        # owners pipeline also stamps this on its own write path, but stamping
        # here guarantees the cooldown holds even when owners was skipped (e.g.
        # already complete) and only filings/contacts ran — without it, those
        # two would re-fire (and re-hit EDGAR) on every visit.
        advisor = await db.get(InvestmentAdvisor, advisor_id)
        if advisor is not None:
            advisor.last_enrich_attempt_at = datetime.now(timezone.utc)
        await db.commit()


__all__ = [
    "GateDecision",
    "REFRESH_ADVISOR_ALL_PIPELINE_NAME",
    "SUB_REFRESH_OWNERS_OFFICERS",
    "SUB_RESOLVE_ADVISOR_WEBSITE",
    "SUB_REFRESH_FILINGS",
    "SUB_ENRICH_CONTACTS",
    "decide_pipelines",
    "required_provider_keys",
    "run_advisor_refresh",
]
