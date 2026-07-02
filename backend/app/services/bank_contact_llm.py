"""Gemini recall pass for bank-contact extraction — grounded, never fabricating.

Why this module exists. The regex extractor in
:mod:`app.services.bank_contact_extraction` is deliberately conservative: a
person is stored only when the surrounding pattern is unambiguous AND the
candidate passes the strict 2-4-title-cased-words shape gate. That precision
costs recall — on the first production sweep the regex pass stored 40 people
while refusing ~290 ambiguous candidates, many of which are real people the
patterns simply can't anchor (ALL-CAPS names, 5+-word names, lowercase
particles, table layouts). This module sends the SAME per-page text (already
pulled by the crash-isolated ``_pdf_text_worker`` subprocess) to Gemini via
the repo's structured-output client (``gemini_responses``) and unions the
results with the regex pass.

THE GROUNDING RULE (the core safety property — never-fabricate):

A Gemini-returned person is kept ONLY when their ``name`` appears verbatim in
the text of one of the pages the model was shown: the name's
whitespace-split tokens, each regex-escaped, joined by ``\\s+`` and guarded
by word boundaries, must match **case-insensitively** against a page's raw
text (claimed page first, then the chunk's other pages). ``email`` / ``phone``
/ ``title`` values pass the same check (channels join with ``\\s*`` since PDF
extraction jams or splits them); an ungrounded value is NULLED, never stored.
The stored ``page_number`` and ``context_snippet`` come from where the name
ACTUALLY matched — the receipt is cut from the source text, not from model
output. Anything the gate refuses (ungrounded name, org-vocabulary name,
unmappable role) is logged with its reason and counted as
``llm_dropped_ungrounded``; a dropped hallucination can never reach the DB.

Prompt policy: people only, values verbatim as printed — no inference,
normalization, expansion, or correction; omit when unsure. Role ``other`` is
dropped unless the (grounded) title clearly maps to one of the four
role_contexts the vertical knows.

Failure contract: :func:`extract_contacts_via_llm` raises
``GeminiConfigurationError`` (missing key — the caller disables the pass for
the run); a per-chunk provider error or response-shape surprise degrades to
that chunk only. The caller (``BankContactExtractionService``) wraps
everything else so the watcher phase can never fail because Gemini is down.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Sequence

from pydantic import ValidationError

from app.services.bank_contact_extraction import (
    _BLOCKED_NAME_WORDS,
    _snippet,
    LLM_SOURCE,
    ROLE_CONTACT_PERSON,
    ROLE_COUNSEL,
    ROLE_ORGANIZER,
    ROLE_PROPOSED_OFFICER,
    ExtractedBankContact,
)
from app.services.gemini_responses import (
    GeminiBankContactPerson,
    GeminiConfigurationError,
    GeminiExtractionError,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Parse an int env override, degrading to ``default`` on anything bad.

    L1 never-fail contract. These ceilings are parsed at IMPORT time, and this
    module is imported lazily by ``bank_contact_extraction.collect_contacts``
    (via ``_collect_llm_contacts``). A malformed override — ``FOO=abc`` or an
    empty string — must therefore fall back here instead of raising
    ``ValueError`` at import and taking the whole contact-collection phase down
    with it. A missing var is silent (the default is expected); a *present but
    unparseable* one is warned so ops can spot the typo.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "bank_contacts[llm]: ignoring malformed %s=%r; using default %d.",
            name, raw, default,
        )
        return default


_ALLOWED_ROLES = frozenset(
    {ROLE_CONTACT_PERSON, ROLE_ORGANIZER, ROLE_PROPOSED_OFFICER, ROLE_COUNSEL}
)

# Pages are grouped into prompts of roughly this many characters (~5k tokens)
# — small enough that the model attends to every page, large enough that a
# typical public portion (a few dozen sparse pages) fits in 1-3 calls. Pages
# are NEVER split mid-page: grounding and page attribution are per page.
_CHUNK_CHAR_BUDGET = _env_int("BANK_CONTACT_LLM_CHUNK_CHARS", 20_000)

# ── Gemini spend ceilings (M1) ───────────────────────────────────────────────
# Two hard caps bound the paid cost of one PDF's recall pass so a pathological
# filing can't become a cost-DoS. Both are Gemini-ONLY — the regex pass keeps
# seeing each page's full text:
#   * each page's text is truncated to _MAX_PAGE_CHARS before it enters a chunk,
#     so one enormous page can't become a single near-context-limit prompt (the
#     people this pass wants sit in the application narrative up front);
#   * at most _MAX_CHUNKS_PER_PDF chunks are ever sent — beyond that, later
#     pages degrade to regex-only coverage instead of ~40 paid calls/PDF.
_MAX_PAGE_CHARS = _env_int("BANK_CONTACT_LLM_MAX_PAGE_CHARS", 20_000)
_MAX_CHUNKS_PER_PDF = _env_int("BANK_CONTACT_LLM_MAX_CHUNKS", 8)

# ── Same-page channel proximity (M2) ─────────────────────────────────────────
# An email/phone/title is grounded only within this many characters of the
# person's NAME match, on the SAME page. This stops the model from attaching
# one person's channel (or an attacker-planted "attacker@evil.example" printed
# anywhere in the filing) to a different person's name. Wide enough to span a
# normal "Name / Title / Telephone / Email" contact stanza; far tighter than a
# whole page, and page boundaries are never crossed.
_CHANNEL_PROXIMITY_CHARS = _env_int("BANK_CONTACT_LLM_CHANNEL_PROXIMITY_CHARS", 500)

# A "name" longer than this is a copied sentence, not a person — even if it
# grounds verbatim. (The regex gate caps at 64; the LLM gate is looser on
# purpose to admit long non-Western names, but not unboundedly so.)
_MAX_NAME_CHARS = 120
_MAX_NAME_TOKENS = 6

_PROMPT_HEADER = """\
You are extracting PEOPLE from the text of the public portion of a bank \
charter application filed with the OCC (Office of the Comptroller of the \
Currency). The text below is split into pages by "--- PAGE N ---" markers.

