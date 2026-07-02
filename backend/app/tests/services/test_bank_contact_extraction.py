"""Bank-contact extraction (``services/bank_contact_extraction.py``).

Covers the four contracts the feature must keep:

1. **Conservative parsing** — the pure ``parse_bank_contacts`` core: the
   contact-person block (labeled + FOCUS-style + narrative), organizers
   (heading list + sentence), proposed officers (all three sentence shapes),
   counsel-of-record — and, just as load-bearing, the NEVER-GUESS refusals
   (law firms, ALL-CAPS headers, "TBD", redacted sections) landing in
   ``skipped_ambiguous`` instead of the DB.
2. **Allowlist + fetch guards** — https occ.gov only, verified BEFORE any
   HTTP and AFTER redirects; the 20MB cap (declared and mid-stream); the
   %PDF magic check. All via ``httpx.MockTransport`` — no network.
3. **Crash isolation** — text extraction runs in the ``_pdf_text_worker``
   subprocess; a dying child (deterministic ``_FIS_PDF_TEXT_ABORT`` hook,
   same convention as the render worker) and a corrupt PDF both degrade to
   a parse-miss, never an exception.
4. **Idempotent upserts** — against a real (SQLite) engine: re-running the
   same contacts updates in place, the ``uq_bank_contacts_dedupe``
   expression index rejects raw duplicates, and additive updates never
   NULL-out previously captured channels.

A tiny hand-built (but fully valid) one-page PDF exercises the REAL
pdfplumber path end-to-end through the subprocess.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.bank_contact_extraction as extraction
import app.services.bank_contact_llm as llm_extraction
from app.db.base import Base
from app.models.bank import BankContact
from app.services.bank_contact_extraction import (
    DEFAULT_SOURCE,
    LLM_SOURCE,
    ROLE_CONTACT_PERSON,
    ROLE_COUNSEL,
    ROLE_ORGANIZER,
    ROLE_PROPOSED_OFFICER,
    BankContactExtractionService,
    ExtractedBankContact,
    extract_pdf_text_pages,
    is_allowed_application_pdf_url,
    looks_like_person_name,
    parse_bank_contacts,
)
from app.services.bank_contact_llm import extract_contacts_via_llm
from app.services.gemini_responses import (
    GeminiBankContactExtraction,
    GeminiBankContactPerson,
    GeminiConfigurationError,
    GeminiExtractionError,
)

_PDF_URL = "https://www.occ.gov/topics/digital-assets/apps/erebor-public.pdf"


# ── Minimal-but-valid PDF builder (real pdfplumber food) ────────────────────


def _build_minimal_pdf(lines: list[str]) -> bytes:
    """One-page Helvetica PDF with ``lines`` as its text content. Valid xref
    and offsets — pdfplumber/pdfminer parse it for real."""
    ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({escaped}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


# ── 1. Person-name gate ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "candidate",
    [
        "Jane Doe",
        "John Q. Smith",
        "Mary Beth Johnson-Lee",
        "Robert Roe, Jr.",
        "Dr. Alice M. Founder",
    ],
)
def test_person_gate_accepts_real_names(candidate: str) -> None:
    assert looks_like_person_name(candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        "PUBLIC PORTION",  # ALL-CAPS section header
        "B. ACCOUNTANT IDENTIFICATION",
        "Sullivan & Cromwell LLP",  # law firm
        "Erebor Bank, N.A.",  # the applicant itself
        "Office of the Comptroller",
        "Proposed President",  # a title, not a person
        "TBD",
        "Jane",  # single token
        "Alka Patel +44 203 696",  # digits (phone-in-name)
        "jane.doe@erebor.example",
        # Corporate-noun blocklist (the "Privacy Technology" precision bug):
        # title-cased two-token company fragments must never pass as people.
        "Privacy Technology",
        "Quantum Solutions",
        "Sterling Capital",
        "Apex Services",
        "Meridian Systems",
    ],
)
def test_person_gate_rejects_non_people(candidate: str | None) -> None:
    assert not looks_like_person_name(candidate)


# ── 1. Parser: the unambiguous patterns ─────────────────────────────────────


def _parse(text: str, page: int = 1):
    return parse_bank_contacts([{"page": page, "text": text}], source_url=_PDF_URL)


def test_contact_block_same_line_with_title_phone_email() -> None:
    contacts, ambiguous = _parse(
        "Contact person: Jane A. Doe, Chief Compliance Officer\n"
        "Telephone: (202) 555-0100\n"
        "Email Address: jane.doe@erebor.example\n"
    )
    assert ambiguous == 0
    (contact,) = contacts
    assert contact.role_context == ROLE_CONTACT_PERSON
    assert contact.name == "Jane A. Doe"
    assert contact.title == "Chief Compliance Officer"
    assert contact.phone == "(202) 555-0100"
    assert contact.email == "jane.doe@erebor.example"
    assert contact.page_number == 1
    assert contact.source_url == _PDF_URL
    assert contact.source == "application_pdf"
    assert "Jane A. Doe" in (contact.context_snippet or "")


def test_contact_block_focus_style_name_on_following_sublabel_line() -> None:
    contacts, ambiguous = _parse(
        "PERSON TO CONTACT REGARDING THIS APPLICATION\n"
        "Name: William D. Hawthorne\n"
        "Telephone: 212-555-4444\n"
    )
    assert ambiguous == 0
    (contact,) = contacts
    assert (contact.name, contact.phone) == ("William D. Hawthorne", "212-555-4444")
    assert contact.role_context == ROLE_CONTACT_PERSON


def test_narrative_please_contact_sentence() -> None:
    contacts, ambiguous = _parse(
        "If you have any questions regarding this filing, please contact "
        "Sam T. Waters at (415) 555-0111 or swaters@example.org.\n"
    )
    assert ambiguous == 0
    (contact,) = contacts
    assert contact.name == "Sam T. Waters"
    assert contact.phone == "(415) 555-0111"
    assert contact.email == "swaters@example.org"


def test_organizers_heading_consumes_name_lines_and_stops_at_prose() -> None:
    contacts, ambiguous = _parse(
        "ORGANIZERS\n"
        "John Q. Smith\n"
        "Mary Beth Johnson-Lee\n"
        "Robert Roe, Jr.\n"
        "The remainder of this section has been redacted.\n",
        page=2,
    )
    assert ambiguous == 0
    assert [c.name for c in contacts] == [
        "John Q. Smith", "Mary Beth Johnson-Lee", "Robert Roe Jr.",
    ]
    assert {c.role_context for c in contacts} == {ROLE_ORGANIZER}
    assert {c.page_number for c in contacts} == {2}


def test_organizers_narrative_sentence_with_middle_initials() -> None:
    contacts, ambiguous = _parse(
        "The organizers of the proposed bank are Henry Adams, Ida B. Wells "
        "and John Jay. Each organizer has completed the required forms.\n"
    )
    assert ambiguous == 0
    assert [c.name for c in contacts] == ["Henry Adams", "Ida B. Wells", "John Jay"]


def test_organizers_list_refuses_privacy_technology_regression() -> None:
    # Regression: the shape heuristic let "Privacy Technology" through as an
    # organizer (2 title-cased words, no digits). The corporate-noun
    # blocklist must refuse it — counted ambiguous, never extracted — while
    # the real person in the same list still lands.
    contacts, ambiguous = _parse(
        "The organizers of the proposed bank are Privacy Technology and John Jay.\n"
    )
    assert [c.name for c in contacts] == ["John Jay"]
    assert ambiguous == 1


def test_proposed_officer_title_then_name_and_name_then_title() -> None:
    contacts, ambiguous = _parse(
        "The proposed President and Chief Executive Officer is Alice M. Founder. "
        "The filing was signed by Brian K. Olsen, the proposed Chief Financial Officer.\n"
    )
    assert ambiguous == 0
    by_name = {c.name: c for c in contacts}
    assert by_name["Alice M. Founder"].title == "President and Chief Executive Officer"
    assert by_name["Brian K. Olsen"].title == "Chief Financial Officer"
    assert {c.role_context for c in contacts} == {ROLE_PROPOSED_OFFICER}


def test_proposed_directors_list_extracts_every_name() -> None:
    contacts, ambiguous = _parse(
        "The proposed directors are Carl Bond, Dana E. Fox and Erin Gale.\n"
    )
    assert ambiguous == 0
    assert sorted(c.name for c in contacts) == ["Carl Bond", "Dana E. Fox", "Erin Gale"]


def test_counsel_label_and_inline_counsel_person() -> None:
    contacts, ambiguous = _parse(
        "Counsel for the applicant: Karen J. Lee, Esq.\n"
        "The public portion was prepared by Mark Stone, counsel to the organizers.\n"
    )
    assert ambiguous == 0
    names = {c.name for c in contacts}
    assert names == {"Karen J. Lee Esq.", "Mark Stone"}
    assert {c.role_context for c in contacts} == {ROLE_COUNSEL}


# ── 1. Parser: ambiguous → logged + counted, never written ─────────────────


@pytest.mark.parametrize(
    ("text", "expected_ambiguous"),
    [
        ("Contact person: TBD\n", 1),
        ("Contact person: (202) 555-0100\n", 1),  # phone but no name
        ("Contact person:\nPUBLIC PORTION\nCONFIDENTIAL\n", 1),  # headers only
        ("ORGANIZERS\nThe remainder of this exhibit is confidential.\n", 1),
        ("The proposed Chief Executive Officer has not yet been identified.\n", 1),
        ("Counsel: Bigg & Firm LLP\n", 1),  # a firm, not a person
        ("Sullivan & Cromwell LLP serves as counsel to the applicant.\n", 1),
    ],
)
def test_ambiguous_patterns_skip_and_count(text: str, expected_ambiguous: int) -> None:
    contacts, ambiguous = _parse(text)
    assert contacts == []
    assert ambiguous == expected_ambiguous


def test_plain_prose_is_not_a_pattern_hit_at_all() -> None:
    # No labels, no organizer/proposed/counsel keywords: nothing extracted
    # AND nothing counted ambiguous (there was never a hit to refuse).
    contacts, ambiguous = _parse(
        "The institution will serve customers throughout the region and "
        "expects steady deposit growth over the first three years.\n"
    )
    assert (contacts, ambiguous) == ([], 0)


def test_same_person_across_patterns_dedupes_within_pdf() -> None:
    contacts, ambiguous = _parse(
        "Contact person: Jane A. Doe\n"
        "Please contact Jane A. Doe at (202) 555-0100 for questions.\n"
    )
    assert ambiguous == 0
    assert [c.name for c in contacts] == ["Jane A. Doe"]
    assert contacts[0].role_context == ROLE_CONTACT_PERSON


# ── 2. Allowlist + fetch guards ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://www.occ.gov/x/app.pdf", True),
        ("https://occ.gov/x/app.pdf", True),
        ("https://apps.occ.gov/x/app.pdf", True),
        ("http://www.occ.gov/x/app.pdf", False),  # plain http
        ("https://evil.example/x/app.pdf", False),
        ("https://occ.gov.evil.example/x/app.pdf", False),  # suffix spoof
        ("https://notocc.gov/x/app.pdf", False),
        (None, False),
        (42, False),
    ],
)
def test_allowlist(url: object, allowed: bool) -> None:
    assert is_allowed_application_pdf_url(url) is allowed


def _service(handler) -> BankContactExtractionService:
    return BankContactExtractionService(transport=httpx.MockTransport(handler))


async def test_download_refuses_disallowed_urls_before_any_http(tmp_path: Path) -> None:
    def _explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request may be made for a disallowed URL")

    service = _service(_explode)
    assert await service.download_pdf("http://www.occ.gov/x.pdf", tmp_path) is None
    assert await service.download_pdf("https://evil.example/x.pdf", tmp_path) is None


async def test_download_happy_path_writes_the_pdf(tmp_path: Path) -> None:
    body = _build_minimal_pdf(["hello"])

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        # stream= (not content=): a Response built with content= is born
        # already-consumed, so the service's aiter_raw() path would raise
        # StreamConsumed instead of exercising the real streaming flow.
        return httpx.Response(200, stream=httpx.ByteStream(body))

    path = await _service(_handler).download_pdf(_PDF_URL, tmp_path)
    assert path is not None and path.read_bytes() == body


async def test_download_refuses_redirect_off_the_allowlist(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("occ.gov"):
            return httpx.Response(302, headers={"location": "https://evil.example/x.pdf"})
        return httpx.Response(200, content=b"%PDF-1.4 stolen")

    assert await _service(_handler).download_pdf(_PDF_URL, tmp_path) is None


async def test_download_refuses_non_pdf_body(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"<html>Not Found</html>"))

    assert await _service(_handler).download_pdf(_PDF_URL, tmp_path) is None


async def test_download_refuses_declared_oversize(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(extraction, "MAX_PDF_BYTES", 16)

    def _handler(request: httpx.Request) -> httpx.Response:
        # MockTransport stamps Content-Length from the body — 17 > 16.
        return httpx.Response(200, content=b"%PDF-1.4 17 bytes")

    assert await _service(_handler).download_pdf(_PDF_URL, tmp_path) is None


async def test_download_refuses_oversize_mid_stream(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(extraction, "MAX_PDF_BYTES", 16)

    class _ChunkedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"%PDF-1.4 "
            yield b"x" * 64

    def _handler(request: httpx.Request) -> httpx.Response:
        # No Content-Length header — forces the counted-bytes branch.
        return httpx.Response(200, stream=_ChunkedBody())

    assert await _service(_handler).download_pdf(_PDF_URL, tmp_path) is None


async def test_download_http_error_status_is_a_skip(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    assert await _service(_handler).download_pdf(_PDF_URL, tmp_path) is None


# ── 3. Crash-isolated text extraction ───────────────────────────────────────


def test_text_worker_extracts_real_pdf_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "app.pdf"
    pdf_path.write_bytes(
        _build_minimal_pdf(
            ["Contact person: Jane A. Doe, President", "Telephone: (202) 555-0100"]
        )
    )
    pages = extract_pdf_text_pages(pdf_path)
    assert pages and pages[0]["page"] == 1
    assert "Jane A. Doe" in pages[0]["text"]


def test_text_worker_death_is_contained_as_parse_miss(tmp_path: Path, monkeypatch) -> None:
    # The deterministic stand-in for a child killed mid-extraction (same
    # convention as the render worker's abort hook).
    monkeypatch.setenv("_FIS_PDF_TEXT_ABORT", "1")
    pdf_path = tmp_path / "app.pdf"
    pdf_path.write_bytes(_build_minimal_pdf(["Contact person: Jane A. Doe"]))
    assert extract_pdf_text_pages(pdf_path) == []


def test_corrupt_pdf_is_a_parse_miss_not_an_exception(tmp_path: Path) -> None:
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 this is not really a pdf at all")
    assert extract_pdf_text_pages(pdf_path) == []


# ── Gemini fake (no live calls, ever) ───────────────────────────────────────


class _FakeGeminiClient:
    """Stands in for ``GeminiResponsesClient`` — records prompts, no HTTP."""

    def __init__(self, *, people=None, exc: Exception | None = None) -> None:
        self._people = list(people or [])
        self._exc = exc
        self.prompts: list[str] = []

    async def extract_bank_contacts(self, *, prompt: str) -> GeminiBankContactExtraction:
        self.prompts.append(prompt)
        if self._exc is not None:
            raise self._exc
        return GeminiBankContactExtraction(people=self._people)


def _llm_person(**overrides) -> GeminiBankContactPerson:
    defaults = dict(
        name="Priya Krishnamurthy",
        title=None,
        role="organizer",
        email=None,
        phone=None,
        page_number=3,
    )
    defaults.update(overrides)
    return GeminiBankContactPerson(**defaults)


# ── End-to-end: fetch → subprocess → parse (no DB) ─────────────────────────


async def test_collect_contacts_end_to_end() -> None:
    body = _build_minimal_pdf(
        [
            "Contact person: Jane A. Doe, President",
            "Telephone: (202) 555-0100",
            "Email: jane.doe@erebor.example",
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(body))

    service = BankContactExtractionService(
        transport=httpx.MockTransport(_handler),
        llm_client=_FakeGeminiClient(),  # Gemini finds nothing extra
    )
    contacts, stats = await service.collect_contacts(
        bank_id=42,
        bank_name="Erebor Bank, N.A.",
        pdf_entries=[
            {"title": "Erebor Bank, N.A.", "url": _PDF_URL, "received_date": None},
            {"title": "no url entry survives", "url": None},
            "not-a-dict entries are tolerated",
        ],
    )
    assert stats.pdfs_fetched == 1
    assert stats.contacts_extracted == 1
    assert stats.skipped_ambiguous == 0
    assert stats.llm_extracted == 0
    assert stats.llm_dropped_ungrounded == 0
    (contact,) = contacts
    assert contact.name == "Jane A. Doe"
    assert contact.title == "President"
    assert contact.phone == "(202) 555-0100"
    assert contact.email == "jane.doe@erebor.example"
    assert contact.source_url == _PDF_URL
    assert contact.source == DEFAULT_SOURCE


# ── 5. Gemini recall pass: grounding validation (the never-fabricate gate) ──
# All via _FakeGeminiClient — no live calls. The page fixture is a case the
# REGEX pass refuses (ALL-CAPS name under a heading it can't anchor): the
# recall win, provable end to end.

_LLM_PAGES = [
    {"page": 1, "text": "PUBLIC PORTION\nErebor Bank, N.A.\nApplication to the OCC.\n"},
    {
        "page": 3,
        "text": (
            "ORGANIZING GROUP OF THE PROPOSED BANK\n"
            "PRIYA KRISHNAMURTHY, chair of the organizing group\n"
            "Questions may be directed to pkrishnamurthy@erebor.example\n"
            "or (614) 555-0142.\n"
        ),
    },
]


def test_llm_fixture_is_refused_by_the_regex_pass() -> None:
    # Pin the premise: this exact text is a regex MISS (the ALL-CAPS name
    # fails the strict shape gate), so anything the LLM pass grounds here is
    # genuinely additive recall.
    contacts, ambiguous = parse_bank_contacts(_LLM_PAGES, source_url=_PDF_URL)
    assert contacts == []
    assert ambiguous == 1


async def test_llm_grounded_person_is_kept_with_source_receipt() -> None:
    client = _FakeGeminiClient(
        people=[
            _llm_person(
                title="chair of the organizing group",
                email="pkrishnamurthy@erebor.example",
                phone="(614) 555-0142",
            )
        ]
    )
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert dropped == 0
    (contact,) = contacts
    assert contact.source == LLM_SOURCE
    # Case-insensitive + whitespace-flexible grounding: the model returned
    # title case, the page prints ALL CAPS — verbatim tokens either way.
    assert contact.name == "Priya Krishnamurthy"
    assert contact.role_context == ROLE_ORGANIZER
    assert contact.title == "chair of the organizing group"
    assert contact.email == "pkrishnamurthy@erebor.example"
    assert contact.phone == "(614) 555-0142"
    assert contact.page_number == 3
    # The receipt is cut from the SOURCE page around the actual match.
    assert "PRIYA KRISHNAMURTHY" in (contact.context_snippet or "")
    assert contact.source_url == _PDF_URL


async def test_llm_ungrounded_name_is_dropped_and_counted() -> None:
    client = _FakeGeminiClient(
        people=[_llm_person(name="Robert Fabricated", page_number=3)]
    )
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert contacts == []  # a hallucination must never reach the caller
    assert dropped == 1


async def test_llm_ungrounded_email_and_phone_are_nulled_person_kept() -> None:
    client = _FakeGeminiClient(
        people=[
            _llm_person(
                email="fabricated@nowhere.example",
                phone="(999) 999-9999",
            )
        ]
    )
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert dropped == 0
    (contact,) = contacts
    assert contact.name == "Priya Krishnamurthy"
    assert contact.email is None  # not printed on the page → never stored
    assert contact.phone is None


async def test_llm_wrong_page_claim_self_corrects_to_the_actual_page() -> None:
    # Model claims page 1; the name is printed on page 3. Grounding stores
    # the page where the name ACTUALLY appears, not the model's claim.
    client = _FakeGeminiClient(people=[_llm_person(page_number=1)])
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert dropped == 0
    (contact,) = contacts
    assert contact.page_number == 3


async def test_llm_role_other_salvaged_only_from_grounded_title() -> None:
    pages = [{"page": 2, "text": "Prepared by Mark Stone, counsel to the organizers.\n"}]
    salvage = _FakeGeminiClient(
        people=[
            _llm_person(
                name="Mark Stone",
                title="counsel to the organizers",
                role="other",
                page_number=2,
            )
        ]
    )
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=salvage
    )
    assert dropped == 0
    assert contacts[0].role_context == ROLE_COUNSEL

    # Same person, role 'other', NO title to salvage from → dropped.
    unmapped = _FakeGeminiClient(
        people=[_llm_person(name="Mark Stone", role="other", page_number=2)]
    )
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=unmapped
    )
    assert contacts == []
    assert dropped == 1


async def test_llm_corporate_noun_name_dropped_even_when_grounded() -> None:
    # "Privacy Technology" IS printed verbatim on the page — grounding alone
    # would pass. The shared corporate-noun blocklist must still refuse it.
    pages = [
        {
            "page": 4,
            "text": "The organizers retained Privacy Technology to build controls.\n",
        }
    ]
    client = _FakeGeminiClient(
        people=[_llm_person(name="Privacy Technology", role="organizer", page_number=4)]
    )
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=client
    )
    assert contacts == []
    assert dropped == 1


async def test_llm_chunking_splits_on_page_boundaries(monkeypatch) -> None:
    # Force one page per chunk; grounding is per chunk, so the person only
    # survives from the chunk that actually contains page 3.
    monkeypatch.setattr(llm_extraction, "_CHUNK_CHAR_BUDGET", 10)
    client = _FakeGeminiClient(
        people=[_llm_person()]  # returned for BOTH chunk calls
    )
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert len(client.prompts) == 2  # one call per page-chunk, never mid-page
    assert "--- PAGE 1 ---" in client.prompts[0]
    assert "--- PAGE 3 ---" in client.prompts[1]
    assert "NEVER invent" in client.prompts[0]  # the verbatim-only order
    (contact,) = contacts
    assert contact.page_number == 3
    assert dropped == 1  # the chunk-1 copy could not ground → refused


async def test_llm_provider_error_degrades_to_no_results_not_a_raise() -> None:
    client = _FakeGeminiClient(exc=GeminiExtractionError("Gemini 503"))
    contacts, dropped = await extract_contacts_via_llm(
        _LLM_PAGES, source_url=_PDF_URL, client=client
    )
    assert (contacts, dropped) == ([], 0)


# ── 5b. Service-level fallback + merge (regex wins, LLM adds recall) ────────


def _two_pass_pdf_body() -> bytes:
    return _build_minimal_pdf(
        [
            "Contact person: Jane A. Doe, President",
            "Telephone: (202) 555-0100",
            "PROPOSED CHIEF RISK OFFICER: TUNDE ADEYEMI-OKAFOR",
        ]
    )


def _two_pass_service(llm_client) -> BankContactExtractionService:
    body = _two_pass_pdf_body()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(body))

    return BankContactExtractionService(
        transport=httpx.MockTransport(_handler), llm_client=llm_client
    )


async def test_collect_contacts_merges_llm_recall_and_regex_wins_conflicts() -> None:
    llm_client = _FakeGeminiClient(
        people=[
            # Duplicate of the regex hit (same name + title): regex must win.
            _llm_person(
                name="Jane A. Doe",
                title="President",
                role="contact_person",
                page_number=1,
            ),
            # The regex miss (ALL-CAPS officer): the LLM recall win.
            _llm_person(
                name="TUNDE ADEYEMI-OKAFOR",
                title="Chief Risk Officer",
                role="proposed_officer",
                page_number=1,
            ),
        ]
    )
    service = _two_pass_service(llm_client)
    contacts, stats = await service.collect_contacts(
        bank_id=42,
        bank_name="Erebor Bank, N.A.",
        pdf_entries=[{"url": _PDF_URL}],
    )
    by_name = {c.name: c for c in contacts}
    assert set(by_name) == {"Jane A. Doe", "TUNDE ADEYEMI-OKAFOR"}
    # Regex row won the (name, title) conflict — it keeps the regex source
    # and the pattern-anchored snippet.
    assert by_name["Jane A. Doe"].source == DEFAULT_SOURCE
    assert by_name["Jane A. Doe"].phone == "(202) 555-0100"
    # The LLM-only person is tagged distinctly and grounded to the page.
    assert by_name["TUNDE ADEYEMI-OKAFOR"].source == LLM_SOURCE
    assert by_name["TUNDE ADEYEMI-OKAFOR"].title == "Chief Risk Officer"
    assert by_name["TUNDE ADEYEMI-OKAFOR"].role_context == ROLE_PROPOSED_OFFICER
    assert stats.contacts_extracted == 2
    assert stats.llm_extracted == 1  # only the NOVEL person counts
    assert stats.llm_dropped_ungrounded == 0


async def test_collect_contacts_without_gemini_key_is_regex_only(caplog) -> None:
    # GeminiConfigurationError (no key) → warning once, LLM pass disabled
    # for the rest of the run, results identical to the pre-LLM extractor.
    llm_client = _FakeGeminiClient(
        exc=GeminiConfigurationError("GEMINI_API_KEY is not configured.")
    )
    service = _two_pass_service(llm_client)
    with caplog.at_level(logging.WARNING, logger="app.services.bank_contact_extraction"):
        contacts, stats = await service.collect_contacts(
            bank_id=42,
            bank_name="Erebor Bank, N.A.",
            pdf_entries=[{"url": _PDF_URL}, {"url": _PDF_URL + "?copy=2"}],
        )
    assert [c.name for c in contacts] == ["Jane A. Doe"]
    assert contacts[0].source == DEFAULT_SOURCE
    assert stats.pdfs_fetched == 2
    assert stats.llm_extracted == 0
    assert stats.llm_dropped_ungrounded == 0
    assert len(llm_client.prompts) == 1  # disabled after the first failure
    assert any("regex-only" in message for message in caplog.messages)


async def test_collect_contacts_survives_llm_schema_surprise() -> None:
    # An unexpected exception from the LLM pass (e.g. a response-shape
    # surprise) must degrade to regex-only for that PDF — never a raise.
    llm_client = _FakeGeminiClient(exc=RuntimeError("schema surprise"))
    service = _two_pass_service(llm_client)
    contacts, stats = await service.collect_contacts(
        bank_id=42, bank_name="Erebor Bank, N.A.", pdf_entries=[{"url": _PDF_URL}]
    )
    assert [c.name for c in contacts] == ["Jane A. Doe"]
    assert stats.llm_extracted == 0


# ── 4. Idempotent upserts (real engine) ─────────────────────────────────────


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Only the table under test — the banks table carries JSONB (PG-only)
        # and SQLite never enforces the FK by default, so bank_id=1 is fine.
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=[BankContact.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _contact(**overrides) -> ExtractedBankContact:
    defaults = dict(
        name="Jane A. Doe",
        title="President",
        role_context=ROLE_CONTACT_PERSON,
        email="jane.doe@erebor.example",
        phone="(202) 555-0100",
        source_url=_PDF_URL,
        page_number=1,
        context_snippet="Contact person: Jane A. Doe, President",
    )
    defaults.update(overrides)
    return ExtractedBankContact(**defaults)


async def test_upsert_is_idempotent_on_the_dedupe_key(sqlite_session) -> None:
    service = BankContactExtractionService()
    contacts = [
        _contact(),
        _contact(name="John Q. Smith", title=None, role_context=ROLE_ORGANIZER,
                 email=None, phone=None, page_number=2),
    ]
    first = await service.upsert_contacts(sqlite_session, 1, contacts)
    await sqlite_session.commit()
    second = await service.upsert_contacts(sqlite_session, 1, contacts)
    await sqlite_session.commit()

    assert first == (2, 0)
    assert second == (0, 2)  # re-run updates in place, inserts nothing
    rows = (await sqlite_session.execute(select(BankContact))).scalars().all()
    assert len(rows) == 2


async def test_upsert_updates_additively_never_nulling_channels(sqlite_session) -> None:
    service = BankContactExtractionService()
    await service.upsert_contacts(sqlite_session, 1, [_contact()])
    await sqlite_session.commit()

    # Re-extraction found the same person but this time no email/phone and a
    # different page: channels must SURVIVE, the receipt may refresh.
    await service.upsert_contacts(
        sqlite_session, 1,
        [_contact(email=None, phone=None, page_number=3, context_snippet="new snippet")],
    )
    await sqlite_session.commit()

    (row,) = (await sqlite_session.execute(select(BankContact))).scalars().all()
    assert row.email == "jane.doe@erebor.example"
    assert row.phone == "(202) 555-0100"
    assert row.page_number == 3
    assert row.context_snippet == "new snippet"


async def test_same_name_different_title_is_a_distinct_row(sqlite_session) -> None:
    # (bank_id, name, coalesce(title,''), source) — the proposed-CEO row and
    # the untitled organizer row for the same person coexist by design.
    service = BankContactExtractionService()
    await service.upsert_contacts(
        sqlite_session, 1,
        [
            _contact(title=None, role_context=ROLE_ORGANIZER, email=None, phone=None),
            _contact(title="President and Chief Executive Officer",
                     role_context=ROLE_PROPOSED_OFFICER, email=None, phone=None),
        ],
    )
    await sqlite_session.commit()
    rows = (await sqlite_session.execute(select(BankContact))).scalars().all()
    assert len(rows) == 2


async def test_unique_index_rejects_raw_duplicates(sqlite_session) -> None:
    sqlite_session.add(
        BankContact(bank_id=1, name="Jane A. Doe", title=None,
                    role_context=ROLE_CONTACT_PERSON, source_url=_PDF_URL,
                    source="application_pdf")
    )
    await sqlite_session.commit()
    sqlite_session.add(
        BankContact(bank_id=1, name="Jane A. Doe", title=None,
                    role_context=ROLE_ORGANIZER, source_url=_PDF_URL,
                    source="application_pdf")
    )
    with pytest.raises(IntegrityError):
        await sqlite_session.commit()


async def test_llm_and_regex_rows_coexist_for_different_people(sqlite_session) -> None:
    # Distinct people from the two passes land as distinct rows; the LLM row
    # keeps its distinguishing source through the same upsert path.
    service = BankContactExtractionService()
    await service.upsert_contacts(
        sqlite_session, 1,
        [
            _contact(),
            _contact(name="Priya Krishnamurthy", title=None, email=None,
                     phone=None, role_context=ROLE_ORGANIZER, source=LLM_SOURCE),
        ],
    )
    await sqlite_session.commit()
    rows = (await sqlite_session.execute(select(BankContact))).scalars().all()
    assert {(row.name, row.source) for row in rows} == {
        ("Jane A. Doe", DEFAULT_SOURCE),
        ("Priya Krishnamurthy", LLM_SOURCE),
    }


async def test_upsert_regex_row_supersedes_llm_twin_across_runs(sqlite_session) -> None:
    # Run 1: the person was only found by the LLM pass. Run 2: the regex
    # pass now anchors them — the regex row must REPLACE the LLM twin
    # (same name + title), never duplicate the person on the detail card.
    service = BankContactExtractionService()
    await service.upsert_contacts(
        sqlite_session, 1,
        [_contact(email=None, phone=None, source=LLM_SOURCE,
                  context_snippet="llm receipt")],
    )
    await sqlite_session.commit()

    inserted, updated = await service.upsert_contacts(sqlite_session, 1, [_contact()])
    await sqlite_session.commit()

    assert (inserted, updated) == (1, 0)
    (row,) = (await sqlite_session.execute(select(BankContact))).scalars().all()
    assert row.source == DEFAULT_SOURCE
    assert row.email == "jane.doe@erebor.example"
    assert row.context_snippet == "Contact person: Jane A. Doe, President"
