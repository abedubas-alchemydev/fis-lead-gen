"""Unit tests for ``BankRepository`` (``services/banks.py``).

SQL-shape assertions via the ``_StagedSession`` pattern (mirrors
``test_broker_dealers_range_filters.py``): compile the statements the
repository hands to ``db.execute`` and assert the expected predicates —
no live Postgres in the default suite. Pure-function coverage for the
forward-only status advance and the digital-assets tagging mutation.
"""

from __future__ import annotations

from datetime import date

import pytest
from unittest.mock import MagicMock

from app.models.bank import Bank
from app.services.banks import ALLOWED_SORT_FIELDS, BankRepository, _advance_status


class _StagedSession:
    """AsyncSession mock capturing statements; ``list_banks`` issues two
    execute() calls: count_stmt (scalar_one) then data_stmt (scalars.all)."""

    def __init__(self) -> None:
        self.executed_statements: list[object] = []
        self._call_count = 0

    async def execute(self, statement: object) -> object:
        self.executed_statements.append(statement)
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            result.scalar_one.return_value = 0
        else:
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        return result


def _compile_sql(statement: object) -> str:
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    return str(compiled).lower()


def _captured_where_sql(session: _StagedSession) -> str:
    assert len(session.executed_statements) >= 2
    sql = _compile_sql(session.executed_statements[1])
    return sql.split("where", 1)[1] if "where" in sql else ""


def _default_kwargs() -> dict[str, object]:
    return {
        "search": None,
        "states": [],
        "charter_authorities": [],
        "charter_statuses": [],
        "established_after": None,
        "established_before": None,
        "sort_by": "established_date",
        "sort_dir": "desc",
        "page": 1,
        "limit": 25,
    }


@pytest.fixture
def repository() -> BankRepository:
    return BankRepository()


async def test_no_filters_emits_no_where(repository: BankRepository) -> None:
    session = _StagedSession()
    await repository.list_banks(session, **_default_kwargs())
    assert _captured_where_sql(session) == ""


async def test_search_matches_name_city_and_identifiers(repository: BankRepository) -> None:
    session = _StagedSession()
    kwargs = _default_kwargs()
    kwargs["search"] = "Erebor"
    await repository.list_banks(session, **kwargs)
    where = _captured_where_sql(session)
    # Generic-dialect compile renders ilike as lower(...) like lower(...).
    assert "lower(banks.name) like lower('%erebor%')" in where
    assert "lower(banks.city) like" in where
    assert "banks.fdic_cert" in where
    assert "banks.occ_control_number" in where


async def test_enum_filters_emit_in_predicates(repository: BankRepository) -> None:
    session = _StagedSession()
    kwargs = _default_kwargs()
    kwargs.update(
        states=["NY", "FL"],
        charter_authorities=["OCC"],
        charter_statuses=["pending", "approved"],
    )
    await repository.list_banks(session, **kwargs)
    where = _captured_where_sql(session)
    assert "banks.state in ('ny', 'fl')" in where
    assert "banks.charter_authority in ('occ')" in where
    assert "banks.charter_status in ('pending', 'approved')" in where


async def test_established_window_emits_range(repository: BankRepository) -> None:
    session = _StagedSession()
    kwargs = _default_kwargs()
    kwargs.update(
        established_after=date(2026, 1, 1), established_before=date(2026, 7, 2)
    )
    await repository.list_banks(session, **kwargs)
    where = _captured_where_sql(session)
    assert "banks.established_date >= '2026-01-01'" in where
    assert "banks.established_date <= '2026-07-02'" in where


async def test_digital_assets_tri_state(repository: BankRepository) -> None:
    # None (default) -> no predicate.
    session = _StagedSession()
    await repository.list_banks(session, **_default_kwargs())
    assert "digital_assets" not in _captured_where_sql(session)
    # True -> IS true.
    session = _StagedSession()
    await repository.list_banks(session, **_default_kwargs(), digital_assets=True)
    assert "banks.digital_assets is true" in _captured_where_sql(session)
    # False -> IS false.
    session = _StagedSession()
    await repository.list_banks(session, **_default_kwargs(), digital_assets=False)
    assert "banks.digital_assets is false" in _captured_where_sql(session)


async def test_find_banks_by_occ_charter_number_filters_on_the_column(
    repository: BankRepository,
) -> None:
    # Strong-key lookup used by the watcher's digital-assets seed
    # (KNOWN_DIGITAL_ASSET_APPLICANTS): exact equality on
    # occ_charter_number, nothing fuzzy.
    session = _StagedSession()
    session_result = MagicMock()
    session_result.scalars.return_value.all.return_value = []

    async def execute(statement: object) -> object:
        session.executed_statements.append(statement)
        return session_result

    session.execute = execute  # type: ignore[method-assign]
    banks = await repository.find_banks_by_occ_charter_number(session, "25357")
    assert banks == []
    sql = _compile_sql(session.executed_statements[0])
    assert "banks.occ_charter_number = '25357'" in sql


