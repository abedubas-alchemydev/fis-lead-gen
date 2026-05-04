"""Unit tests for ``DeficiencyWatcherService._build_alert_records``.

The pure record-building step is the part worth locking with tests
without standing up an async DB session and a fake SEC EFTS server:
it's a deterministic transform from EFTS hit dicts + a CIK->bd_id index
to a list of FilingAlertRecord instances.

The async ``run`` method (which makes the EFTS HTTP call) is exercised
by the endpoint auth tests in ``test_pipeline.py`` plus a post-deploy
smoke check on staging.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.deficiency_watcher import DeficiencyWatcherService


def _hit(
    *,
    accession: str = "0001542278-24-000001",
    cik_padded: str = "0001542278",
    form: str = "X-17A-5",
    file_date: str = "2024-03-15",
    extra_ciks: list[str] | None = None,
) -> dict:
    """Build a synthetic EFTS hit shaped like the live response."""
    ciks = [cik_padded]
    if extra_ciks:
        ciks.extend(extra_ciks)
    return {
        "_source": {
            "adsh": accession,
            "ciks": ciks,
            "form": form,
            "file_date": file_date,
        }
    }


# Empty-input + missing-data guards


def test_returns_empty_list_when_no_hits() -> None:
    assert DeficiencyWatcherService._build_alert_records([], {}) == []


def test_skips_hit_without_ciks() -> None:
    bad = {"_source": {"adsh": "0001542278-24-000001", "form": "X-17A-5"}}
    assert DeficiencyWatcherService._build_alert_records([bad], {"1542278": 1}) == []


def test_skips_hit_with_empty_ciks_list() -> None:
    bad = {"_source": {"adsh": "0001542278-24-000001", "ciks": [], "form": "X-17A-5"}}
    assert DeficiencyWatcherService._build_alert_records([bad], {"1542278": 1}) == []


def test_skips_hit_without_accession() -> None:
    bad = {"_source": {"ciks": ["0001542278"], "form": "X-17A-5"}}
    assert DeficiencyWatcherService._build_alert_records([bad], {"1542278": 1}) == []


# CIK matching


def test_skips_hit_whose_cik_is_not_in_our_universe() -> None:
    """EFTS returns hits across all SEC filers; only those that map to
    a broker_dealer in our DB should produce alerts."""
    hits = [_hit(cik_padded="9999999999")]
    cik_index = {"1542278": 42}
    assert DeficiencyWatcherService._build_alert_records(hits, cik_index) == []


def test_strips_leading_zeros_from_efts_cik_when_matching() -> None:
    """EFTS pads CIKs to 10 digits; broker_dealers.cik stores them
    unpadded. The index join must strip leading zeros."""
    hits = [_hit(cik_padded="0001542278")]
    cik_index = {"1542278": 42}
    records = DeficiencyWatcherService._build_alert_records(hits, cik_index)
    assert len(records) == 1
    assert records[0].bd_id == 42


def test_zero_cik_edge_case_is_handled() -> None:
    """A CIK of '0' is technically valid (no SEC filer uses it but
    the lstrip('0') would leave an empty string; the watcher coerces
    that to '0' so the dict lookup behaves)."""
    hits = [_hit(cik_padded="0")]
    cik_index = {"0": 1}
    records = DeficiencyWatcherService._build_alert_records(hits, cik_index)
    assert len(records) == 1


# Record content contract


def test_emits_alert_with_form_17a11_form_type() -> None:
    """The existing alerts.py:74 'deficiencies' category filter
    matches on form_type == 'Form 17a-11'. This test locks the contract."""
    hits = [_hit()]
    cik_index = {"1542278": 42}
    record = DeficiencyWatcherService._build_alert_records(hits, cik_index)[0]
    assert record.form_type == "Form 17a-11"


def test_priority_is_critical() -> None:
    hits = [_hit()]
    record = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})[0]
    assert record.priority == "critical"


def test_filed_at_is_anchored_at_noon_utc_on_filing_date() -> None:
    hits = [_hit(file_date="2024-03-15")]
    record = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})[0]
    assert record.filed_at == datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_dedupe_key_combines_bd_id_and_accession() -> None:
    """Re-running the watcher must not double-insert. Accession numbers
    are globally unique on SEC EDGAR, so (bd_id, accession) is a stable
    dedupe key."""
    hits = [_hit(accession="0001542278-24-000001")]
    record = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})[0]
    assert record.dedupe_key == "Form 17a-11:42:0001542278-24-000001"


def test_summary_mentions_17a11_and_alternative_list() -> None:
    hits = [_hit()]
    summary = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})[0].summary
    assert "17a-11" in summary
    assert "Alternative List" in summary


def test_source_filing_url_points_at_edgar_browse() -> None:
    """The link gives the user a direct view of the firm's filings on
    EDGAR rather than a deep-link to the specific filing — easier to
    inspect surrounding context (when did they recover, did they file
    again)."""
    hits = [_hit(cik_padded="0001542278", form="X-17A-5")]
    record = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})[0]
    assert "browse-edgar" in record.source_filing_url
    assert "CIK=0001542278" in record.source_filing_url


# Multi-CIK and ordering


def test_dedupes_within_single_run() -> None:
    """If the same accession appears twice in EFTS results (rare but
    defensive), only one alert is emitted per (bd_id, accession)."""
    hits = [_hit(), _hit()]  # same accession + cik
    records = DeficiencyWatcherService._build_alert_records(hits, {"1542278": 42})
    assert len(records) == 1


def test_records_sorted_by_filed_at_descending() -> None:
    hits = [
        _hit(accession="A1", cik_padded="0000000001", file_date="2024-01-15"),
        _hit(accession="A2", cik_padded="0000000002", file_date="2024-04-15"),
        _hit(accession="A3", cik_padded="0000000003", file_date="2024-03-01"),
    ]
    cik_index = {"1": 1, "2": 2, "3": 3}
    records = DeficiencyWatcherService._build_alert_records(hits, cik_index)
    assert [r.bd_id for r in records] == [2, 3, 1]


def test_falls_back_to_today_when_file_date_missing_or_invalid() -> None:
    """A defensive fallback: if EFTS omits or malforms the file_date,
    we still emit the alert using today's date — operators can correct
    the timestamp manually rather than losing the signal."""
    bad = {
        "_source": {
            "adsh": "0001542278-24-000001",
            "ciks": ["0001542278"],
            "form": "X-17A-5",
            # no file_date
        }
    }
    record = DeficiencyWatcherService._build_alert_records([bad], {"1542278": 42})[0]
    assert record.filed_at.date() == date.today()
