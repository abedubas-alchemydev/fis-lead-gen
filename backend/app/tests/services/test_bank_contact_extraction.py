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

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.bank_contact_extraction as extraction
from app.db.base import Base
from app.models.bank import BankContact
from app.services.bank_contact_extraction import (
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

    service = _service(_handler)
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
    (contact,) = contacts
    assert contact.name == "Jane A. Doe"
    assert contact.title == "President"
    assert contact.phone == "(202) 555-0100"
    assert contact.email == "jane.doe@erebor.example"
    assert contact.source_url == _PDF_URL


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