async def test_unknown_sort_falls_back_to_established_date(repository: BankRepository) -> None:
    session = _StagedSession()
    kwargs = _default_kwargs()
    kwargs["sort_by"] = "drop table banks"  # not in the allowlist
    await repository.list_banks(session, **kwargs)
    sql = _compile_sql(session.executed_statements[1])
    assert "order by banks.established_date desc nulls last, banks.id asc" in sql


def test_sort_allowlist_covers_the_list_columns() -> None:
    for key in ("name", "state", "charter_status", "established_date", "asset"):
        assert key in ALLOWED_SORT_FIELDS


class TestAdvanceStatus:
    def test_forward_moves_apply(self) -> None:
        assert _advance_status("pending", "approved") == "approved"
        assert _advance_status("approved", "opened") == "opened"
        assert _advance_status(None, "pending") == "pending"

    def test_backward_moves_are_refused(self) -> None:
        # A stale window re-seeing 'Receipt' after Approved landed must not
        # demote the row.
        assert _advance_status("approved", "pending") is None
        assert _advance_status("opened", "approved") is None

    def test_unknown_incoming_is_ignored(self) -> None:
        assert _advance_status("approved", None) is None


class TestApplyDigitalAssetTag:
    def _bank(self, **overrides) -> Bank:
        defaults = dict(
            name="Catena Trust Bank, N.A.",
            charter_status="pending",
            digital_assets=False,
            digital_asset_pdfs=None,
            occ_control_number="2026-Charter-345000",
            application_received_date=None,
            source="occ",
        )
        defaults.update(overrides)
        return Bank(**defaults)

    def test_tags_and_attaches_pdf(self) -> None:
        repo = BankRepository()
        bank = self._bank()
        changed = repo.apply_digital_asset_tag(
            bank,
            pdf_url="https://www.occ.gov/x/catena.pdf",
            pdf_title="Catena Trust Bank, N.A.",
            received_date=date(2026, 5, 18),
        )
        assert changed is True
        assert bank.digital_assets is True
        assert bank.digital_asset_pdfs == [
            {
                "title": "Catena Trust Bank, N.A.",
                "url": "https://www.occ.gov/x/catena.pdf",
                "received_date": "2026-05-18",
            }
        ]
        # DA "Date received" backfills the missing Receipt date on OCC rows.
        assert bank.application_received_date == date(2026, 5, 18)

    def test_rerun_is_idempotent(self) -> None:
        repo = BankRepository()
        bank = self._bank()
        repo.apply_digital_asset_tag(
            bank, pdf_url="https://www.occ.gov/x/catena.pdf",
            pdf_title="Catena", received_date=date(2026, 5, 18),
        )
        changed_again = repo.apply_digital_asset_tag(
            bank, pdf_url="https://www.occ.gov/x/catena.pdf",
            pdf_title="Catena", received_date=date(2026, 5, 18),
        )
        assert changed_again is False
        assert len(bank.digital_asset_pdfs) == 1

    def test_never_overwrites_existing_received_date(self) -> None:
        repo = BankRepository()
        bank = self._bank(application_received_date=date(2026, 5, 1))
        repo.apply_digital_asset_tag(
            bank, pdf_url=None, pdf_title=None, received_date=date(2026, 5, 18)
        )
        assert bank.application_received_date == date(2026, 5, 1)

    def test_no_received_date_backfill_on_non_occ_rows(self) -> None:
        # An FDIC-only row (e.g. a conversion that matched by name) must not
        # gain an application_received_date it never had a filing for.
        repo = BankRepository()
        bank = self._bank(occ_control_number=None, source="fdic")
        repo.apply_digital_asset_tag(
            bank, pdf_url=None, pdf_title=None, received_date=date(2026, 5, 18)
        )
        assert bank.application_received_date is None
        assert bank.digital_assets is True

    def test_pdf_list_reassigned_not_mutated(self) -> None:
        # Plain JSONB columns don't track in-place mutation; the tag must
        # REPLACE the list object so SQLAlchemy sees the change.
        repo = BankRepository()
        original = [{"title": "First", "url": "https://www.occ.gov/a.pdf", "received_date": None}]
        bank = self._bank(digital_assets=True, digital_asset_pdfs=original)
        repo.apply_digital_asset_tag(
            bank, pdf_url="https://www.occ.gov/b.pdf", pdf_title="Second", received_date=None
        )
        assert bank.digital_asset_pdfs is not original
        assert [e["url"] for e in bank.digital_asset_pdfs] == [
            "https://www.occ.gov/a.pdf", "https://www.occ.gov/b.pdf",
        ]
