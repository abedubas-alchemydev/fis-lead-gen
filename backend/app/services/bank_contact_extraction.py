"""Bank-contact extraction from OCC charter-application public-portion PDFs.

The banks-vertical sibling of ``focus_ceo_extraction`` (which pulls the
"PERSON TO CONTACT" off X-17A-5 PDFs into ``executive_contacts``): for a bank
whose ``digital_asset_pdfs`` carries public-portion application PDF URLs
(stored by the watcher's digital-assets phase, https occ.gov only), download
each PDF, extract its text, and conservatively parse the PEOPLE the filing
names. Per the client: "contacts, everything we can grab" from these PDFs.

What is extracted (``role_context`` on each row):

- ``contact_person``   — the "Contact person" / "Primary contact" block (or
  a "please contact Jane Doe at ..." sentence), plus any phone/email printed
  alongside;
- ``organizer``        — names under an "Organizers" / "Organizing group"
  heading or an "the organizers are ..." sentence;
- ``proposed_officer`` — "proposed President / CEO / director / Chief *
  Officer" sentences, title captured verbatim;
- ``counsel``          — counsel-of-record ("Counsel:", "Attorney for the
  applicant:", "Jane Doe, counsel to the organizers").

Conservative philosophy (mirrors ``focus_ceo_extraction`` and the watcher's
never-guess matching): a person is persisted ONLY when the pattern around the
hit is unambiguous AND the candidate passes strict person-name validation
(2-4 title-cased words, no digits, no org vocabulary). Anything else — a law
firm where a person was expected, an ALL-CAPS section header, a "TBD" — is
logged and counted as ``skipped_ambiguous``, never written. Every stored row
keeps its receipt: ``page_number`` + ``context_snippet`` + ``source_url``.

Safety rails:

- **Host allowlist** — URLs are re-verified against the same occ.gov
  allowlist the scraper used before persisting them (https + occ.gov /
  *.occ.gov only), both BEFORE the request and AFTER redirects. Defense in
  depth: the DB values should already be clean, but a vandalized row must
  not turn this service into a generic fetcher.
- **Size cap / timeout** — downloads are streamed with a 20MB ceiling
  (header AND counted bytes) and a bounded timeout.
- **Crash isolation** — text extraction runs in a throwaway subprocess
  (:mod:`app.services._pdf_text_worker`, the pdfminer sibling of
  ``_pdf_render_worker``): a corrupt PDF that hangs or kills the child is
  contained to that child and degrades to a parse-miss, never killing the
  watcher run.
- **Idempotent upserts** — keyed on ``(bank_id, name, coalesce(title,''),
  source)`` (the ``uq_bank_contacts_dedupe`` index); re-running over the
  same PDFs is a no-op.

Wired into ``scripts/watch_bank_charters.py`` as the opt-in
``--extract-contacts`` phase (dry-run by default; ``--apply`` writes). See
docs/runbooks/bank-charter-watch.md, "Extracting contacts from application
PDFs".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.bank import BankContact
from app.services.occ_cas import _is_allowed_occ_url
from app.services.pdf_downloader import pdf_tempdir

logger = logging.getLogger(__name__)

# ── Role contexts (BankContact.role_context vocabulary) ─────────────────────
ROLE_CONTACT_PERSON = "contact_person"
ROLE_ORGANIZER = "organizer"
ROLE_PROPOSED_OFFICER = "proposed_officer"
ROLE_COUNSEL = "counsel"

DEFAULT_SOURCE = "application_pdf"

# ── Fetch limits ─────────────────────────────────────────────────────────────
# Public-portion application PDFs are a few hundred KB to a few MB; 20MB is
# a generous ceiling that still bounds a mis-linked giant file.
MAX_PDF_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 30.0

# ── Text-extraction subprocess bounds (both env-overridable for ops) ─────────
# Absolute path to the isolated extractor, invoked as a bare script — same
# containment contract as focus_ceo_extraction's _RENDER_WORKER.
_TEXT_WORKER = Path(__file__).with_name("_pdf_text_worker.py")
_TEXT_SUBPROCESS_TIMEOUT_SECONDS = int(
    os.environ.get("BANK_CONTACT_TEXT_TIMEOUT_SECONDS", "120")
)
# The people this service extracts (contact block, organizers, proposed
# officers, counsel) live in the application narrative up front; 40 pages
# covers every public portion seen so far while bounding a pathological file.
_TEXT_MAX_PAGES = int(os.environ.get("BANK_CONTACT_TEXT_MAX_PAGES", "40"))


def is_allowed_application_pdf_url(url: object) -> bool:
    """https + occ.gov / *.occ.gov only.

    Delegates to the security-reviewed allowlist the digital-assets scraper
    applies before persisting a PDF link (``occ_cas._is_allowed_occ_url``) so
    there is exactly ONE definition of "an allowed occ.gov URL" in the
    codebase. Never raises.
    """
    return isinstance(url, str) and _is_allowed_occ_url(url)


# ── Extraction result shapes ────────────────────────────────────────────────


@dataclass(slots=True)
class ExtractedBankContact:
    """One unambiguously-parsed person from a public-portion PDF."""

    name: str
    title: str | None
    role_context: str
    email: str | None
    phone: str | None
    source_url: str
    page_number: int | None
    context_snippet: str | None
    source: str = DEFAULT_SOURCE


@dataclass(slots=True)
class BankContactExtractionStats:
    """Counts for one bank (or, summed, one run) — the watcher's summary keys.

    - ``pdfs_fetched``       → ``bank_contacts_pdfs_fetched``: PDFs actually
      downloaded (allowlist-passed, HTTP 200, under the size cap).
    - ``contacts_extracted`` → ``bank_contacts_extracted``: unambiguous people
      parsed (deduped per bank). In apply mode these are upserted; the upsert
      is idempotent, so a re-run reports the same count and changes nothing.
    - ``skipped_ambiguous``  → ``bank_contacts_skipped_ambiguous``: pattern
      hits whose person candidate failed conservative validation — logged and
      never written (the never-guess policy's visible counter).
    """

    pdfs_fetched: int = 0
    contacts_extracted: int = 0
    skipped_ambiguous: int = 0

    def add(self, other: "BankContactExtractionStats") -> None:
        self.pdfs_fetched += other.pdfs_fetched
        self.contacts_extracted += other.contacts_extracted
        self.skipped_ambiguous += other.skipped_ambiguous


# ── Person-name / title validation (the never-guess gate) ───────────────────

_NAME_WORD_RE = re.compile(r"^[A-Z][a-z]+(?:[-'’][A-Z][a-z]+)?$")
_NAME_INITIAL_RE = re.compile(r"^[A-Z]\.?$")
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "esq"})
_HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "hon"})
# Vocabulary that marks a candidate as an organization / form artifact, not a
# person. Includes title words so "Proposed President" can't be stored as a
# name — titles ride in the title column, captured by their own patterns.
_NON_PERSON_WORDS = frozenset(
    {
        "bank", "banks", "trust", "national", "association", "company",
        "companies", "corp", "corporation", "incorporated", "llc", "llp",
        "lp", "inc", "group", "holdings", "partners", "advisors", "office",
        "comptroller", "currency", "occ", "fdic", "federal", "reserve",
        "application", "applications", "charter", "filing", "portion",
        "public", "confidential", "exhibit", "section", "appendix",
        "organizer", "organizers", "organizing", "contact", "person",
        "proposed", "director", "directors", "president", "officer",
        "officers", "board", "chairman", "chairwoman", "chief", "executive",
        "counsel", "attorney", "department", "digital", "assets", "the",
        "and", "for", "new", "name", "telephone", "email", "address",
    }
)


def looks_like_person_name(candidate: str | None) -> bool:
    """Strict person-name shape: 2-4 title-cased words (initials and
    generational/professional suffixes allowed), no digits, no '@', no
    organization/form vocabulary, and at least two full words (a lone
    "J. P." never qualifies). ALL-CAPS section headers ("PUBLIC PORTION")
    and firm names ("Sullivan & Cromwell LLP") fail by construction."""
    if not candidate:
        return False
    cleaned = " ".join(candidate.split()).strip(" ,;:")
    if not cleaned or len(cleaned) > 64:
        return False
    if any(ch.isdigit() for ch in cleaned) or "@" in cleaned:
        return False
    words = [w for w in cleaned.replace(",", " ").split() if w]
    # Leading honorific ("Mr. Jane Doe" never prints, but "Dr." does).
    if words and words[0].rstrip(".").lower() in _HONORIFICS:
        words = words[1:]
    # Trailing suffixes ride with the name but don't count toward shape.
    while words and words[-1].rstrip(".").lower() in _NAME_SUFFIXES:
        words = words[:-1]
    if not 2 <= len(words) <= 4:
        return False
    full_words = 0
    for word in words:
        if word.rstrip(".").lower() in _NON_PERSON_WORDS:
            return False
        if _NAME_WORD_RE.match(word):
            full_words += 1
            continue
        if _NAME_INITIAL_RE.match(word):
            continue
        return False
    return full_words >= 2


_TITLE_KEYWORD_RE = re.compile(
    r"(?i)\b(president|chair(?:man|woman|person)?|chief|officers?|directors?|"
    r"treasurer|secretary|counsel|attorney|partner|founder|organizer|"
    r"manager|principal|executive|esq)\b"
)


def _clean_title(candidate: str | None) -> str | None:
    """Accept a title only when it is short, digit-free, and contains a
    recognizable title keyword; otherwise None (store no title rather than
    a guess)."""
    if not candidate:
        return None
    cleaned = " ".join(candidate.split()).strip(" ,;:.-")
    if not cleaned or len(cleaned) > 120:
        return None
    if any(ch.isdigit() for ch in cleaned) or "@" in cleaned:
        return None
    if not _TITLE_KEYWORD_RE.search(cleaned):
        return None
    return cleaned


def _refine_person_name(candidate: str | None, *, trim: str = "tail") -> str | None:
    """Trim a greedy regex name span down to a valid person name.

    The name-span regexes capture runs of capitalized tokens and can cross a
    sentence boundary ("Alice M. Founder. The"); rather than guess in the
    regex, tokens are dropped from the ``tail`` (name-first patterns) or the
    ``head`` (name-before patterns) until the remainder passes
    :func:`looks_like_person_name` — or nothing does and the hit is refused.
    A sentence period after a lowercase letter is stripped ("Founder." →
    "Founder") while initials ("M.") survive.
    """
    if not candidate:
        return None
    tokens = " ".join(candidate.split()).split()
    while len(tokens) >= 2:
        name = re.sub(r"(?<=[a-z])[.,;:]+$", "", " ".join(tokens))
        if looks_like_person_name(name):
            return name
        tokens = tokens[:-1] if trim == "tail" else tokens[1:]
    return None


_SENTENCE_END_RE = re.compile(r"(?<=[a-z])\.(?=\s|$)")


def _cut_sentence(text: str) -> str:
    """Cut at the first sentence-ending period (one following a lowercase
    letter — middle initials like "Dana E. Fox" survive)."""
    return _SENTENCE_END_RE.split(text, maxsplit=1)[0]


def _split_name_title(raw: str) -> tuple[str | None, str | None]:
    """'Jane Doe, President' -> ('Jane Doe', 'President'); 'Jane Doe' ->
    ('Jane Doe', None). Suffixes re-attach to the name ('John Roe, Jr.,
    Esq.'). Returns (None, None) when no valid person name leads the text."""
    cleaned = " ".join(raw.split()).strip(" ;:")
    if not cleaned:
        return None, None
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return None, None
    name = parts[0]
    rest = parts[1:]
    while rest and rest[0].rstrip(".").lower() in _NAME_SUFFIXES:
        name = f"{name} {rest[0]}"
        rest = rest[1:]
    if not looks_like_person_name(name):
        return None, None
    title = _clean_title(", ".join(rest)) if rest else None
    return " ".join(name.split()), title


# ── Channel patterns (same shapes as pdf_text_extractor) ────────────────────

_EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.\w{2,}")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")


def _strip_channels(text: str) -> tuple[str, str | None, str | None]:
    """Remove the first email/phone from ``text``; return (remainder, email,
    phone)."""
    email = phone = None
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0)
        text = text.replace(email, " ")
    m = _PHONE_RE.search(text)
    if m:
        phone = m.group(0)
        text = text.replace(phone, " ")
    return " ".join(text.split()), email, phone


