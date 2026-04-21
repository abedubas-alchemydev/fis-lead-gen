"""
FINRA BrokerCheck Detailed Report parser.

Strategy: split the document into named sections using stable header anchors,
then run targeted extractors on each section. This isolates failure — if FINRA
changes the format of one section, only that extractor breaks.

Validated against:
  - firm_5393 Charles Schwab (modern, active, 315 pages — fully populated)
  - firm_10997 R H Securities  (legacy, terminated — 70% "Information not available")
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from ..schema.models import (
    FirmHistory,
    FirmOperations,
    FirmProfile,
    IntroducingArrangement,
    Officer,
    TypesOfBusiness,
)
from .base import ExtractedPdf, extract_pdf, find_first_match, split_sections

logger = logging.getLogger(__name__)


# Section headers in the order they appear in a BrokerCheck Detailed Report
FINRA_SECTION_HEADERS = [
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

INFO_NOT_AVAILABLE = re.compile(r"information not available", re.IGNORECASE)
PAGE_FOOTER = re.compile(r"©\d{4}\s*FINRA.*?(?:\n|$)", re.IGNORECASE)
HEADER_BOILERPLATE = re.compile(r"www\.finra\.org/brokercheck\s+User Guidance\n?")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_finra_pdf(pdf_bytes: bytes, queried_name: Optional[str] = None) -> FirmProfile:
    extracted = extract_pdf(pdf_bytes)
    text = _normalize(extracted.full_text)
    sections = split_sections(text, FINRA_SECTION_HEADERS)

    profile = FirmProfile(raw_pdf_hash=extracted.sha256)

    _parse_identity(text, profile)
    _parse_officers(sections.get("Direct Owners and Executive Officers", ""), profile)
    _parse_types_of_business(sections.get("Types of Business", ""), profile)
    _parse_operations(sections, profile)
    _parse_history(sections, text, profile)

    # Sanity — if we got zero officers AND the section says "Information not
    # available", that's expected (legacy firms). Flag any unexpected silence.
    if not profile.officers and not INFO_NOT_AVAILABLE.search(
        sections.get("Direct Owners and Executive Officers", "")
    ):
        profile.parse_warnings.append("no_officers_parsed_but_section_looked_populated")

    return profile


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Strip page footers / repeated header boilerplate that fragments sections."""
    text = PAGE_FOOTER.sub("\n", text)
    text = HEADER_BOILERPLATE.sub("", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ---------------------------------------------------------------------------
# Identity (CRD, SEC#, Firm Name)
# ---------------------------------------------------------------------------

def _parse_identity(text: str, profile: FirmProfile) -> None:
    crd = find_first_match([r"CRD#\s*(\d+)"], text)
    sec = find_first_match([r"SEC#\s*([\w\-]+)"], text)
    profile.crd_number = crd
    profile.sec_number = sec

    # Firm name — the header under "BrokerCheck Report" is a reliable anchor
    m = re.search(r"BrokerCheck Report\s*\n\s*([^\n]+)", text)
    if m:
        profile.firm_name = m.group(1).strip()

    # "This firm is currently registered with the SEC..." or legacy opposite
    if re.search(r"This firm is currently registered with the SEC", text, re.IGNORECASE):
        profile.is_registered = True
    elif re.search(
        r"This firm is no longer registered|termination", text, re.IGNORECASE
    ):
        profile.is_registered = False


# ---------------------------------------------------------------------------
# Officers
# ---------------------------------------------------------------------------

# Anchor for each officer block
_OFFICER_SPLIT = re.compile(r"Legal Name & CRD# \(if any\):", re.IGNORECASE)


def _parse_officers(section_text: str, profile: FirmProfile) -> None:
    if not section_text or INFO_NOT_AVAILABLE.search(section_text):
        return

    blocks = _OFFICER_SPLIT.split(section_text)[1:]  # drop preamble before first anchor
    for block in blocks:
        officer = _parse_officer_block(block)
        if officer:
            profile.officers.append(officer)


def _parse_officer_block(block: str) -> Optional[Officer]:
    """Parse a single officer block. The text between this block's anchor and
    the next is small (~8 label/value pairs), so we can regex each field."""
    # Name is on the first line after the anchor
    lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    name = lines[0].rstrip(",")

    # Position can span multiple lines — everything between "Position" label
    # and "Position Start Date" label, stripping the label itself.
    position = _extract_multiline_field(
        block,
        start_label=r"Position(?!\s+Start Date)",
        end_label=r"(?:Position Start Date|Percentage of Ownership|Relationship)",
    )
    start = find_first_match([r"Position Start Date\s+([\d/]+)"], block)
    ownership = find_first_match([r"Percentage of Ownership\s+([^\n]+)"], block)
    direct = find_first_match([r"Does this owner direct the[^\n]*\n?\s*management[^\n]*\n?\s*([YN][eo][sn])"], block)
    pr = find_first_match([r"Is this a public reporting[^\n]*\n?\s*company\?\s*([YN][eo][sn])"], block)

    return Officer(
        name=name,
        position=position,
        position_start=start,
        ownership_code=ownership,
        is_control_affiliate=(direct or "").strip().lower() == "yes" if direct else None,
        pr=(pr or "").strip().lower() == "yes" if pr else None,
        raw=block.strip()[:800],
    )


def _extract_multiline_field(
    block: str, start_label: str, end_label: str
) -> Optional[str]:
    pattern = re.compile(
        rf"{start_label}\s+(.+?)(?=\n\s*{end_label})",
        re.DOTALL,
    )
    m = pattern.search(block)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


# ---------------------------------------------------------------------------
# Types of Business
# ---------------------------------------------------------------------------

# Known next-section headers that terminate the Types of Business list
_TOB_TERMINATORS = {
    "Firm Operations",
    "Other Types of Business",
    "Clearing Arrangements",
    "Introducing Arrangements",
    "Industry Arrangements",
    "Disclosure Events",
}


def _parse_types_of_business(section_text: str, profile: FirmProfile) -> None:
    if not section_text or INFO_NOT_AVAILABLE.search(section_text):
        return

    count_match = re.search(
        r"This firm currently conducts (\d+) types? of businesses?", section_text, re.IGNORECASE
    )
    total = int(count_match.group(1)) if count_match else 0

    # Anchor algorithm: after the count sentence, the second occurrence of
    # "Types of Business" is the subheader that precedes the actual list.
    # Collect every line after that until a terminator header or "Other Types".
    anchor = count_match.end() if count_match else 0
    post = section_text[anchor:]
    subheader = re.search(r"^\s*Types of Business\s*$", post, re.MULTILINE)

    services: list[str] = []
    other_text: Optional[str] = None
    if subheader:
        body = post[subheader.end():]
        collecting = True
        for line in body.splitlines():
            ln = line.strip()
            if not ln:
                continue
            if ln in _TOB_TERMINATORS:
                if ln == "Other Types of Business":
                    # Capture the freeform blob that follows
                    idx = body.find(ln, 0) + len(ln)
                    rest = body[idx:].strip()
                    # Cut at next recognized terminator
                    for term in _TOB_TERMINATORS - {"Other Types of Business"}:
                        pos = rest.find(f"\n{term}")
                        if pos > 0:
                            rest = rest[:pos]
                    other_text = rest.strip() or None
                collecting = False
                break
            if ln.startswith("©") or ln.startswith("www.finra.org"):
                continue
            if collecting:
                services.append(ln)

    profile.types_of_business = TypesOfBusiness(
        total=total or len(services),
        services=services,
        other=other_text,
    )


# ---------------------------------------------------------------------------
# Firm Operations (Clearing + Introducing)
# ---------------------------------------------------------------------------

def _parse_operations(sections: dict, profile: FirmProfile) -> None:
    ops = FirmOperations()

    clearing_section = sections.get("Clearing Arrangements", "")
    introducing_section = sections.get("Introducing Arrangements", "")

    # Clearing statement — first sentence of the section content
    clearing_text = _strip_section_header(clearing_section, "Clearing Arrangements")
    ops.clearing_statement = _first_sentence(clearing_text)
    ops.clearing_raw_text = clearing_text.strip() or None

    # Introducing: if section says "does not refer or introduce" → empty list
    intro_text = _strip_section_header(introducing_section, "Introducing Arrangements")
    if intro_text and not re.search(
        r"does not refer or introduce", intro_text, re.IGNORECASE
    ):
        ops.introducing_arrangements = _parse_introducing_entries(intro_text)

    profile.operations = ops


def _strip_section_header(section_text: str, header: str) -> str:
    if not section_text:
        return ""
    pattern = re.compile(rf"^\s*{re.escape(header)}\s*\n?", re.IGNORECASE)
    return pattern.sub("", section_text, count=1)


def _first_sentence(text: str) -> Optional[str]:
    text = text.strip()
    if not text:
        return None
    m = re.search(r"^(.+?[.!?])(?=\s|$)", text, re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return re.sub(r"\s+", " ", text.splitlines()[0]).strip() or None


def _parse_introducing_entries(text: str) -> list[IntroducingArrangement]:
    """Introducing entries follow the Name/Business Address/Effective Date/Description pattern."""
    entries: list[IntroducingArrangement] = []
    blocks = re.split(r"\n(?=Name:\s)", text)
    for block in blocks:
        name = find_first_match([r"Name:\s*([^\n]+)"], block)
        eff = find_first_match([r"Effective Date:\s*([\d/]+)"], block)
        desc = find_first_match([r"Description:\s*(.+?)(?=\n\s*(?:Name:|$))"], block)
        if not name:
            continue
        entries.append(
            IntroducingArrangement(
                business_name=name,
                effective_date=eff,
                description=(desc or "").strip() or None,
                statement=block.strip()[:800],
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Firm History (Formation + Registration dates)
# ---------------------------------------------------------------------------

_DATE_FORMATS_TRY = ["%m/%d/%Y", "%m/%d/%y"]


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    from datetime import datetime
    for fmt in _DATE_FORMATS_TRY:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_history(sections: dict, full_text: str, profile: FirmProfile) -> None:
    hist = FirmHistory()

    # Formation — appears on the Firm Profile page: "This firm was formed in <state> on <date>."
    formation = find_first_match(
        [r"This firm was formed in [\w\s]+ on (\d{1,2}/\d{1,2}/\d{2,4})"],
        full_text,
    )
    hist.formation_date = _parse_date(formation)

    # FINRA registration — on the Registrations page as "SEC Approved MM/DD/YYYY" or "FINRA Approved MM/DD/YYYY"
    registrations_text = sections.get("Registrations", "") or full_text
    reg_match = re.search(
        r"(?:SEC|FINRA)\s+Approved\s+(\d{1,2}/\d{1,2}/\d{2,4})",
        registrations_text,
    )
    if reg_match:
        hist.registration_date = _parse_date(reg_match.group(1))

    # Termination — legacy firms
    term = find_first_match(
        [r"terminat\w+\s+(?:on\s+)?(\d{1,2}/\d{1,2}/\d{2,4})"],
        full_text,
    )
    hist.termination_date = _parse_date(term)

    profile.history = hist
