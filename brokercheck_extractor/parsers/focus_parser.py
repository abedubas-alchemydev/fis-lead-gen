"""
SEC Form X-17A-5 (FOCUS Report) parser.

X-17A-5 facing pages are typically scanned forms, so this parser relies
heavily on the OCR path in parsers/base.py. The Statement of Financial
Condition that follows is usually born-digital and extracts cleanly.

Fields produced:
  contact: name / title / email / phone       (facing page via OCR)
  financials.period_end                        (SoFC heading)
  financials.total_assets                      (SoFC)
  financials.total_liabilities                 (SoFC)
  financials.members_equity / stockholders_equity (SoFC)
  financials.net_capital                       (Computation table when present)
  auditor_name / auditor_pcaob_id              (Accountant Identification block)

Validated against &Partners, LLC 2025 Part III filing.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..schema.models import FinancialStanding, FocusReport, PrimaryContact
from .base import extract_pdf, parse_money

logger = logging.getLogger(__name__)


_PHONE = re.compile(r"\b(\d{3})[\s\-.]?(\d{3})[\s\-.]?(\d{4})\b")
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_MONEY = re.compile(r"\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_focus_pdf(pdf_bytes: bytes) -> FocusReport:
    extracted = extract_pdf(pdf_bytes)
    text = extracted.full_text
    report = FocusReport(raw_pdf_hash=extracted.sha256)

    _parse_facing_page(text, report)
    _parse_oath_title(text, report)
    _parse_accountant_block(text, report)
    _parse_statement_of_financial_condition(text, report)
    _parse_net_capital(text, report)

    if not report.contact.full_name:
        report.parse_warnings.append("contact_name_not_found")
    if not report.financials.total_assets:
        report.parse_warnings.append("total_assets_not_found")

    return report


# ---------------------------------------------------------------------------
# Facing Page (Registrant Identification + Contact)
# ---------------------------------------------------------------------------

def _parse_facing_page(text: str, report: FocusReport) -> None:
    # SEC file number — "8-03774" style, adjacent to "FORM X-17A-5" header
    sec_file = _find(r"(?:SEC\s+FILE\s+NUMBER|FORM\s+X-17A-5)\s*[\n\s]*(\d{1,2}[\- ]\d{3,6})", text)
    if sec_file:
        report.sec_file_number = sec_file.strip().replace(" ", "-")

    # Period
    period = re.search(
        r"FILING\s+FOR\s+THE\s+PERIOD\s+BEGINNING\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+AND\s+ENDING\s+(\d{1,2}/\d{1,2}/\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if period:
        report.period_beginning = _parse_date(period.group(1))
        report.period_ending = _parse_date(period.group(2))

    # Firm name — directly follows "NAME OF FIRM:" on same or next line.
    # Be tolerant of OCR inserting a newline between the label and the value.
    m = re.search(
        r"NAME\s+OF\s+FIRM\s*[:\-]?\s*\n?\s*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip()
        # If OCR put the name BEFORE the label (some scans do), fall back
        if candidate.upper().startswith("TYPE OF REGISTRANT") or len(candidate) < 3:
            pre = re.search(r"([^\n]+)\n\s*NAME\s+OF\s+FIRM", text, re.IGNORECASE)
            if pre:
                candidate = pre.group(1).strip()
        report.firm_name = candidate

    # Contact line: "Michael C Schaller  314-897-4920  mike@firm.com"
    contact_block = _slice_between(
        text,
        start_marker=r"PERSON\s+TO\s+CONTACT\s+WITH\s+REGARD\s+TO\s+THIS\s+FILING",
        end_marker=r"B\.\s+ACCOUNTANT\s+IDENTIFICATION|ACCOUNTANT\s+IDENTIFICATION",
    )
    if contact_block:
        email_match = _EMAIL.search(contact_block)
        phone_match = _PHONE.search(contact_block)

        email = email_match.group(0) if email_match else None
        phone = (
            f"{phone_match.group(1)}-{phone_match.group(2)}-{phone_match.group(3)}"
            if phone_match
            else None
        )

        # Name = the non-label text on the line that contains the phone or email.
        name = None
        for line in contact_block.splitlines():
            if email_match and email_match.group(0) in line or phone_match and phone_match.group(0) in line:
                # Strip phone + email from the line; what's left is the name.
                stripped = _PHONE.sub("", _EMAIL.sub("", line)).strip()
                # Remove parenthetical labels like "(Name)"
                stripped = re.sub(r"\([^)]+\)", "", stripped).strip()
                # OCR sometimes joins initials: "MichaelC" -> "Michael C"
                stripped = re.sub(r"([a-z])([A-Z])", r"\1 \2", stripped)
                if 2 <= len(stripped.split()) <= 6:
                    name = stripped
                    break

        report.contact = PrimaryContact(
            full_name=name,
            email=email,
            phone=phone,
        )


# ---------------------------------------------------------------------------
# Oath or Affirmation — title lives here
# ---------------------------------------------------------------------------

_TITLE_KEYWORDS = re.compile(
    r"\b(FinOp|Controller|Officer|Director|President|Manager|Partner|Principal|"
    r"CFO|CEO|COO|CCO|Compliance|Treasurer|Secretary|Chairman|Chief)\b",
    re.IGNORECASE,
)

# Words that act as phrase connectors inside a title (kept when expanding)
_TITLE_CONNECTORS = {"and", "of", "&", "the", "for", "-"}

# Words that signal OCR noise adjacent to a title (stop expansion on these)
_TITLE_NOISE = {
    "st", "st.", "saint", "county", "state", "missouri", "notary",
    "public", "seal", "commission", "expires", "signature", "title",
    "number", "registration", "date", "filing", "sworn", "affirm",
}


def _parse_oath_title(text: str, report: FocusReport) -> None:
    """Find a plausible title phrase in the window after 'Title:'.

    Strategy: locate the first job-title keyword, then greedily expand the
    phrase in both directions over title-case words and connectors, stopping
    at known noise words or line breaks. This correctly pulls
    'FinOp and Controller' out of the OCR-merged line
    'St. Louis County FinOp and Controller'.
    """
    m = re.search(r"Title\s*[:\-]", text, re.IGNORECASE)
    if not m:
        return

    window = text[m.end(): m.end() + 500]

    for kw in _TITLE_KEYWORDS.finditer(window):
        phrase = _expand_title_phrase(window, kw.start(), kw.end())
        if phrase and 4 <= len(phrase) <= 80:
            _set_title(report, phrase)
            return


def _expand_title_phrase(window: str, kw_start: int, kw_end: int) -> Optional[str]:
    """Expand outward from a keyword to build a clean title phrase."""
    # Determine the line containing the keyword (title phrases don't cross lines)
    line_start = window.rfind("\n", 0, kw_start) + 1
    line_end = window.find("\n", kw_end)
    if line_end == -1:
        line_end = len(window)
    line = window[line_start:line_end]
    kw_offset = kw_start - line_start  # keyword position within the line

    tokens = line.split()
    if not tokens:
        return None

    # Locate which token contains the keyword
    cursor = 0
    kw_token_idx = 0
    for i, tok in enumerate(tokens):
        tok_start = line.find(tok, cursor)
        tok_end = tok_start + len(tok)
        if tok_start <= kw_offset < tok_end:
            kw_token_idx = i
            break
        cursor = tok_end

    # Expand backward
    left = kw_token_idx
    for i in range(kw_token_idx - 1, -1, -1):
        tok = tokens[i].strip(".,;:")
        low = tok.lower()
        if low in _TITLE_NOISE:
            break
        if low in _TITLE_CONNECTORS or re.match(r"^[A-Z][A-Za-z&]*$", tok):
            left = i
        else:
            break

    # Expand forward
    right = kw_token_idx
    for i in range(kw_token_idx + 1, len(tokens)):
        tok = tokens[i].strip(".,;:")
        low = tok.lower()
        if low in _TITLE_NOISE:
            break
        if low in _TITLE_CONNECTORS or re.match(r"^[A-Z][A-Za-z&]*$", tok):
            right = i
        else:
            break

    phrase_tokens = tokens[left: right + 1]
    # Trim leading/trailing connectors
    while phrase_tokens and phrase_tokens[0].lower() in _TITLE_CONNECTORS:
        phrase_tokens.pop(0)
    while phrase_tokens and phrase_tokens[-1].lower() in _TITLE_CONNECTORS:
        phrase_tokens.pop()
    if not phrase_tokens:
        return None
    return " ".join(phrase_tokens).strip(".,;:")


def _set_title(report: FocusReport, title: str) -> None:
    if report.contact:
        report.contact = report.contact.model_copy(update={"title": title})
    else:
        report.contact = PrimaryContact(title=title)


# ---------------------------------------------------------------------------
# Accountant identification
# ---------------------------------------------------------------------------

def _parse_accountant_block(text: str, report: FocusReport) -> None:
    block = _slice_between(
        text,
        start_marker=r"B\.\s+ACCOUNTANT\s+IDENTIFICATION|INDEPENDENT\s+PUBLIC\s+ACCOUNTANT",
        end_marker=r"FOR\s+OFFICIAL\s+USE\s+ONLY|OATH\s+OR\s+AFFIRMATION",
    )
    if not block:
        return

    # Auditor name — line right after "contained in this filing*"
    m = re.search(
        r"whose\s+reports\s+are\s+contained\s+in\s+this\s+filing\*?\s*\n?\s*([^\n]+)",
        block,
        re.IGNORECASE,
    )
    if m:
        report.auditor_name = m.group(1).strip()

    # PCAOB number — on the facing page the data row appears ABOVE the label
    # row: "01/25/2005                    2100" then "(Date of Registration
    # with PCAOB)        (PCAOB Registration Number)".
    # Strategy: find the "(PCAOB Registration Number" label and walk back to
    # the nearest 3-5 digit number on a preceding line.
    label_m = re.search(r"\(PCAOB\s+Registration\s+Number", block, re.IGNORECASE)
    if label_m:
        preceding = block[: label_m.start()]
        numeric_hits = list(re.finditer(r"\b(\d{3,5})\b", preceding))
        if numeric_hits:
            report.auditor_pcaob_id = numeric_hits[-1].group(1)


# ---------------------------------------------------------------------------
# Statement of Financial Condition
# ---------------------------------------------------------------------------

# Money-on-line pattern: label ... optional "$" ... number
_SOFC_LINE = re.compile(
    r"(?P<label>.+?)\s+\$?\s*(?P<val>\(?-?[\d,]+(?:\.\d+)?\)?)\s*$",
    re.MULTILINE,
)

_PERIOD_HEADING = re.compile(
    r"(?:AS\s+OF\s+|FINANCIAL\s+CONDITION\s*\n\s*)?"
    r"(?P<month>JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)"
    r"\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)


def _parse_statement_of_financial_condition(text: str, report: FocusReport) -> None:
    # The heading appears in the filing checklist, cover page, auditor letter,
    # and footnotes as well as the actual statement. Find the occurrence whose
    # following ~500 chars contain the ASSETS heading AND at least one dollar
    # amount — that's the real Statement.
    start = None
    for m in re.finditer(r"STATEMENT\s+OF\s+FINANCIAL\s+CONDITION", text, re.IGNORECASE):
        window = text[m.end(): m.end() + 600]
        if re.search(r"\bASSETS\b", window) and re.search(r"[\d,]{5,}", window):
            start = m
            break
    if not start:
        return

    tail = text[start.end():]
    end_match = re.search(
        r"(The\s+accompanying\s+notes|STATEMENT\s+OF\s+(?:OPERATIONS|INCOME|CASH\s+FLOWS|CHANGES)|NOTES?\s+TO\s+(?:THE\s+)?FINANCIAL)",
        tail,
        re.IGNORECASE,
    )
    sofc_text = tail[: end_match.start()] if end_match else tail[:5000]

    # Period end date from heading (first 300 chars)
    pd = _PERIOD_HEADING.search(sofc_text[:300])
    if pd:
        try:
            report.financials.period_end = datetime.strptime(
                f"{pd.group('month')} {pd.group('day')} {pd.group('year')}",
                "%B %d %Y",
            ).date()
        except ValueError:
            pass

    # Extract each line: label + trailing number
    found = _extract_sofc_inline(sofc_text, report)
    if not found:
        _extract_sofc_split(sofc_text, report)


def _extract_sofc_inline(sofc_text: str, report: FocusReport) -> bool:
    """pdfplumber typically keeps 'Label ... Amount' on the same line."""
    rows: dict[str, Decimal] = {}
    for m in _SOFC_LINE.finditer(sofc_text):
        label = m.group("label").strip().lower()
        val_str = m.group("val")
        val = _to_decimal(val_str)
        if val is None:
            continue
        rows[label] = val

    any_hit = False
    for label, val in rows.items():
        if re.search(r"\btotal\s+assets\b", label):
            report.financials.total_assets = val
            any_hit = True
        elif re.search(r"\btotal\s+liabilities\b", label) and "equity" not in label:
            report.financials.total_liabilities = val
            any_hit = True
        elif re.match(r"^member'?s?\s+equity\b", label):
            report.financials.members_equity = val
            any_hit = True
        elif re.match(r"^stockholders?'?\s+equity\b", label):
            report.financials.stockholders_equity = val
            any_hit = True
    return any_hit


def _extract_sofc_split(sofc_text: str, report: FocusReport) -> None:
    """PyMuPDF sometimes emits all labels then all numbers. Align by order."""
    # Collect labels (lines without digits) and numbers (lines that are pure numeric)
    labels: list[str] = []
    numbers: list[Decimal] = []
    for raw in sofc_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?", line):
            val = _to_decimal(line)
            if val is not None:
                numbers.append(val)
        elif re.search(r"[A-Za-z]", line) and not re.search(r"\d", line):
            labels.append(line)

    # Heuristic: align the last N labels with the first N numbers, skipping
    # section headers like "ASSETS", "Liabilities"
    keep = [
        lab for lab in labels
        if not re.fullmatch(
            r"(ASSETS|LIABILITIES|LIABILITIES\s+AND\s+MEMBER'?S?\s+EQUITY|Liabilities|Member'?s?\s+Equity)",
            lab,
            re.IGNORECASE,
        )
        or re.search(r"total|member|stockholders", lab, re.IGNORECASE)
    ]

    # Walk labels; if a label looks like a target, pair it with the next unused number
    num_iter = iter(numbers)
    for label in keep:
        low = label.lower()
        try:
            val = next(num_iter)
        except StopIteration:
            break
        if re.search(r"\btotal\s+assets\b", low) and report.financials.total_assets is None:
            report.financials.total_assets = val
        elif re.search(r"\btotal\s+liabilities\b", low) and "equity" not in low:
            report.financials.total_liabilities = val
        elif re.search(r"member'?s?\s+equity\b", low) and report.financials.members_equity is None:
            report.financials.members_equity = val
        elif re.search(r"stockholders?'?\s+equity", low):
            report.financials.stockholders_equity = val


# ---------------------------------------------------------------------------
# Net Capital (Computation — may be absent from Part III filings)
# ---------------------------------------------------------------------------

def _parse_net_capital(text: str, report: FocusReport) -> None:
    # Must be anchored to financial reporting context, not the regulatory
    # citations like "17 CFR 240.15c3-1" or "net capital under 17 CFR...".
    # Require: "Net Capital" followed within 50 chars by a $ sign and a number
    # of at least 4 digits (excludes CFR paragraph numbers).
    pattern = re.compile(
        r"\bNet\s+Capital\b(?![^\n]*?\bCFR\b)[^\n$]{0,50}\$\s*(\(?-?[\d,]{4,}(?:\.\d+)?\)?)",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if m:
        val = _to_decimal(m.group(1))
        if val is not None:
            report.financials.net_capital = val


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find(pattern: str, text: str, flags: int = re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def _slice_between(text: str, start_marker: str, end_marker: str) -> Optional[str]:
    s = re.search(start_marker, text, re.IGNORECASE)
    if not s:
        return None
    after = text[s.end():]
    e = re.search(end_marker, after, re.IGNORECASE)
    return after[: e.start()] if e else after


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_decimal(s: str) -> Optional[Decimal]:
    cleaned = parse_money(s)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