Rules — follow them exactly:
1. Return ONLY individual human beings who are explicitly named in the text. \
Companies, law firms, banks, trusts, government agencies (OCC, FDIC), form \
labels, and section headings are NOT people.
2. Copy every value VERBATIM, exactly as printed in the text — character for \
character, including capitalization and punctuation. NEVER invent, infer, \
normalize, expand, abbreviate, or correct a name, title, email address, or \
phone number.
3. If a title, email, or phone number is not printed in the text for that \
person, return null for that field. Do not borrow a value from a different \
person or page.
4. If you are not sure a candidate is a real person, omit them entirely. An \
empty "people" array is a correct answer for pages that name no one.
5. page_number is the N from the "--- PAGE N ---" marker of the page where \
the person's name is printed.

Roles (pick the best match for WHY the person appears):
- contact_person: the filing's contact block ("Contact person:", "Person to \
contact", "please contact ... at ...").
- organizer: listed as an organizer / member of the organizing group.
- proposed_officer: named as a proposed officer or director ("proposed \
President", "proposed Chief Risk Officer", "the proposed directors are ...").
- counsel: counsel or attorney for the applicant / organizers.
- other: clearly a named person, but none of the roles above fit.

Return strict JSON matching the schema: an object with a "people" array of \
{name, title, role, email, phone, page_number}."""


# ── Chunking ─────────────────────────────────────────────────────────────────


def _chunk_pages(pages: Sequence[object]) -> list[list[tuple[int, str]]]:
    """Group worker pages into prompt-sized chunks of ``(page_number, text)``.

    Splits only on page boundaries; a single page larger than the budget gets
    a chunk of its own. Pages without a usable number or without text are
    skipped (the regex pass still covers them — it tolerates ``page=None``).

    M1 spend ceilings apply here (Gemini-only): each page's text is truncated
    to ``_MAX_PAGE_CHARS`` before it enters a chunk, and at most
    ``_MAX_CHUNKS_PER_PDF`` chunks are returned.
    """
    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    size = 0
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        text = str(page.get("text") or "")
        if not text.strip():
            continue
        try:
            number = int(page.get("page"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if len(text) > _MAX_PAGE_CHARS:
            # Bound spend: an oversized page would otherwise become one
            # near-context-limit prompt. Truncate for Gemini only.
            text = text[:_MAX_PAGE_CHARS]
        if current and size + len(text) > _CHUNK_CHAR_BUDGET:
            chunks.append(current)
            current, size = [], 0
        current.append((number, text))
        size += len(text)
    if current:
        chunks.append(current)
    if len(chunks) > _MAX_CHUNKS_PER_PDF:
        logger.warning(
            "bank_contacts[llm]: capping %d chunks to %d for this PDF "
            "(bounding Gemini spend); later pages get regex-only coverage.",
            len(chunks), _MAX_CHUNKS_PER_PDF,
        )
        chunks = chunks[:_MAX_CHUNKS_PER_PDF]
    return chunks


def _build_prompt(chunk: Sequence[tuple[int, str]]) -> str:
    blocks = [f"--- PAGE {number} ---\n{text}" for number, text in chunk]
    return _PROMPT_HEADER + "\n\n" + "\n\n".join(blocks)


# ── Verbatim grounding ───────────────────────────────────────────────────────


def _verbatim_pattern(value: str, *, joiner: str) -> re.Pattern[str] | None:
    """Compile the grounding matcher for ``value``.

    Tokens (whitespace-split) are regex-escaped and joined by ``joiner``
    (``\\s+`` for names/titles — words stay separated; ``\\s*`` for
    emails/phones — PDF extraction jams or splits them). Word-boundary
    guards on alphanumeric ends stop "Jane A. Doe" from grounding inside
    "Sarajane A. Doeman". Matching is case-insensitive; together with the
    whitespace-flexible joins this is exactly "appears verbatim in the
    source text (case-insensitive, whitespace-flexible)".
    """
    tokens = value.split()
    if not tokens:
        return None
    body = joiner.join(re.escape(token) for token in tokens)
    prefix = r"\b" if value[0].isalnum() else ""
    suffix = r"\b" if value[-1].isalnum() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def _ground_on_pages(
    value: str,
    chunk: Sequence[tuple[int, str]],
    preferred_page: int | None,
    *,
    joiner: str,
) -> tuple[int, tuple[int, int], str] | None:
    """Locate ``value`` verbatim on one of the chunk's pages.

    Returns ``(page_number, (start, end), page_text)`` for the page where the
    value ACTUALLY appears — the model's claimed page is only a search hint
    (tried first), so an off-by-one page claim self-corrects instead of either
    dropping a real person or storing a wrong receipt. ``None`` = ungrounded.
    """
    pattern = _verbatim_pattern(value, joiner=joiner)
    if pattern is None:
        return None
    ordered = sorted(chunk, key=lambda page: page[0] != preferred_page)
    for number, text in ordered:
        match = pattern.search(text)
        if match is not None:
            return number, (match.start(), match.end()), text
    return None


def _grounded_or_none(
    value: str | None,
    chunk: Sequence[tuple[int, str]],
    preferred_page: int | None,
    *,
    joiner: str,
    field: str,
    name: str,
) -> str | None:
    """Return the cleaned value when it grounds; otherwise NULL it (logged).

    A fabricated email/phone/title on an otherwise-grounded person must never
    reach the DB — the person survives, the ungrounded value does not.
    """
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return None
    if _ground_on_pages(cleaned, chunk, preferred_page, joiner=joiner) is not None:
        return cleaned
    # L3: never log the value itself — an ungrounded channel is untrusted
    # (fabricated, third-party PII, or attacker-planted). Field + length only.
    logger.info(
        "bank_contacts[llm]: nulling ungrounded %s (len=%d) for %r "
        "(not printed on the source pages; never fabricate).",
        field, len(cleaned), name,
    )
    return None


# ── Person gate (shape sanity + role mapping) ────────────────────────────────


def _is_plausible_person_name(name: str) -> bool:
    """Loose shape sanity for LLM-returned names.

    Deliberately looser than ``looks_like_person_name`` (that strictness is
    WHY the regex pass under-recalls — ALL-CAPS and 5-6-word names must pass
    here), but a hard NO on digits/'@', on absurd lengths, and on the shared
    org-vocabulary blocklist: "Privacy Technology" is refused even when it is
    printed verbatim on the page — grounded text can still be a non-person.
    """
    tokens = name.split()
    if not 2 <= len(tokens) <= _MAX_NAME_TOKENS:
        return False
    if len(name) > _MAX_NAME_CHARS:
        return False
    if any(ch.isdigit() for ch in name) or "@" in name:
        return False
    for token in tokens:
        if token.strip(".,;:&()'’\"").lower() in _BLOCKED_NAME_WORDS:
            return False
    return True


def _map_role(role: str, grounded_title: str | None) -> str | None:
    """Map the model's role onto our role_context vocabulary.

    One of ours → kept as-is. ``other`` (or anything off-enum) is salvaged
    only when the GROUNDED title — verbatim source text, so this is not a
    guess — clearly names one of our four roles; otherwise ``None`` = drop.
    """
    normalized = (role or "").strip().lower()
    if normalized in _ALLOWED_ROLES:
        return normalized
    title = (grounded_title or "").lower()
    if "counsel" in title or "attorney" in title:
        return ROLE_COUNSEL
    if "organiz" in title:  # organizer / organizing group
        return ROLE_ORGANIZER
    if "proposed" in title:
        return ROLE_PROPOSED_OFFICER
    return None


def _gate(
    person: GeminiBankContactPerson,
    chunk: Sequence[tuple[int, str]],
    source_url: str,
) -> tuple[ExtractedBankContact | None, str]:
    """Apply the full grounding/validation gate to one returned person.

    Returns ``(contact, "")`` on keep, ``(None, reason)`` on drop.
    """
    name = " ".join((person.name or "").split())
    if not name:
        return None, "empty name"
    if not _is_plausible_person_name(name):
        return None, "not a plausible person name (org/form vocabulary or shape)"

    ground = _ground_on_pages(name, chunk, person.page_number, joiner=r"\s+")
    if ground is None:
        return None, "name not printed verbatim on the source pages"
    page_number, (start, end), page_text = ground

    # M2: channels must ground on the SAME page as the name AND within a
    # proximity window of the name match. Passing only this windowed slice into
    # the grounding means an email/phone/title printed on a DIFFERENT page — or
    # far from this name on the same page (another person's, or an
    # attacker-planted value) — is treated as ungrounded and NULLed. The window
    # still spans a normal contact stanza, so single-page recall is preserved.
    lo = max(0, start - _CHANNEL_PROXIMITY_CHARS)
    hi = min(len(page_text), end + _CHANNEL_PROXIMITY_CHARS)
    channel_scope: list[tuple[int, str]] = [(page_number, page_text[lo:hi])]

    # Title grounds BEFORE role mapping so an 'other' role can only be
    # salvaged from verbatim source text, never from a fabricated title.
    title = _grounded_or_none(
        person.title, channel_scope, page_number, joiner=r"\s+", field="title", name=name
    )
    role = _map_role(person.role, title)
    if role is None:
        return None, f"role {person.role!r} does not map to a known role_context"

    email = _grounded_or_none(
        person.email, channel_scope, page_number, joiner=r"\s*", field="email", name=name
    )
    phone = _grounded_or_none(
        person.phone, channel_scope, page_number, joiner=r"\s*", field="phone", name=name
    )

    return (
        ExtractedBankContact(
            name=name[:255],
            title=title[:255] if title else None,
            role_context=role,
            email=email,
            phone=phone,
            source_url=source_url,
            page_number=page_number,
            # Receipt cut from the SOURCE page around the actual name match —
            # same audit-trail contract as the regex extractor.
            context_snippet=_snippet(page_text, start, end),
            source=LLM_SOURCE,
        ),
        "",
    )


# ── Entry point ──────────────────────────────────────────────────────────────


async def extract_contacts_via_llm(
    pages: Sequence[object],
    *,
    source_url: str,
    client: object,
) -> tuple[list[ExtractedBankContact], int]:
    """Extract grounded people from one PDF's worker pages via Gemini.

    ``client`` is duck-typed (needs ``await client.extract_bank_contacts(
    prompt=...)`` returning a ``GeminiBankContactExtraction``) so tests
    inject a fake and production passes the real ``GeminiResponsesClient``.

    Returns ``(kept_contacts, dropped_ungrounded)`` — kept contacts are
    deduped on ``(name, title)`` across chunks and carry
    ``source='application_pdf_llm'``; the dropped count is every returned
    record the gate refused (each drop logs its reason).

    Raises ``GeminiConfigurationError`` (missing key) so the caller can
    disable the pass for the run; a per-chunk ``GeminiExtractionError`` or
    ``ValidationError`` (schema surprise) degrades to that chunk only.
    """
    kept: dict[tuple[str, str], ExtractedBankContact] = {}
    returned = dropped = 0

    for chunk in _chunk_pages(pages):
        prompt = _build_prompt(chunk)
        try:
            extraction = await client.extract_bank_contacts(prompt=prompt)  # type: ignore[attr-defined]
        except GeminiConfigurationError:
            raise  # caller disables the pass for the whole run
        except (GeminiExtractionError, ValidationError) as exc:
            logger.warning(
                "bank_contacts[llm]: chunk (pages %s-%s) failed for %s: %s — "
                "regex-only coverage for these pages.",
                chunk[0][0], chunk[-1][0], source_url, exc,
            )
            continue

        returned += len(extraction.people)
        for person in extraction.people:
            contact, reason = _gate(person, chunk, source_url)
            if contact is None:
                dropped += 1
                logger.info(
                    "bank_contacts[llm]: dropped %r (%s) page=%s url=%s "
                    "(never fabricate).",
                    (person.name or "")[:80], reason, person.page_number,
                    source_url,
                )
                continue
            key = (contact.name.lower(), (contact.title or "").lower())
            kept.setdefault(key, contact)

    contacts = list(kept.values())
    logger.info(
        "bank_contacts[llm]: %s -> returned=%d kept=%d dropped_ungrounded=%d",
        source_url, returned, len(contacts), dropped,
    )
    return contacts, dropped
