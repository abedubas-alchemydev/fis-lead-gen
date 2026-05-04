"""Unit tests for ``RegistrationWatcherService._build_alert_records``.

The pure record-building step is the part worth locking with tests
without standing up an async DB session: it's a deterministic transform
from a list of broker_dealers to a list of FilingAlertRecord instances.

The async ``run`` method is exercised by an integration-shaped test in
``test_pipeline.py`` (auth + endpoint plumbing), and end-to-end via the
post-deploy smoke check on staging.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from types import SimpleNamespace

from app.services.registration_watcher import RegistrationWatcherService


def _bd(
    *,
    bd_id: int,
    registration_date: date | None,
    filings_index_url: str | None = "https://data.sec.gov/submissions/CIK0001234567.json",
) -> SimpleNamespace:
    """Minimal duck-typed broker_dealer for the record builder.

    The builder only reads ``id``, ``registration_date``, and
    ``filings_index_url`` so we don't need a real ORM instance.
    """
    return SimpleNamespace(
        id=bd_id,
        registration_date=registration_date,
        filings_index_url=filings_index_url,
    )


def test_returns_empty_list_when_no_broker_dealers() -> None:
    assert RegistrationWatcherService._build_alert_records([]) == []


def test_skips_broker_dealers_with_null_registration_date() -> None:
    bds = [_bd(bd_id=1, registration_date=None)]
    assert RegistrationWatcherService._build_alert_records(bds) == []


def test_emits_one_record_per_recently_registered_firm() -> None:
    bds = [
        _bd(bd_id=1, registration_date=date(2026, 4, 15)),
        _bd(bd_id=2, registration_date=date(2026, 4, 20)),
    ]
    records = RegistrationWatcherService._build_alert_records(bds)
    assert len(records) == 2
    assert {r.bd_id for r in records} == {1, 2}


def test_record_form_type_is_form_bd() -> None:
    """The existing alerts.py 'registrations' category filters on
    form_type == 'Form BD'. This test locks the contract so a future
    rename can't silently break that category."""
    bds = [_bd(bd_id=42, registration_date=date(2026, 4, 15))]
    record = RegistrationWatcherService._build_alert_records(bds)[0]
    assert record.form_type == "Form BD"


def test_record_priority_is_high() -> None:
    bds = [_bd(bd_id=1, registration_date=date(2026, 4, 15))]
    assert RegistrationWatcherService._build_alert_records(bds)[0].priority == "high"


def test_record_filed_at_is_anchored_at_noon_utc() -> None:
    """Anchoring at noon UTC keeps timezone math stable across the
    daily cron boundary — the alert's calendar day matches the firm's
    registration_date regardless of the server's local zone."""
    bds = [_bd(bd_id=1, registration_date=date(2026, 4, 15))]
    record = RegistrationWatcherService._build_alert_records(bds)[0]
    assert record.filed_at == datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_dedupe_key_is_keyed_on_id_plus_registration_date() -> None:
    """Re-running the watcher must not double-insert. The dedupe_key
    pattern matches the pre-2026-05-04 daily_filing_monitor shape so any
    historical Form BD alerts in the table use the same key space."""
    bds = [_bd(bd_id=42, registration_date=date(2026, 4, 15))]
    record = RegistrationWatcherService._build_alert_records(bds)[0]
    assert record.dedupe_key == "Form BD:42:2026-04-15"


def test_summary_mentions_registration_and_routes_to_registrations_tab() -> None:
    bds = [_bd(bd_id=1, registration_date=date(2026, 4, 15))]
    summary = RegistrationWatcherService._build_alert_records(bds)[0].summary
    assert "registration" in summary.lower()
    assert "registrations tab" in summary.lower()


def test_records_sorted_by_filed_at_descending() -> None:
    """Newest registrations should appear first in the result set."""
    bds = [
        _bd(bd_id=1, registration_date=date(2026, 1, 5)),
        _bd(bd_id=2, registration_date=date(2026, 4, 15)),
        _bd(bd_id=3, registration_date=date(2026, 3, 1)),
    ]
    records = RegistrationWatcherService._build_alert_records(bds)
    bd_ids_in_order = [r.bd_id for r in records]
    assert bd_ids_in_order == [2, 3, 1]


def test_source_filing_url_passes_through_when_set() -> None:
    bds = [
        _bd(
            bd_id=1,
            registration_date=date(2026, 4, 15),
            filings_index_url="https://data.sec.gov/submissions/CIK0001234567.json",
        )
    ]
    record = RegistrationWatcherService._build_alert_records(bds)[0]
    assert record.source_filing_url == "https://data.sec.gov/submissions/CIK0001234567.json"


def test_source_filing_url_none_passes_through_when_unset() -> None:
    """A FINRA-only firm without an EDGAR submissions index still
    qualifies — the alert just doesn't link out to a filing index."""
    bds = [_bd(bd_id=1, registration_date=date(2026, 4, 15), filings_index_url=None)]
    assert RegistrationWatcherService._build_alert_records(bds)[0].source_filing_url is None
