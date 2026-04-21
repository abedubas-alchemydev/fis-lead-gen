"""Fast, free PDF text extraction for X-17A-5 FOCUS Reports using pdfplumber.

Extracts:
  1. Contact person name (from the Facing Page)
  2. Contact phone number
  3. Contact email
  4. Net Capital (from the Computation of Net Capital schedule or Notes)

This runs in milliseconds per PDF with zero API cost — used as the primary
extraction method. Gemini vision is the fallback for PDFs where text
extraction fails (scanned images, non-standard layouts).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PdfTextExtractionResult:
    """Structured result from pdfplumber text extraction."""
    contact_name: str | None = None
    contact_title: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    net_capital: float | None = None
    excess_net_capital: float | None = None
    report_date: str | None = None
    success: bool = False


# ──────────────────────────────────────────────────────────────
# Phone / email patterns
# ──────────────────────────────────────────────────────────────

_PHONE_PATTERN = re.compile(
    r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}'
)

_EMAIL_PATTERN = re.compile(
    r'[\w.\-+]+@[\w.\-]+\.\w{2,}'
)


# ──────────────────────────────────────────────────────────────
# Amount parsing
# ──────────────────────────────────────────────────────────────

def _parse_dollar_amount(text: str) -> float | None:
    """Parse a dollar amount string like '$24,860' or '(3,501,687)' into a float."""
    s = text.replace("$", "").replace(" ", "").replace("l", "1").replace("I", "1")
    s = s.strip()
    if not s:
        return None
    is_negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace(",", "")
    # Remove trailing dots or non-digit chars
    s = re.sub(r'[^\d.]', '', s)
    if not s:
        return None
    try:
        value = float(s)
        return -value if is_negative else value
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────
# Net capital extraction from text
# ──────────────────────────────────────────────────────────────

# Pattern 1: "had net capital of $X,XXX" or "net capital of $X"
_NET_CAPITAL_NARRATIVE = re.compile(
    r'(?:had|has)\s+(?:adjusted\s+)?net\s+capital\s+of\s+\$?([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)

# Pattern 2: "Net Capital $ 24,860" (schedule line)
_NET_CAPITAL_SCHEDULE = re.compile(
    r'^(?:\d+\.\s+)?(?:Adjusted\s+)?Net\s+Capital\s+[\s.]*\$?\s*([\d,]+(?:\.\d+)?)',
    re.IGNORECASE | re.MULTILINE,
)

# Pattern 3: "Excess Net Capital $ 19,860" or "in excess of ... $X"
_EXCESS_NET_CAPITAL = re.compile(
    r'(?:Excess\s+(?:Adjusted\s+)?Net\s+Capital|in\s+excess\s+of\s+(?:its\s+)?(?:the\s+)?required\s+(?:adjusted\s+)?net\s+capital\s+(?:of|by)\s+)\$?\s*([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)


def _extract_net_capital_from_text(full_text: str) -> tuple[float | None, float | None]:
    """Extract net capital and excess net capital from the full PDF text."""
    net_capital: float | None = None
    excess: float | None = None

    # Try narrative pattern first (most reliable — from auditor's notes)
    match = _NET_CAPITAL_NARRATIVE.search(full_text)
    if match:
        net_capital = _parse_dollar_amount(match.group(1))

    # Try schedule pattern
    if net_capital is None:
        # Find the "COMPUTATION OF NET CAPITAL" section and look for the final "Net Capital" line
        computation_start = re.search(r'COMPUTATION\s+OF\s+(?:BASIC\s+)?NET\s+CAPITAL', full_text, re.IGNORECASE)
        if computation_start:
            section = full_text[computation_start.start():]
            # Look for "Net Capital" lines (not "Net Capital before haircuts" or "requirement")
            for line_match in re.finditer(
                r'^(?:\d+\.\s+)?(?:Adjusted\s+)?Net\s+Capital\b(?!\s+(?:before|requirement|ratio|in excess|less))[\s.]*\$?\s*([\d,]+)',
                section,
                re.IGNORECASE | re.MULTILINE,
            ):
                val = _parse_dollar_amount(line_match.group(1))
                if val is not None and val > 0:
                    net_capital = val
                    break

    # Try excess net capital
    excess_match = _EXCESS_NET_CAPITAL.search(full_text)
    if excess_match:
        excess = _parse_dollar_amount(excess_match.group(1))

    return net_capital, excess


# ──────────────────────────────────────────────────────────────
# Contact info extraction from the Facing Page
# ──────────────────────────────────────────────────────────────

def _extract_contact_from_text(full_text: str) -> tuple[str | None, str | None, str | None]:
    """Extract contact name, phone, and email from the Facing Page."""
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None

    lines = [line.strip() for line in full_text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        upper = line.upper()

        if "PERSON TO CONTACT" in upper and "FILING" in upper:
            # The contact info is on the next non-empty line(s)
            for j in range(i + 1, min(i + 6, len(lines))):
                candidate = lines[j].strip()
                if not candidate or candidate.startswith("(") and "Name" in candidate:
                    continue

                # Extract email
                email_match = _EMAIL_PATTERN.search(candidate)
                if email_match:
                    contact_email = email_match.group(0)
                    candidate = candidate.replace(contact_email, "").strip()

                # Extract phone
                phone_match = _PHONE_PATTERN.search(candidate)
                if phone_match:
                    contact_phone = phone_match.group(0)
                    candidate = candidate.replace(contact_phone, "").strip()

                # What remains is the name
                # Clean up separators and trailing punctuation
                candidate = re.sub(r'[,;|/]+$', '', candidate).strip()
                if candidate and not candidate.startswith("(") and len(candidate) > 2:
                    if contact_name is None:
                        contact_name = candidate
            break

    # If we didn't find phone/email on the contact line, scan nearby lines
    if contact_name and (not contact_phone or not contact_email):
        # Look in the area around where we found the contact
        for i, line in enumerate(lines):
            if "PERSON TO CONTACT" in line.upper():
                search_zone = "\n".join(lines[i:min(i + 10, len(lines))])
                if not contact_phone:
                    phone_match = _PHONE_PATTERN.search(search_zone)
                    if phone_match:
                        contact_phone = phone_match.group(0)
                if not contact_email:
                    email_match = _EMAIL_PATTERN.search(search_zone)
                    if email_match:
                        contact_email = email_match.group(0)
                break

    return contact_name, contact_phone, contact_email


# ──────────────────────────────────────────────────────────────
# Main extraction function
# ──────────────────────────────────────────────────────────────

def extract_from_pdf(pdf_path: str | Path) -> PdfTextExtractionResult:
    """Extract contact info + net capital from an X-17A-5 PDF using pdfplumber.

    This is the fast, free extraction path. Returns a result with success=True
    if at least a contact name or net capital was found.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PdfTextExtractionResult()

    try:
        import pdfplumber

        full_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"

        if len(full_text.strip()) < 100:
            # PDF is likely scanned images with no extractable text
            return PdfTextExtractionResult()

        # Extract contact info
        contact_name, contact_phone, contact_email = _extract_contact_from_text(full_text)

        # Extract net capital
        net_capital, excess_net_capital = _extract_net_capital_from_text(full_text)

        has_data = bool(contact_name) or net_capital is not None

        return PdfTextExtractionResult(
            contact_name=contact_name,
            contact_phone=contact_phone,
            contact_email=contact_email,
            net_capital=net_capital,
            excess_net_capital=excess_net_capital,
            success=has_data,
        )

    except Exception as exc:
        logger.warning("pdfplumber extraction failed for %s: %s", pdf_path.name, exc)
        return PdfTextExtractionResult()
