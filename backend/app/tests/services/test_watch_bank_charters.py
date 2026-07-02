"""Unit tests for the bank-charter watcher (``scripts/watch_bank_charters.py``):
the pure window resolution, the curated digital-assets SEED
(``KNOWN_DIGITAL_ASSET_APPLICANTS`` / ``_apply_digital_asset_seed``), and the
Wayback history tagging (``_tag_digital_asset_history_entries``).

Everything else in the script is orchestration over pieces covered by their
own suites (``test_fdic_bankfind.py``, ``test_occ_cas.py``,
``test_banks_repository.py``); the window arithmetic is the part where an
off-by-one or a swapped bound silently turns the nightly run into a no-op,
and the seed is the part where a matching bug would silently mis-tag rows,
so both get pinned here. Import bootstrap mirrors ``test_extract_new_bds.py``
— the script's module top-level is deliberately light (no ``app.*`` imports
at import time).
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pytest

# Repo root = backend/app/tests/services/<this file> → parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.watch_bank_charters import (  # noqa: E402
    KNOWN_DIGITAL_ASSET_APPLICANTS,
    _apply_digital_asset_seed,
    _reconcile_occ_banks,
    _tag_digital_asset_history_entries,
    resolve_window,
)

TODAY = date(2026, 7, 2)


def test_default_trailing_window() -> None:
    start, end = resolve_window(from_date=None, to_date=None, window_days=30, today=TODAY)
    assert (start, end) == (date(2026, 6, 2), TODAY)


def test_explicit_backfill_from_date_wins() -> None:
    start, end = resolve_window(
        from_date=date(2024, 1, 1), to_date=None, window_days=30, today=TODAY
    )
    assert (start, end) == (date(2024, 1, 1), TODAY)


def test_explicit_to_date_anchors_the_trailing_window() -> None:
    start, end = resolve_window(
        from_date=None, to_date=date(2026, 3, 31), window_days=7, today=TODAY
    )
    assert (start, end) == (date(2026, 3, 24), date(2026, 3, 31))


def test_backwards_window_raises_instead_of_silently_no_oping() -> None:
    with pytest.raises(ValueError):
        resolve_window(
            from_date=date(2026, 7, 3), to_date=date(2026, 7, 1), window_days=30, today=TODAY
        )


# ── Curated digital-assets seed (KNOWN_DIGITAL_ASSET_APPLICANTS) ─────────

from app.models.bank import Bank  # noqa: E402
from app.services.banks import BankRepository  # noqa: E402

_SEED_LOGGER = "watch_bank_charters"
_EREBOR = ("Erebor Bank, N.A.", "OH", "25357")


class _SeedStubRepository(BankRepository):
    """Real ``apply_digital_asset_tag`` (the mutation under test rides the
    production code path); canned lookups so no live DB is needed."""

    def __init__(self, *, by_charter=None, by_name=None) -> None:
        self._by_charter = by_charter or {}
        self._by_name = by_name or {}
        self.charter_lookups: list[str] = []
        self.name_lookups: list[str] = []

    async def find_banks_by_occ_charter_number(self, db, charter_number):
        self.charter_lookups.append(charter_number)
        return list(self._by_charter.get(charter_number, []))

    async def find_banks_by_normalized_name(self, db, name):
        self.name_lookups.append(name)
        return list(self._by_name.get(name, []))


def _erebor_bank(**overrides) -> Bank:
    defaults = dict(
        id=42,
        name="Erebor Bank, N.A.",
        state="OH",
        occ_charter_number="25357",
        fdic_cert="59378",
        charter_status="opened",
        digital_assets=False,
        digital_asset_pdfs=None,
        application_received_date=None,
        source="fdic+occ",
    )
    defaults.update(overrides)
    return Bank(**defaults)


def test_seed_constant_pins_the_entry_format() -> None:
    # (name, state, occ_charter_number or None) — one line per entry; keep
    # this pinned so a malformed future addition fails loudly here.
    assert KNOWN_DIGITAL_ASSET_APPLICANTS == (_EREBOR,)
    for name, state, charter_number in KNOWN_DIGITAL_ASSET_APPLICANTS:
        assert name and isinstance(name, str)
        assert state and isinstance(state, str) and len(state) == 2
        assert charter_number is None or (
            isinstance(charter_number, str) and charter_number.strip()
        )


async def test_seed_match_by_charter_number_tags_the_row(caplog) -> None:
    bank = _erebor_bank()
    repo = _SeedStubRepository(by_charter={"25357": [bank]})
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _apply_digital_asset_seed(
            object(), repository=repo, apply=True, seed=(_EREBOR,)
        )
    assert (tagged, unmatched) == (1, 0)
    assert bank.digital_assets is True
    # Seed entries carry no PDFs and no received-date — row stays clean.
    assert bank.digital_asset_pdfs is None
    assert bank.application_received_date is None
    # Strong key wins outright: the name matcher is never consulted.
    assert repo.charter_lookups == ["25357"]
    assert repo.name_lookups == []
    assert "digital-assets: seed-tagged 'Erebor Bank, N.A.'" in caplog.text


async def test_seed_rerun_is_idempotent(caplog) -> None:
    bank = _erebor_bank()
    repo = _SeedStubRepository(by_charter={"25357": [bank]})
    await _apply_digital_asset_seed(object(), repository=repo, apply=True, seed=(_EREBOR,))
    caplog.clear()  # drop the first run's 'seed-tagged' record
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged_again, unmatched_again = await _apply_digital_asset_seed(
            object(), repository=repo, apply=True, seed=(_EREBOR,)
        )
    # Sticky tag already set -> the re-run changes NOTHING.
    assert (tagged_again, unmatched_again) == (0, 0)
    assert bank.digital_assets is True
    assert bank.digital_asset_pdfs is None
    assert bank.application_received_date is None
    assert "already tagged" in caplog.text
    assert "seed-tagged" not in caplog.text


async def test_seed_unmatched_entry_logs_and_skips(caplog) -> None:
    # Neither the charter number nor the name matches anything ingested.
    repo = _SeedStubRepository()
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _apply_digital_asset_seed(
            object(), repository=repo, apply=True, seed=(_EREBOR,)
        )
    assert (tagged, unmatched) == (0, 1)
    assert "0 match(es); skipping" in caplog.text


async def test_seed_ambiguous_name_match_skips_and_never_guesses() -> None:
    twins = [_erebor_bank(id=1), _erebor_bank(id=2)]
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": twins})
    tagged, unmatched = await _apply_digital_asset_seed(
        object(), repository=repo, apply=True, seed=((_EREBOR[0], "OH", None),)
    )
    assert (tagged, unmatched) == (0, 1)
    assert all(bank.digital_assets is False for bank in twins)


async def test_seed_falls_back_to_name_and_state_when_charter_unstamped() -> None:
    # Reconciliation hasn't stamped the charter number on the row yet (or
    # the row is FDIC-only): 0 charter hits -> the page's name matcher,
    # narrowed by the entry's state, still finds it uniquely.
    erebor = _erebor_bank(occ_charter_number=None)
    decoy = _erebor_bank(id=99, state="NV", occ_charter_number=None)
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": [erebor, decoy]})
    tagged, unmatched = await _apply_digital_asset_seed(
        object(), repository=repo, apply=True, seed=(_EREBOR,)
    )
    assert (tagged, unmatched) == (1, 0)
    assert repo.charter_lookups == ["25357"]
    assert repo.name_lookups == ["Erebor Bank, N.A."]
    assert erebor.digital_assets is True
    assert decoy.digital_assets is False


async def test_seed_dry_run_counts_but_never_writes(caplog) -> None:
    bank = _erebor_bank()
    repo = _SeedStubRepository(by_charter={"25357": [bank]})
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _apply_digital_asset_seed(
            object(), repository=repo, apply=False, seed=(_EREBOR,)
        )
    assert (tagged, unmatched) == (1, 0)
    assert bank.digital_assets is False
    assert "would seed-tag" in caplog.text


# ── Reconcile phase: Institutions API primary, XLSX fallback ─────────────

from app.services.occ_cas import OccNationalBankDirectoryRow  # noqa: E402

_RECONCILE_LOGGER = "watch_bank_charters"


class _StubOccService:
    """Canned directory sources; counts which one the reconcile phase hit.

    ``api_rows=None`` models every fetch_active_institutions bail-out
    (key unset / HTTP error / schema surprise — the real client collapses
    them all to None).
    """

    def __init__(self, *, api_rows=None, xlsx_rows=None) -> None:
        self._api_rows = api_rows
        self._xlsx_rows = xlsx_rows or []
        self.api_calls = 0
        self.xlsx_calls = 0

    async def fetch_active_institutions(self):
        self.api_calls += 1
        return None if self._api_rows is None else list(self._api_rows)

    async def fetch_national_bank_directory(self):
        self.xlsx_calls += 1
        return list(self._xlsx_rows)


class _StubFdicService:
    def __init__(self) -> None:
        self.requested_certs: list[list[str]] = []

    async def fetch_institutions_by_certs(self, certs):
        self.requested_certs.append(list(certs))
        return []  # enrichment fetch is exercised, upsert path is not


class _ReconcileStubRepository(BankRepository):
    """No pre-existing FDIC row for any cert (the merge path has its own
    suite in test_banks_upsert_sql.py)."""

    async def find_by_cert(self, db, cert):
        return None


class _FakeDb:
    async def flush(self) -> None:
        pass


def _unreconciled_erebor(**overrides) -> Bank:
    defaults = dict(
        id=7,
        occ_control_number="2025-Charter-342076",
        name="Erebor Bank, NA",  # the CAS spelling
        state="OH",
        charter_status="approved",
        source="occ",
        fdic_cert=None,
        fed_rssd=None,
        occ_charter_number=None,
        lei=None,
        charter_type=None,
    )
    defaults.update(overrides)
    return Bank(**defaults)


# The SAME institution as both sources see it: the API row carries the
# LEI/CharterType enrichment, the XLSX row cannot.
_EREBOR_API_ROW = OccNationalBankDirectoryRow(
    charter_number="25357",
    name="Erebor Bank, National Association",
    city="Columbus",
    state="OH",
    fdic_cert="59378",
    fed_rssd="1234567",
    lei="549300EREBOR0000LE00",
    charter_type="National",
)
_EREBOR_XLSX_ROW = OccNationalBankDirectoryRow(
    charter_number="25357",
    name="Erebor Bank, National Association",
    city="Columbus",
    state="OH",
    fdic_cert="59378",
    fed_rssd="1234567",
)


async def _run_reconcile(occ, bank, *, apply=True):
    fdic = _StubFdicService()
    linked, source = await _reconcile_occ_banks(
        _FakeDb(),
        repository=_ReconcileStubRepository(),
        occ=occ,
        fdic=fdic,
        unreconciled=[bank],
        apply=apply,
    )
    return linked, source, fdic


async def test_reconcile_uses_the_api_directory_when_available(caplog) -> None:
    bank = _unreconciled_erebor()
    occ = _StubOccService(api_rows=[_EREBOR_API_ROW], xlsx_rows=[_EREBOR_XLSX_ROW])
    with caplog.at_level(logging.INFO, logger=_RECONCILE_LOGGER):
        linked, source, fdic = await _run_reconcile(occ, bank)
    assert (linked, source) == (1, "api")
    # API served -> the XLSX workbook is never even downloaded.
    assert (occ.api_calls, occ.xlsx_calls) == (1, 0)
    assert "reconcile: directory source=api (1 row(s))" in caplog.text
    # Identity stamped exactly as the XLSX path would...
    assert bank.occ_charter_number == "25357"
    assert bank.fed_rssd == "1234567"
    assert bank.fdic_cert == "59378"
    # ...plus the API-only enrichment.
    assert bank.lei == "549300EREBOR0000LE00"
    assert bank.charter_type == "National"
    # Freshly-linked cert queued for FDIC enrichment, same as before.
    assert fdic.requested_certs == [["59378"]]


async def test_reconcile_falls_back_to_xlsx_when_api_unavailable(caplog) -> None:
    # api_rows=None == key unset / API down / schema surprise.
    bank = _unreconciled_erebor()
    occ = _StubOccService(api_rows=None, xlsx_rows=[_EREBOR_XLSX_ROW])
    with caplog.at_level(logging.INFO, logger=_RECONCILE_LOGGER):
        linked, source, _fdic = await _run_reconcile(occ, bank)
    assert (linked, source) == (1, "xlsx")
    assert (occ.api_calls, occ.xlsx_calls) == (1, 1)
    assert "reconcile: directory source=xlsx (1 row(s))" in caplog.text
    assert bank.fdic_cert == "59378"
    # The workbook carries no LEI/CharterType — enrichment stays NULL.
    assert bank.lei is None
    assert bank.charter_type is None


async def test_reconcile_api_and_xlsx_paths_link_identically() -> None:
    """The reconcile CONTRACT: same directory data -> same links, same
    stamped identity, whichever source served it."""
    api_bank = _unreconciled_erebor()
    xlsx_bank = _unreconciled_erebor()
    api_linked, api_source, _ = await _run_reconcile(
        _StubOccService(api_rows=[_EREBOR_API_ROW]), api_bank
    )
    xlsx_linked, xlsx_source, _ = await _run_reconcile(
        _StubOccService(api_rows=None, xlsx_rows=[_EREBOR_XLSX_ROW]), xlsx_bank
    )
    assert (api_linked, xlsx_linked) == (1, 1)
    assert (api_source, xlsx_source) == ("api", "xlsx")

    def identity(bank: Bank) -> tuple:
        return (bank.occ_charter_number, bank.fed_rssd, bank.fdic_cert)

    assert identity(api_bank) == identity(xlsx_bank) == ("25357", "1234567", "59378")


async def test_reconcile_api_path_keeps_the_never_guess_rule() -> None:
    # Two API rows collapsing to the same normalized name + state must
    # link NOTHING — identical unique-match semantics as the XLSX path.
    twin = OccNationalBankDirectoryRow(
        charter_number="99999",
        name="Erebor Bank, N.A.",  # same normalized token
        state="OH",
        fdic_cert="11111",
    )
    bank = _unreconciled_erebor()
    linked, source, _ = await _run_reconcile(
        _StubOccService(api_rows=[_EREBOR_API_ROW, twin]), bank
    )
    assert (linked, source) == (0, "api")
    assert bank.fdic_cert is None
    assert bank.lei is None


async def test_reconcile_dry_run_reports_source_but_writes_nothing() -> None:
    bank = _unreconciled_erebor()
    occ = _StubOccService(api_rows=[_EREBOR_API_ROW])
    linked, source, fdic = await _run_reconcile(occ, bank, apply=False)
    assert (linked, source) == (1, "api")
    assert bank.occ_charter_number is None
    assert bank.fdic_cert is None
    assert bank.lei is None
    assert bank.charter_type is None
    assert fdic.requested_certs == []  # no enrichment fetch in dry-run


# ── Wayback history rows (--backfill-digital-assets-history) ─────────────

from app.services.occ_cas import OccDigitalAssetHistoryEntry  # noqa: E402

_HISTORY_PDF_1 = (
    "https://www.occ.gov/topics/charters-and-licensing/"
    "digital-assets-licensing-applications/erebor-bank.pdf"
)
_HISTORY_PDF_2 = (
    "https://www.occ.gov/topics/charters-and-licensing/"
    "digital-assets-licensing-applications/erebor-bank-amended.pdf"
)


def _erebor_history_entry(**overrides) -> OccDigitalAssetHistoryEntry:
    defaults = dict(
        applicant="Erebor Bank, N.A.",
        received_date=date(2025, 6, 12),
        pdf_urls=(_HISTORY_PDF_1, _HISTORY_PDF_2),
    )
    defaults.update(overrides)
    return OccDigitalAssetHistoryEntry(**defaults)


async def test_history_unique_match_tags_sticky_and_merges_the_pdf_set(caplog) -> None:
    # occ_control_number set -> the row is an OCC application row, so the
    # page's received date may backfill (same rule as live-page rows).
    bank = _erebor_bank(occ_control_number="2025-Charter-344111")
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": [bank]})
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _tag_digital_asset_history_entries(
            object(), repository=repo, entries=[_erebor_history_entry()], apply=True
        )
    assert (tagged, unmatched) == (1, 0)
    assert bank.digital_assets is True
    # EVERY url in the newest non-empty set lands, via the same sticky
    # apply_digital_asset_tag the live rows use.
    assert [entry["url"] for entry in bank.digital_asset_pdfs] == [
        _HISTORY_PDF_1, _HISTORY_PDF_2,
    ]
    assert bank.application_received_date == date(2025, 6, 12)
    assert "digital-assets-history: tagged bank id=42" in caplog.text


async def test_history_rerun_is_idempotent() -> None:
    bank = _erebor_bank(occ_control_number="2025-Charter-344111")
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": [bank]})
    entries = [_erebor_history_entry()]
    await _tag_digital_asset_history_entries(
        object(), repository=repo, entries=entries, apply=True
    )
    tagged_again, unmatched_again = await _tag_digital_asset_history_entries(
        object(), repository=repo, entries=entries, apply=True
    )
    # Sticky tag + URL-deduped PDF merge -> the re-run changes NOTHING.
    assert (tagged_again, unmatched_again) == (0, 0)
    assert len(bank.digital_asset_pdfs) == 2


async def test_history_zero_and_ambiguous_matches_skip_and_never_guess(caplog) -> None:
    twins = [_erebor_bank(id=1), _erebor_bank(id=2)]
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": twins})
    entries = [
        _erebor_history_entry(),  # 2 matches -> ambiguous
        _erebor_history_entry(applicant="Never Ingested Trust Company, N.A."),  # 0
    ]
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _tag_digital_asset_history_entries(
            object(), repository=repo, entries=entries, apply=True
        )
    assert (tagged, unmatched) == (0, 2)
    assert all(bank.digital_assets is False for bank in twins)
    assert "digital-assets-history: 'Erebor Bank, N.A.' received 2025-06-12 -> 2 match(es); skipping" in caplog.text
    assert "0 match(es); skipping" in caplog.text


async def test_history_entry_without_pdfs_still_flips_the_sticky_tag() -> None:
    # A history row whose PDF never surfaced (or was dropped by the
    # allowlist) still tags — same as an N/A conversion row on the live page.
    bank = _erebor_bank()
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": [bank]})
    tagged, unmatched = await _tag_digital_asset_history_entries(
        object(), repository=repo, entries=[_erebor_history_entry(pdf_urls=())], apply=True
    )
    assert (tagged, unmatched) == (1, 0)
    assert bank.digital_assets is True
    assert bank.digital_asset_pdfs is None
    # No occ_control_number on the row -> received date must NOT backfill.
    assert bank.application_received_date is None


async def test_history_dry_run_counts_but_never_writes(caplog) -> None:
    bank = _erebor_bank()
    repo = _SeedStubRepository(by_name={"Erebor Bank, N.A.": [bank]})
    with caplog.at_level(logging.INFO, logger=_SEED_LOGGER):
        tagged, unmatched = await _tag_digital_asset_history_entries(
            object(), repository=repo, entries=[_erebor_history_entry()], apply=False
        )
    assert (tagged, unmatched) == (1, 0)
    assert bank.digital_assets is False
    assert bank.digital_asset_pdfs is None
    assert "would tag bank id=42" in caplog.text
