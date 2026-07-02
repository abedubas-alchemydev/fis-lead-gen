"""Grounded Gemini recall pass — security fast-follow coverage.

No live Gemini calls, ever: a tiny in-file fake stands in for
``GeminiResponsesClient``. These tests pin the wave-3 security fixes:

* **M1 (spend ceilings)** — ``_chunk_pages`` truncates an oversized page to
  ``_MAX_PAGE_CHARS`` and caps a PDF at ``_MAX_CHUNKS_PER_PDF`` chunks.
* **M2 (same-page channel grounding)** — an email/phone grounds only within
  ``_CHANNEL_PROXIMITY_CHARS`` of the name match, on the SAME page; a value on
  another page (or far away on the same page) is NULLed, person kept.
* **L1 (never-fail contract)** — a malformed ``BANK_CONTACT_LLM_*`` int env
  var falls back to the default instead of raising at import.
* **L3 (no PII in logs)** — a nulled ungrounded channel is logged by field +
  length only, never by value.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import app.services.bank_contact_llm as llm
from app.services.bank_contact_llm import extract_contacts_via_llm
from app.services.gemini_responses import (
    GeminiBankContactExtraction,
    GeminiBankContactPerson,
)

_PDF_URL = "https://www.occ.gov/topics/digital-assets/apps/erebor-public.pdf"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


# ── Fakes / helpers ──────────────────────────────────────────────────────────


class _FakeGeminiClient:
    """Records prompts, returns a fixed people list — no HTTP, ever."""

    def __init__(self, people) -> None:
        self._people = list(people)
        self.prompts: list[str] = []

    async def extract_bank_contacts(self, *, prompt: str) -> GeminiBankContactExtraction:
        self.prompts.append(prompt)
        return GeminiBankContactExtraction(people=self._people)


def _person(**overrides) -> GeminiBankContactPerson:
    defaults = dict(
        name="Priya Krishnamurthy",
        title=None,
        role="organizer",
        email=None,
        phone=None,
        page_number=1,
    )
    defaults.update(overrides)
    return GeminiBankContactPerson(**defaults)


# ── M1: per-page truncation + chunk cap ─────────────────────────────────────


def test_giant_single_page_is_truncated_to_the_per_page_cap() -> None:
    # One enormous page must not become a single near-context-limit prompt.
    pages = [{"page": 1, "text": "A" * 100_000}]
    chunks = llm._chunk_pages(pages)
    assert len(chunks) == 1
    (only_page,) = chunks[0]
    assert only_page[0] == 1
    assert len(only_page[1]) == llm._MAX_PAGE_CHARS  # truncated, not passed whole


def test_chunk_count_is_capped_per_pdf(monkeypatch) -> None:
    # Force one page per chunk (tiny budget) and feed far more pages than the
    # cap: the real _MAX_CHUNKS_PER_PDF ceiling must bound the paid calls.
    monkeypatch.setattr(llm, "_CHUNK_CHAR_BUDGET", 5)
    pages = [{"page": i, "text": f"PAGE {i} organizing group blurb"} for i in range(1, 21)]
    chunks = llm._chunk_pages(pages)
    assert len(chunks) == llm._MAX_CHUNKS_PER_PDF == 8
    # Kept chunks are the FIRST pages; the tail degrades to regex-only.
    assert [chunk[0][0] for chunk in chunks] == [1, 2, 3, 4, 5, 6, 7, 8]


# ── M2: same-page + proximity channel grounding ─────────────────────────────


async def test_channel_on_a_different_page_is_not_paired() -> None:
    # THE required case: a real name on page 1, an attacker-planted email that
    # only appears on page 2 — the email must NOT attach to the person.
    pages = [
        {
            "page": 1,
            "text": (
                "ORGANIZING GROUP OF THE PROPOSED BANK\n"
                "PRIYA KRISHNAMURTHY, chair of the organizing group\n"
            ),
        },
        {
            "page": 2,
            "text": "Appendix B. For unrelated inquiries, write to attacker@evil.example.\n",
        },
    ]
    client = _FakeGeminiClient([_person(email="attacker@evil.example", page_number=1)])
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=client
    )
    assert dropped == 0
    (contact,) = contacts  # the person survives
    assert contact.name == "Priya Krishnamurthy"
    assert contact.email is None  # page-2 email never grounds onto a page-1 name
    assert contact.page_number == 1


async def test_same_page_channel_within_proximity_is_kept() -> None:
    # Recall preserved for the normal contact stanza: name + email adjacent on
    # one page still pairs.
    pages = [
        {
            "page": 1,
            "text": (
                "ORGANIZING GROUP OF THE PROPOSED BANK\n"
                "PRIYA KRISHNAMURTHY, chair of the organizing group\n"
                "Email: pkrishnamurthy@erebor.example\n"
            ),
        }
    ]
    client = _FakeGeminiClient(
        [_person(email="pkrishnamurthy@erebor.example", page_number=1)]
    )
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=client
    )
    (contact,) = contacts
    assert contact.email == "pkrishnamurthy@erebor.example"


async def test_same_page_channel_beyond_proximity_is_nulled() -> None:
    # Same page, but the email is far (> _CHANNEL_PROXIMITY_CHARS) from the
    # name match — e.g. a different person's block further down. Still NULLed.
    far_filler = "padding " * 300  # ~2400 chars, no digits/@
    pages = [
        {
            "page": 1,
            "text": (
                "PRIYA KRISHNAMURTHY, chair of the organizing group. "
                + far_filler
                + " Reach the back office at farside@erebor.example."
            ),
        }
    ]
    client = _FakeGeminiClient([_person(email="farside@erebor.example", page_number=1)])
    contacts, dropped = await extract_contacts_via_llm(
        pages, source_url=_PDF_URL, client=client
    )
    (contact,) = contacts
    assert contact.name == "Priya Krishnamurthy"  # person kept
    assert contact.email is None  # channel beyond the proximity window is dropped


# ── L1: never-fail env parsing ──────────────────────────────────────────────


def test_env_int_parses_good_and_falls_back_on_bad() -> None:
    assert llm._env_int("fis_missing_var_xyzzy", 7) == 7  # missing -> default
    with mock.patch.dict(os.environ, {"FIS_TEST_INT": "123"}):
        assert llm._env_int("FIS_TEST_INT", 7) == 123      # good -> parsed
    with mock.patch.dict(os.environ, {"FIS_TEST_INT": "not-an-int"}):
        assert llm._env_int("FIS_TEST_INT", 7) == 7        # malformed -> default
    with mock.patch.dict(os.environ, {"FIS_TEST_INT": ""}):
        assert llm._env_int("FIS_TEST_INT", 7) == 7        # empty -> default


def test_import_with_malformed_cap_envs_does_not_raise() -> None:
    # A malformed override must NOT raise at import — bank_contact_llm is
    # imported lazily from collect_contacts, so a ValueError here would crash
    # the whole contact-collection phase. Run in a FRESH interpreter so the
    # shared test process's already-imported module is untouched.
    env = {
        **os.environ,
        "PYTHONPATH": ".",
        "BANK_CONTACT_LLM_CHUNK_CHARS": "lots",
        "BANK_CONTACT_LLM_MAX_CHUNKS": "eight",
        "BANK_CONTACT_LLM_MAX_PAGE_CHARS": "",
        "BANK_CONTACT_LLM_CHANNEL_PROXIMITY_CHARS": "near",
    }
    code = (
        "import app.services.bank_contact_llm as m;"
        "assert m._CHUNK_CHAR_BUDGET == 20000, m._CHUNK_CHAR_BUDGET;"
        "assert m._MAX_CHUNKS_PER_PDF == 8, m._MAX_CHUNKS_PER_PDF;"
        "assert m._MAX_PAGE_CHARS == 20000, m._MAX_PAGE_CHARS;"
        "assert m._CHANNEL_PROXIMITY_CHARS == 500, m._CHANNEL_PROXIMITY_CHARS;"
        "print('import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "import-ok" in result.stdout


def test_module_reload_with_bad_env_keeps_defaults() -> None:
    # Belt-and-braces at the module level (reload in-process, then restore):
    # the constants are wired through _env_int, so a bad env keeps defaults.
    with mock.patch.dict(os.environ, {"BANK_CONTACT_LLM_MAX_CHUNKS": "eight"}):
        reloaded = importlib.reload(llm)
        try:
            assert reloaded._MAX_CHUNKS_PER_PDF == 8
        finally:
            importlib.reload(llm)  # restore clean constants for later tests


# ── L3: ungrounded channel value is never logged ────────────────────────────


async def test_ungrounded_channel_is_logged_by_length_not_value(caplog) -> None:
    pages = [
        {
            "page": 1,
            "text": (
                "ORGANIZING GROUP OF THE PROPOSED BANK\n"
                "PRIYA KRISHNAMURTHY, chair of the organizing group\n"
            ),
        }
    ]
    secret_email = "attacker-secret@evil.example"
    client = _FakeGeminiClient([_person(email=secret_email, page_number=1)])
    with caplog.at_level(logging.INFO, logger="app.services.bank_contact_llm"):
        contacts, _dropped = await extract_contacts_via_llm(
            pages, source_url=_PDF_URL, client=client
        )
    (contact,) = contacts
    assert contact.email is None
    # The untrusted value must NOT appear in the logs; field + length do.
    assert secret_email not in caplog.text
    assert "nulling ungrounded email" in caplog.text
    assert f"len={len(secret_email)}" in caplog.text
