"""Inline Form BD extractor — reads the BrokerCheck Detailed Report PDF.

Why this file exists. The legacy FINRA BrokerCheck JSON detail endpoint
(``/firm/{crd}``) now 403s at Cloudflare even with a real browser fingerprint,
and the substitute (``/search/firm/{crd}``) returns a payload that no longer
contains the Form BD fields — ``firm_bd_types_of_business``,
``firm_executive_officers``, ``firm_bd_firm_operations`` — that
``FinraService.enrich_with_detail`` was wired against. Our enrichment pass
silently no-op'd every record after the gateway change.

The deterministic Form BD PDF at ``files.brokercheck.finra.org/firm/firm_{CRD}.pdf``
still ships those fields, just in PDF form. The sibling
``brokercheck_extractor/`` package has a sophisticated parser for it, but
the brokercheck_extractor source isn't copied into the backend Docker image
(build context is ``./backend/``), so importing it would raise at runtime.
``services/finra_pdf_service.py`` already documented this lesson; the same
constraint applies here. So we inline a minimal extractor that pulls the
three fields ``enrich_with_detail`` actually needs, using ``pdfplumber``
(already a backend dep) and hand-tuned regex against the section anchors
the FINRA Detailed Report has used unchanged for years.

Scope is intentionally narrow: ``types_of_business``, ``executive_officers``,
``firm_operations_text`` (the clearing-classifier gate text), and an
opportunistic ``web_address`` (the FINRA Form BD PDF doesn't typically carry
the firm's website — that's a Form ADV thing — but we look for it anyway in
case a firm filed it). Industry Arrangements parsing and clearing
classification are NOT done here; they live downstream against the raw
operations text.

This is Option D from ``reports/finra-pdf-migration-blocker-2026-05-01.md``.
The full ``brokercheck_extractor/`` parser is the eventual destination once
the build context is restructured (Option A). Keeping this file small means
the migration is straightforward when that day comes.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber
import pypdf

from app.services.finra_pdf_service import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_brokercheck_pdf,
)

logger = logging.getLogger(__name__)


# Section headers in the order they appear in a BrokerCheck Detailed Report.
# Order matters: ``_split_sections`` walks them sequentially so a header word
# appearing earlier (e.g. inside a TOC) doesn't capture later content.
_SECTION_HEADERS = [
    "Report Summary",
    "Firm Profile",
    "Direct Owners and Executive Officers",
    "Indirect Owners",
    "Firm History",
    "Firm Operations",
    "Registrations",
    "Types of Business",
    "Clearing Arrangements",
    "Introducing Arrangements",
    "Industry Arrangements",
    "Control Persons/Financing",
    "Organization Affiliates",
    "Disclosure Events",
]

# Boilerplate FINRA repeats on every page — strip before section splitting so
# multi-page sections collapse into a single contiguous block.
_PAGE_FOOTER = re.compile(r"©\d{4}\s*FINRA.*?(?:\n|$)", re.IGNORECASE)
_HEADER_BOILERPLATE = re.compile(r"www\.finra\.org/brokercheck\s+User Guidance\n?")
# Tolerate the "Informationnotavailable" space-collapsed variant that legacy
# / terminated-firm PDFs produce (the cover page is typeset with kerning that
# pdfplumber renders as zero-width gaps).
_INFO_NOT_AVAILABLE = re.compile(r"information\s*not\s*available", re.IGNORECASE)

# Anchor for each officer block inside the "Direct Owners and Executive
# Officers" section.
_OFFICER_SPLIT = re.compile(r"Legal Name & CRD# \(if any\):", re.IGNORECASE)

# Section-header words that terminate the Types of Business list. The list
# anchor is the second occurrence of "Types of Business" inside the section
# body; collection stops at any of these.
_TYPES_TERMINATORS = {
    "Firm Operations",
    "Other Types of Business",
    "Clearing Arrangements",
    "Introducing Arrangements",
    "Industry Arrangements",
    "Disclosure Events",
}

# All Form BD fields we read live in the first ~15 pages of any report.
# Big firms balloon their reports to 1000+ pages with Disclosure Events /
# Organization Affiliates content we don't read; pdfplumber's per-document
# metadata pass over those pages is a 15-30 second penalty per firm. The
# fix is two-stage: pypdf slices the PDF down to the first ``_PAGE_HARD_CAP``
# pages (cheap — copies references not bytes), then pdfplumber does its
# full layout-aware text extraction on the small slice. Layout-aware
# extraction is required for the "Direct Owners and Executive Officers"
# section, which uses a two-column layout that pypdf / pypdfium2 flatten
# in a way that breaks our regex. Verified against the fixture set + a
# live probe of CRDs 7560 (Pershing, 264p), 7691 (BAC NA, 1995p), and
# 13071 (Apex, 64p).
_PAGE_HARD_CAP = 30


@dataclass(frozen=True)
class FormBdDetail:
    """Form BD fields extracted from the BrokerCheck Detailed Report PDF.

    All list/text fields are populated best-effort from the PDF. Empty/None
    values mean the parser couldn't find the field — the caller should leave
    the existing record value alone rather than overwriting with None.

    ``web_address`` is included for completeness but is almost always None:
    the Form BD Detailed Report PDF doesn't carry the firm's web address,
    only IAPD references for regulatory disclosures. We pluck it
    opportunistically on the off chance a firm filed it.
    """

    crd: str
    types_of_business: list[str]
    executive_officers: list[dict[str, str]]
    firm_operations_text: Optional[str]
    web_address: Optional[str]


async def fetch_form_bd_detail(crd: str) -> Optional[FormBdDetail]:
    """Download + parse the Form BD PDF for ``crd``.

    Returns ``None`` when FINRA has no Detailed Report on file (404). Raises
    ``FinraPdfFetchError`` on transient upstream failures and propagates
    parse exceptions — callers log + leave the existing record intact rather
    than null its fields.
    """
    try:
        pdf_bytes = await fetch_brokercheck_pdf(crd)
    except FinraPdfNotFound:
        logger.info("FINRA has no Form BD PDF on file for CRD %s", crd)
        return None
    return _parse_form_bd_pdf(crd, pdf_bytes)


def _parse_form_bd_pdf(crd: str, pdf_bytes: bytes) -> FormBdDetail:
    """Extract Form BD fields from the PDF bytes."""
    full_text = _extract_text(pdf_bytes)
    full_text = _normalize(full_text)
    sections = _split_sections(full_text, _SECTION_HEADERS)

    return FormBdDetail(
        crd=crd,
        types_of_business=_parse_types_of_business(
            sections.get("Types of Business", "")
        ),
        executive_officers=_parse_officers(
            sections.get("Direct Owners and Executive Officers", "")
        ),
        firm_operations_text=_parse_firm_operations(sections),
        web_address=_parse_web_address(full_text),
    )


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _extract_text(pdf_bytes: bytes) -> str:
    """Extract text from the first ``_PAGE_HARD_CAP`` pages.

    Two stages: pypdf slices the source PDF down to the first N pages
    (cheap; copies page references) and pdfplumber does layout-aware text
    extraction on the small slice. Iterating pdfplumber over a 1995-page
    PDF without slicing first is ~15 seconds per firm because pdfplumber
    scans the full xref table at open time; slicing first cuts that to
    ~4-5 seconds even on the largest reports. Layout-aware extraction is
    non-negotiable for the Direct Owners section's two-column block —
    pypdf's own ``extract_text`` flattens the columns and breaks the
    officer regex.
    """
    sliced_bytes = _slice_first_pages(pdf_bytes, _PAGE_HARD_CAP)
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(sliced_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _slice_first_pages(pdf_bytes: bytes, max_pages: int) -> bytes:
    """Return a new PDF containing the first ``max_pages`` pages of the
    source. Used to keep pdfplumber from doing layout work on hundreds of
    pages we don't read.
    """
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) <= max_pages:
        # Already small enough; skip the rewrite roundtrip.
        return pdf_bytes
    writer = pypdf.PdfWriter()
    for index in range(max_pages):
        writer.add_page(reader.pages[index])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _normalize(text: str) -> str:
    """Strip per-page footer + repeated header boilerplate so multi-page
    sections collapse into a single contiguous block."""
    text = _PAGE_FOOTER.sub("\n", text)
    text = _HEADER_BOILERPLATE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_sections(full_text: str, headers: list[str]) -> dict[str, str]:
    """Carve the report into named sections by anchoring on top-level headers.

    Walks ``headers`` in order. Each section is the text from its header to
    the next found header (or EOF). Headers are searched sequentially so a
    header word that appears earlier in the document (e.g. in a table of
    contents) doesn't swallow later content.
    """
    cursor = 0
    positions: list[tuple[str, int]] = []
    for hdr in headers:
        pattern = re.compile(rf"(?m)^\s*{re.escape(hdr)}\b")
        m = pattern.search(full_text, cursor)
        if m:
            positions.append((hdr, m.start()))
            cursor = m.end()

    out: dict[str, str] = {}
    for idx, (hdr, start) in enumerate(positions):
        end = positions[idx + 1][1] if idx + 1 < len(positions) else len(full_text)
        out[hdr] = full_text[start:end].strip()
    return out


# ---------------------------------------------------------------------------
# Types of Business
# ---------------------------------------------------------------------------

def _parse_types_of_business(section_text: str) -> list[str]:
    """Extract the list of business types from the Types of Business section.

    Layout is two anchors: a "This firm currently conducts N types..." line,
    then a second "Types of Business" subheader, then the list — one entry
    per line — until a known terminator header. We require the count
    sentence to anchor extraction; without it (legacy / terminated firms,
    space-collapsed PDFs) we return empty rather than collect prose.
    """
    if not section_text or _INFO_NOT_AVAILABLE.search(section_text):
        return []

    count_match = re.search(
        r"This firm currently conducts \d+ types? of businesses?",
        section_text,
        re.IGNORECASE,
    )
    if not count_match:
        # Either an empty / "Information not available" section that the
        # earlier regex failed to catch (space-collapsed text), or a
        # legacy report layout we don't try to support — better to return
        # nothing than the section's prose preamble.
        return []

    post = section_text[count_match.end():]

    # The subheader is the first standalone "Types of Business" line after
    # the count sentence. The body is everything between that and the next
    # terminator.
    subheader = re.search(r"^\s*Types of Business\s*$", post, re.MULTILINE)
    if not subheader:
        return []

    body = post[subheader.end():]
    services: list[str] = []
    for line in body.splitlines():
        ln = line.strip()
        if not ln:
            continue
        if ln in _TYPES_TERMINATORS:
            break
        if ln.startswith("©") or ln.startswith("www.finra.org"):
            continue
        services.append(ln)
    return services


# ---------------------------------------------------------------------------
# Direct Owners and Executive Officers
# ---------------------------------------------------------------------------

def _parse_officers(section_text: str) -> list[dict[str, str]]:
    """Extract the list of officers/owners from the Direct Owners section.

    The section is split into per-person blocks anchored by the
    "Legal Name & CRD# (if any):" label. Each block carries a small set of
    label/value pairs (name, position, ownership percentage).
    """
    if not section_text or _INFO_NOT_AVAILABLE.search(section_text):
        return []

    # The first split element is the preamble before the first anchor; drop it.
    blocks = _OFFICER_SPLIT.split(section_text)[1:]
    officers: list[dict[str, str]] = []
    for block in blocks:
        officer = _parse_officer_block(block)
        if officer is not None:
            officers.append(officer)
    return officers


def _parse_officer_block(block: str) -> Optional[dict[str, str]]:
    """Parse one officer entry into a {name, title?, ownership_pct?} dict.

    Returns None when the block has no recoverable name (truncated section,
    OCR garble).
    """
    lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    name = lines[0].rstrip(",")
    if not name or name.startswith("(continued)"):
        return None

    position = _multiline_field(
        block,
        start_label=r"Position(?!\s+Start Date)",
        end_label=r"(?:Position Start Date|Percentage of Ownership|Relationship)",
    )
    ownership = _single_line_field(block, r"Percentage of Ownership\s+([^\n]+)")

    out: dict[str, str] = {"name": name}
    if position:
        out["title"] = position
    if ownership:
        out["ownership_pct"] = ownership
    return out


def _multiline_field(block: str, *, start_label: str, end_label: str) -> Optional[str]:
    """Extract a label/value pair where the value can wrap across lines."""
    pattern = re.compile(
        rf"{start_label}\s+(.+?)(?=\n\s*{end_label})",
        re.DOTALL,
    )
    m = pattern.search(block)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip() or None


def _single_line_field(block: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, block)
    if not m:
        return None
    return m.group(1).strip() or None


# ---------------------------------------------------------------------------
# Firm Operations text (the clearing-classifier gate input)
# ---------------------------------------------------------------------------

def _parse_firm_operations(sections: dict[str, str]) -> Optional[str]:
    """Pull the Clearing Arrangements paragraph used by the clearing gates.

    The classifier downstream reads this text for "does hold/maintain funds
    or securities" / "does not hold" / "provides clearing services for other
    broker-dealers" patterns. The Form BD PDF places the paragraph under the
    "Clearing Arrangements" subheader inside Firm Operations; we strip the
    subheader and return the body.
    """
    section = sections.get("Clearing Arrangements", "")
    if not section:
        return None
    body = re.sub(
        r"^\s*Clearing Arrangements\s*\n?", "", section, count=1, flags=re.IGNORECASE
    ).strip()
    # Trim "Introducing Arrangements" subheader if it ended up captured (the
    # section splitter sometimes runs Clearing into the next sibling header
    # if FINRA omits a hard break).
    intro_at = body.find("Introducing Arrangements")
    if intro_at > 0:
        body = body[:intro_at].strip()
    if not body:
        return None
    # Terminated / legacy firms ship "Information not available — see Summary
    # Page" as their entire ops paragraph. Returning that string would let
    # downstream consumers think we have a real ops paragraph; return None
    # so the field stays NULL on the broker_dealers row instead.
    if _INFO_NOT_AVAILABLE.search(body):
        return None
    return body


# ---------------------------------------------------------------------------
# Web Address (best effort — Form BD PDFs almost never carry one)
# ---------------------------------------------------------------------------

def _parse_web_address(full_text: str) -> Optional[str]:
    """Best-effort web-address pluck.

    The Form BD Detailed Report PDF doesn't typically include the firm's
    web address — that field comes from Form ADV (investment-adviser) data.
    But on the chance a firm did file one (and FINRA surfaces it), we look
    for an explicit "Web Address" / "Web Site" / "Website" label and pluck
    the URL on the same line. Excludes FINRA / SEC boilerplate URLs that
    appear in the report's references.
    """
    label = re.search(
        r"(?im)^\s*(?:Web\s*Address|Web\s*Site|Website)\s*[:\-]?\s*([^\n]+)",
        full_text,
    )
    if not label:
        return None
    candidate = label.group(1).strip()
    if not candidate or any(
        s in candidate.lower() for s in ("finra.org", "sec.gov", "adviserinfo")
    ):
        return None
    # Allow https://, http://, or bare domains. Reject anything that doesn't
    # look like a URL/domain to avoid capturing label fragments.
    if not re.match(r"^(?:https?://|[\w\-]+\.)", candidate):
        return None
    return candidate


__all__ = [
    "FinraPdfFetchError",
    "FinraPdfNotFound",
    "FormBdDetail",
    "fetch_form_bd_detail",
]