def _snippet(text: str, start: int, end: int, *, radius: int = 70, limit: int = 240) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return " ".join(text[lo:hi].split())[:limit]


# ── Pattern set ──────────────────────────────────────────────────────────────
# Line-anchored labels require an explicit ':' (or a bare heading) so prose
# like "Contact John Smith for questions" never trips the label path.

_CONTACT_LABEL_RE = re.compile(
    r"^(?:primary\s+|point\s+of\s+)?contact(?:\s+person)?(?:\s+information)?"
    r"\s*(?P<sep>:)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_PERSON_TO_CONTACT_RE = re.compile(
    r"^person\s+to\s+contact"
    r"(?:\s+regarding\s+this\s+(?:report|application|filing))?"
    r"\s*(?P<sep>:)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
# Sub-labels inside a labeled contact block (skipped while hunting the name).
_BLOCK_SUBLABEL_RE = re.compile(
    r"^(?:name|title|telephone|phone|email(?:\s+address)?|address)\s*[:\-]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_ORGANIZERS_HEADING_RE = re.compile(
    r"^(?:the\s+)?(?:organizers?|organizing\s+group)"
    r"(?:\s+of\s+the\s+(?:proposed\s+)?(?:bank|association|trust\s+company))?"
    r"\s*(?P<sep>:)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_ORGANIZERS_INLINE_RE = re.compile(
    r"\b(?:organizers|organizing\s+group)\b[^.;:]{0,60}?\b(?:are|include)\b\s*:?\s*",
    re.IGNORECASE,
)

# "proposed <title>" — the title phrase is matched case-insensitively; the
# person name is matched case-SENSITIVELY afterwards so the title-case shape
# actually anchors (re.IGNORECASE on a name class would match anything).
_TITLE_CORE = (
    r"(?:chief\s+[a-z]+\s+officer|president|chair(?:man|woman|person)?"
    r"(?:\s+of\s+the\s+board)?|directors?|general\s+counsel|ceo|cfo|coo|cro|cco)"
)
_TITLE_PHRASE = (
    rf"(?:(?:senior|executive|vice)\s+)*{_TITLE_CORE}"
    rf"(?:\s*(?:,|and|&)\s*(?:(?:senior|executive|vice)\s+)*{_TITLE_CORE})*"
)
_PROPOSED_RE = re.compile(rf"\bproposed\s+(?P<title>{_TITLE_PHRASE})", re.IGNORECASE)

_NAME_SPAN = r"[A-Z][a-zA-Z.'’\-]+(?:\s+[A-Z][a-zA-Z.'’\-]*){1,3}"
_HONORIFIC_SPAN = r"(?:(?:Mr|Mrs|Ms|Dr)\.\s+)?"
_NAME_AFTER_RE = re.compile(
    rf"^\s*(?:[:,]\s*|\b(?:is|will\s+be|would\s+be)\b\s+)?{_HONORIFIC_SPAN}(?P<name>{_NAME_SPAN})"
)
_NAMES_LIST_AFTER_RE = re.compile(
    r"^\s*(?:(?:are|include)\b|:)\s*:?\s*(?P<names>[^;()]{1,300})", re.IGNORECASE
)
_NAME_BEFORE_RE = re.compile(
    rf"(?P<name>{_NAME_SPAN})\s*,?\s*(?:Esq\.?,?\s*)?"
    r"(?:the\s+|as\s+(?:the\s+)?|is\s+the\s+|will\s+serve\s+as\s+(?:the\s+)?)?$"
)

_COUNSEL_LABEL_RE = re.compile(
    r"^(?:legal\s+)?(?:counsel|attorney)"
    r"(?:\s+(?:of\s+record|for|to)\s+(?:the\s+)?(?:applicant|organizers|proposed\s+bank|bank))?"
    r"\s*(?P<sep>:)\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_COUNSEL_INLINE_RE = re.compile(
    r"\b(?:as\s+)?(?:legal\s+)?counsel\s+(?:of\s+record\s+)?(?:for|to)\s+the\s+"
    r"(?:applicant|organizers|proposed\s+bank|bank)\b",
    re.IGNORECASE,
)
_INLINE_CONTACT_RE = re.compile(r"\b(?:please\s+|may\s+)contact\s+", re.IGNORECASE)
# The inline-contact name must be followed by a channel-ish continuation
# ("at", "by", "via", or an immediately-opening phone paren) so narrative
# like "contact John Smith about anything" stays out.
_INLINE_CONTACT_NAME_RE = re.compile(
    rf"^{_HONORIFIC_SPAN}(?P<name>{_NAME_SPAN})\s*(?:,?\s+(?:at|by|via)\b|,?\s*\()"
)


def _split_person_list(raw: str) -> tuple[list[str], int]:
    """Split 'John Smith, Jane Doe and Robert Roe' into validated names.

    Returns (names, ambiguous) — ambiguous counts chunks that LOOKED like a
    name slot (2+ tokens) but failed validation. Single-token chunks (a
    dangling 'Esq.' after a split, an empty tail) are dropped silently."""
    names: list[str] = []
    ambiguous = 0
    for chunk in re.split(r",|;|\band\b|&", raw):
        chunk = " ".join(chunk.split()).strip(" .")
        if not chunk:
            continue
        tokens = chunk.split()
        if len(tokens) < 2:
            continue
        if looks_like_person_name(chunk):
            names.append(chunk)
        else:
            ambiguous += 1
    return names, ambiguous


# ── Per-page parsing ─────────────────────────────────────────────────────────


def parse_bank_contacts(
    pages: Sequence[dict],
    *,
    source_url: str,
) -> tuple[list[ExtractedBankContact], int]:
    """Parse worker pages (``[{"page": 1, "text": "..."}]``) into contacts.

    Pure and deterministic — the unit-testable core. Returns
    ``(contacts, skipped_ambiguous)``; contacts are deduped within the PDF on
    ``(name, title)`` (first hit wins, page order)."""
    collected: dict[tuple[str, str], ExtractedBankContact] = {}
    ambiguous = 0

    def _add(contact: ExtractedBankContact) -> None:
        key = (contact.name.lower(), (contact.title or "").lower())
        if key not in collected:
            collected[key] = contact

    for page in pages:
        if not isinstance(page, dict):
            continue
        text = str(page.get("text") or "")
        if not text.strip():
            continue
        try:
            page_number = int(page.get("page"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            page_number = None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        flowed = " ".join(text.split())

        ambiguous += _parse_contact_person(lines, flowed, page_number, source_url, _add)
        ambiguous += _parse_organizers(lines, flowed, page_number, source_url, _add)
        ambiguous += _parse_proposed_officers(flowed, page_number, source_url, _add)
        ambiguous += _parse_counsel(lines, flowed, page_number, source_url, _add)

    return list(collected.values()), ambiguous


def _block_snippet(lines: list[str], index: int, span: int = 2) -> str:
    return " ".join(" ".join(lines[index : index + 1 + span]).split())[:240]


def _parse_contact_person(
    lines: list[str], flowed: str, page_number: int | None, source_url: str, add
) -> int:
    ambiguous = 0
    for i, line in enumerate(lines):
        match = _CONTACT_LABEL_RE.match(line) or _PERSON_TO_CONTACT_RE.match(line)
        if match is None:
            continue
        rest = (match.group("rest") or "").strip()
        # A label needs an explicit ':' or must be a bare block heading —
        # prose that merely starts with "Contact ..." is not a label.
        if not match.group("sep") and rest:
            continue

        # Candidate lines: the label's own remainder, then the next few
        # lines of the block (skipping bare sub-labels, reading through
        # 'Name: Jane Doe' ones).
        window = [rest] + lines[i + 1 : i + 5]
        name = title = None
        email = phone = None
        for candidate in window:
            candidate = candidate.strip()
            if not candidate:
                continue
            sublabel = _BLOCK_SUBLABEL_RE.match(candidate)
            if sublabel is not None:
                candidate = (sublabel.group("rest") or "").strip()
                if not candidate:
                    continue
            remainder, found_email, found_phone = _strip_channels(candidate)
            email = email or found_email
            phone = phone or found_phone
            if name is None and remainder:
                parsed_name, parsed_title = _split_name_title(remainder)
                if parsed_name:
                    name, title = parsed_name, parsed_title
        if name is None:
            ambiguous += 1
            logger.info(
                "bank_contacts: contact-person block on page %s had no "
                "unambiguous person name; skipping (never guess). line=%r",
                page_number, line[:120],
            )
            continue
        add(
            ExtractedBankContact(
                name=name,
                title=title,
                role_context=ROLE_CONTACT_PERSON,
                email=email,
                phone=phone,
                source_url=source_url,
                page_number=page_number,
                context_snippet=_block_snippet(lines, i, span=3),
            )
        )
        break  # one contact block per page

    # Narrative form: "please contact Jane Doe at (202) 555-0100".
    for match in _INLINE_CONTACT_RE.finditer(flowed):
        after = flowed[match.end() : match.end() + 90]
        name_match = _INLINE_CONTACT_NAME_RE.match(after)
        name = _refine_person_name(name_match.group("name")) if name_match else None
        if name is None:
            continue  # narrative 'contact' with no clean name — not a hit at all
        tail = flowed[match.end() : match.end() + 160]
        _, email, phone = _strip_channels(tail)
        add(
            ExtractedBankContact(
                name=name,
                title=None,
                role_context=ROLE_CONTACT_PERSON,
                email=email,
                phone=phone,
                source_url=source_url,
                page_number=page_number,
                context_snippet=_snippet(flowed, match.start(), match.end() + 60),
            )
        )
    return ambiguous


_BULLET_RE = re.compile(r"^(?:[•‣▪●\-–—*]|\(?\d{1,2}[.)])\s*")


def _parse_organizers(
    lines: list[str], flowed: str, page_number: int | None, source_url: str, add
) -> int:
    ambiguous = 0

    def _emit(name: str, title: str | None, snippet: str) -> None:
        add(
            ExtractedBankContact(
                name=name,
                title=title,
                role_context=ROLE_ORGANIZER,
                email=None,
                phone=None,
                source_url=source_url,
                page_number=page_number,
                context_snippet=snippet,
            )
        )

    for i, line in enumerate(lines):
        match = _ORGANIZERS_HEADING_RE.match(line)
        if match is None:
            continue
        rest = (match.group("rest") or "").strip()
        if not match.group("sep") and rest:
            continue  # prose ("The organizers have filed...") — not a heading
        found = 0
        if rest:
            names, chunk_ambiguous = _split_person_list(rest)
            ambiguous += chunk_ambiguous
            for name in names:
                _emit(name, None, _block_snippet(lines, i))
                found += 1
        else:
            # Consume consecutive name lines under the heading; stop at the
            # first line that is not a person name.
            for following in lines[i + 1 : i + 16]:
                candidate = _BULLET_RE.sub("", following).strip()
                parsed_name, parsed_title = _split_name_title(candidate)
                if parsed_name is None:
                    break
                _emit(parsed_name, parsed_title, " ".join((line + " " + candidate).split())[:240])
                found += 1
        if found == 0:
            ambiguous += 1
            logger.info(
                "bank_contacts: organizers heading on page %s yielded no "
                "unambiguous names; skipping. line=%r",
                page_number, line[:120],
            )

    # Narrative form: "the organizers are John Smith, Jane Doe and ...".
    for match in _ORGANIZERS_INLINE_RE.finditer(flowed):
        tail = flowed[match.end() : match.end() + 300]
        list_text = _cut_sentence(tail).split(";")[0]
        names, chunk_ambiguous = _split_person_list(list_text)
        ambiguous += chunk_ambiguous
        for name in names:
            _emit(name, None, _snippet(flowed, match.start(), match.end() + len(list_text)))
    return ambiguous


def _parse_proposed_officers(
    flowed: str, page_number: int | None, source_url: str, add
) -> int:
    ambiguous = 0
    for match in _PROPOSED_RE.finditer(flowed):
        title = _clean_title(match.group("title"))
        after = flowed[match.end() : match.end() + 120]
        before = flowed[max(0, match.start() - 80) : match.start()]
        snippet = _snippet(flowed, match.start(), match.end())

        # 1) "proposed directors are Jane Doe, John Smith and ..." — the list
        #    form is tried FIRST so a multi-name sentence never collapses to
        #    just its first name via the single-name pattern.
        list_match = _NAMES_LIST_AFTER_RE.match(after)
        if list_match is not None:
            names, chunk_ambiguous = _split_person_list(
                _cut_sentence(list_match.group("names"))
            )
            ambiguous += chunk_ambiguous
            if names:
                for name in names:
                    add(
                        ExtractedBankContact(
                            name=name,
                            title=title,
                            role_context=ROLE_PROPOSED_OFFICER,
                            email=None,
                            phone=None,
                            source_url=source_url,
                            page_number=page_number,
                            context_snippet=snippet,
                        )
                    )
                continue

        # 2) "proposed President and CEO is Jane Doe" / "..., Jane Doe".
        name_match = _NAME_AFTER_RE.match(after)
        after_name = _refine_person_name(name_match.group("name")) if name_match else None
        if after_name is not None:
            add(
                ExtractedBankContact(
                    name=after_name,
                    title=title,
                    role_context=ROLE_PROPOSED_OFFICER,
                    email=None,
                    phone=None,
                    source_url=source_url,
                    page_number=page_number,
                    context_snippet=snippet,
                )
            )
            continue

        # 3) "Jane Doe, the proposed President and CEO".
        before_match = _NAME_BEFORE_RE.search(before)
        before_name = (
            _refine_person_name(before_match.group("name"), trim="head")
            if before_match
            else None
        )
        if before_name is not None:
            add(
                ExtractedBankContact(
                    name=before_name,
                    title=title,
                    role_context=ROLE_PROPOSED_OFFICER,
                    email=None,
                    phone=None,
                    source_url=source_url,
                    page_number=page_number,
                    context_snippet=snippet,
                )
            )
            continue

        ambiguous += 1
        logger.info(
            "bank_contacts: 'proposed %s' on page %s had no unambiguous "
            "adjacent person name; skipping (never guess).",
            (title or match.group("title"))[:60], page_number,
        )
    return ambiguous


def _parse_counsel(
    lines: list[str], flowed: str, page_number: int | None, source_url: str, add
) -> int:
    ambiguous = 0
    for i, line in enumerate(lines):
        match = _COUNSEL_LABEL_RE.match(line)
        if match is None:
            continue
        remainder, email, phone = _strip_channels(match.group("rest"))
        name, title = _split_name_title(remainder)
        if name is None:
            # Frequently a law FIRM ("Sullivan & Cromwell LLP") — a real hit
            # we deliberately refuse (people only, never guess).
            ambiguous += 1
            logger.info(
                "bank_contacts: counsel label on page %s did not resolve to a "
                "person (likely a firm); skipping. line=%r",
                page_number, line[:120],
            )
            continue
        add(
            ExtractedBankContact(
                name=name,
                # Verbatim only — role_context already says 'counsel'; a
                # synthesized title would be a guess.
                title=title,
                role_context=ROLE_COUNSEL,
                email=email,
                phone=phone,
                source_url=source_url,
                page_number=page_number,
                context_snippet=_block_snippet(lines, i),
            )
        )

    # Inline form: "Jane Doe, counsel to the organizers".
    for match in _COUNSEL_INLINE_RE.finditer(flowed):
        if flowed[match.end() : match.end() + 2].lstrip().startswith(":"):
            # "Counsel for the applicant: ..." — the LABELED form, already
            # handled (or deliberately refused) by the label pass above;
            # counting it here would double-report one mention as ambiguous.
            continue
        before = flowed[max(0, match.start() - 70) : match.start()]
        name_match = _NAME_BEFORE_RE.search(before)
        name = (
            _refine_person_name(name_match.group("name"), trim="head")
            if name_match
            else None
        )
        if name is None:
            ambiguous += 1
            logger.info(
                "bank_contacts: inline counsel-of-record on page %s had no "
                "unambiguous preceding person name; skipping.", page_number,
            )
            continue
        add(
            ExtractedBankContact(
                name=name,
                title=None,
                role_context=ROLE_COUNSEL,
                email=None,
                phone=None,
                source_url=source_url,
                page_number=page_number,
                context_snippet=_snippet(flowed, match.start(), match.end()),
            )
        )
    return ambiguous


# ── Crash-isolated text extraction (parent side) ─────────────────────────────


def extract_pdf_text_pages(pdf_path: str | Path) -> list[dict]:
    """Extract per-page text via the isolated subprocess worker.

    Synchronous/blocking by design — callers invoke it via
    ``asyncio.to_thread``. Any failure (child killed by signal, timeout,
    unreadable output) is contained and returns ``[]`` (a parse-miss), so a
    corrupt PDF can never take down the watcher run — the exact contract of
    ``focus_ceo_extraction._render_pdf_pages_to_images``.
    """
    out_path: Path | None = None
    try:
        out_fd, out_raw = tempfile.mkstemp(suffix=".json", prefix="bank_contact_text_")
        os.close(out_fd)
        out_path = Path(out_raw)

        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(_TEXT_WORKER),
                    str(pdf_path),
                    str(out_path),
                    str(_TEXT_MAX_PAGES),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_TEXT_SUBPROCESS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "bank_contacts: PDF text subprocess timed out after %ds; "
                "treating as parse-miss.", _TEXT_SUBPROCESS_TIMEOUT_SECONDS,
            )
            return []
        except OSError as exc:
            logger.warning("bank_contacts: PDF text subprocess could not launch: %s", exc)
            return []

        if proc.returncode != 0:
            logger.warning(
                "bank_contacts: PDF text subprocess exited abnormally "
                "(returncode=%s); treating as parse-miss (corrupt PDF "
                "contained to the child).", proc.returncode,
            )
            return []

        try:
            payload = json.loads(out_path.read_text(encoding="utf-8") or "[]")
        except (OSError, ValueError) as exc:
            logger.warning("bank_contacts: PDF text subprocess produced no readable output: %s", exc)
            return []
        return payload if isinstance(payload, list) else []
    except Exception as exc:  # the isolation layer itself must never raise
        logger.warning("bank_contacts: PDF text extraction failed: %s", exc)
        return []
    finally:
        if out_path is not None:
            try:
                out_path.unlink()
            except OSError:
                pass


# ── Service ──────────────────────────────────────────────────────────────────


class BankContactExtractionService:
    """Download → extract → parse → (optionally) upsert, one bank at a time.

    ``collect_contacts`` does all the slow network/subprocess work with NO
    database session open (the focus-batch architecture); ``upsert_contacts``
    is the short write. The watcher composes them per bank.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = _DOWNLOAD_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport  # tests inject httpx.MockTransport

    async def collect_contacts(
        self,
        *,
        bank_id: int,
        bank_name: str,
        pdf_entries: Sequence[object],
    ) -> tuple[list[ExtractedBankContact], BankContactExtractionStats]:
        """Fetch + parse every allowlisted PDF for one bank. No DB touched."""
        stats = BankContactExtractionStats()
        collected: dict[tuple[str, str], ExtractedBankContact] = {}

        with pdf_tempdir(prefix="bank_contacts_") as tmp_dir:
            for entry in pdf_entries or []:
                url = entry.get("url") if isinstance(entry, dict) else None
                if not url:
                    continue
                pdf_path = await self.download_pdf(str(url), tmp_dir)
                if pdf_path is None:
                    continue
                stats.pdfs_fetched += 1
                pages = await asyncio.to_thread(extract_pdf_text_pages, pdf_path)
                if not pages:
                    logger.info(
                        "bank_contacts: no extractable text in %s (bank id=%d %r) "
                        "— scanned image or corrupt PDF; skipping.",
                        url, bank_id, bank_name,
                    )
                    continue
                contacts, ambiguous = parse_bank_contacts(pages, source_url=str(url))
                stats.skipped_ambiguous += ambiguous
                for contact in contacts:
                    key = (contact.name.lower(), (contact.title or "").lower())
                    if key not in collected:
                        collected[key] = contact

        contacts = list(collected.values())
        stats.contacts_extracted = len(contacts)
        return contacts, stats

    async def download_pdf(self, url: str, dest_dir: Path) -> Path | None:
        """Fetch one public-portion PDF with the full guard stack.

        Allowlist is re-verified BEFORE the request (a vandalized DB row must
        not turn this into a generic fetcher) and AFTER redirects (occ.gov
        must not bounce us off-domain). Size is capped via the
        Content-Length header AND by counting streamed bytes. Any failure
        logs and returns None — one bad link never aborts the bank.
        """
        if not is_allowed_application_pdf_url(url):
            logger.warning(
                "bank_contacts: refusing non-allowlisted PDF URL %r "
                "(https occ.gov only).", url,
            )
            return None
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "application/pdf",
            # Identity + aiter_raw: keep PDF bytes verbatim past any gateway
            # that mislabels compressed bodies — same fix as edgar.py /
            # finra_pdf_service.py / pdf_downloader.py.
            "Accept-Encoding": "identity",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, transport=self._transport
            ) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code != 200:
                        logger.warning(
                            "bank_contacts: HTTP %d fetching %s; skipping.",
                            response.status_code, url,
                        )
                        return None
                    final_url = str(response.url)
                    if not is_allowed_application_pdf_url(final_url):
                        logger.warning(
                            "bank_contacts: redirect escaped the occ.gov "
                            "allowlist (%s -> %s); skipping.", url, final_url,
                        )
                        return None
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_PDF_BYTES:
                        logger.warning(
                            "bank_contacts: %s declares %s bytes (> %d cap); skipping.",
                            url, declared, MAX_PDF_BYTES,
                        )
                        return None
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_raw():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            logger.warning(
                                "bank_contacts: %s exceeded the %d-byte cap "
                                "mid-stream; skipping.", url, MAX_PDF_BYTES,
                            )
                            return None
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            logger.warning("bank_contacts: fetch failed for %s: %s", url, exc)
            return None

        body = b"".join(chunks)
        if not body.lstrip()[:5].startswith(b"%PDF"):
            logger.warning(
                "bank_contacts: %s did not return a PDF (magic bytes missing) "
                "— likely an HTML error page; skipping.", url,
            )
            return None

        name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".pdf"
        pdf_path = dest_dir / name
        try:
            pdf_path.write_bytes(body)
        except OSError as exc:
            logger.warning("bank_contacts: could not write %s: %s", pdf_path, exc)
            return None
        return pdf_path

    async def upsert_contacts(
        self,
        db: AsyncSession,
        bank_id: int,
        contacts: Sequence[ExtractedBankContact],
    ) -> tuple[int, int]:
        """Idempotent upsert on ``(bank_id, name, coalesce(title,''), source)``.

        Returns ``(inserted, updated)``. Existing rows update additively:
        non-NULL incoming email/phone overwrite, missing ones preserve the
        prior value; the receipt (page/snippet/source_url) refreshes; the
        first-written ``role_context`` sticks. Insert races against a
        parallel run are contained to a SAVEPOINT and fall through to the
        update path — same pattern as
        ``focus_ceo_extraction._persist_net_capital``.
        """
        inserted = updated = 0
        for contact in contacts:
            title_key = contact.title or ""

            def _apply_update(row: BankContact, contact: ExtractedBankContact = contact) -> None:
                if contact.email is not None:
                    row.email = contact.email
                if contact.phone is not None:
                    row.phone = contact.phone
                if contact.page_number is not None:
                    row.page_number = contact.page_number
                if contact.context_snippet:
                    row.context_snippet = contact.context_snippet
                if contact.source_url:
                    row.source_url = contact.source_url

            existing = (
                await db.execute(
                    select(BankContact).where(
                        BankContact.bank_id == bank_id,
                        BankContact.name == contact.name,
                        func.coalesce(BankContact.title, "") == title_key,
                        BankContact.source == contact.source,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                _apply_update(existing)
                updated += 1
                continue

            try:
                async with db.begin_nested():
                    db.add(
                        BankContact(
                            bank_id=bank_id,
                            name=contact.name[:255],
                            title=contact.title[:255] if contact.title else None,
                            role_context=contact.role_context[:32],
                            email=contact.email[:320] if contact.email else None,
                            phone=contact.phone[:64] if contact.phone else None,
                            source=contact.source[:64],
                            source_url=contact.source_url,
                            page_number=contact.page_number,
                            context_snippet=contact.context_snippet,
                        )
                    )
                    await db.flush()
                inserted += 1
            except IntegrityError:
                winner = (
                    await db.execute(
                        select(BankContact).where(
                            BankContact.bank_id == bank_id,
                            BankContact.name == contact.name,
                            func.coalesce(BankContact.title, "") == title_key,
                            BankContact.source == contact.source,
                        )
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                _apply_update(winner)
                updated += 1
        await db.flush()
        return inserted, updated
