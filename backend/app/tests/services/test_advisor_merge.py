"""Pure-logic tests for the IAPD + 13F merge service."""

from __future__ import annotations

from datetime import date

from app.services.advisor_merge import AdvisorMergeService
from app.services.service_models import IapdAdvisorRecord


def _iapd_record(
    crd: str = "107218",
    name: str = "BlackRock",
    cik: str | None = "1364742",
    **overrides: object,
) -> IapdAdvisorRecord:
    base = dict(
        crd_number=crd,
        sec_file_number=None,
        cik=cik,
        name=name,
        legal_name=None,
        city=None,
        state=None,
        status=None,
        last_filing_date=None,
        website=None,
        discretionary_aum=None,
        non_discretionary_aum=None,
        regulatory_aum=None,
        total_clients=None,
        advisory_activities=[],
        client_types=[],
        client_counts={},
    )
    base.update(overrides)
    return IapdAdvisorRecord(**base)  # type: ignore[arg-type]


def test_flags_files_13f_when_cik_matches():
    iapd = [_iapd_record(crd="1", cik="1364742")]
    thirteen_f = {"1364742": date(2026, 2, 14)}
    merged, report = AdvisorMergeService().merge(iapd, thirteen_f)

    assert len(merged) == 1
    assert merged[0].files_13f is True
    assert merged[0].latest_13f_filing_date == date(2026, 2, 14)
    assert report.files_13f_count == 1
    assert report.cik_match_count == 1


def test_does_not_flag_files_13f_without_cik_match():
    iapd = [_iapd_record(crd="1", cik="9999")]
    thirteen_f = {"1364742": date(2026, 2, 14)}
    merged, _ = AdvisorMergeService().merge(iapd, thirteen_f)
    assert merged[0].files_13f is False
    assert merged[0].latest_13f_filing_date is None


def test_handles_iapd_record_with_no_cik():
    """Some IAPD-only firms have no CIK — they can't match 13F by definition."""
    iapd = [_iapd_record(crd="1", cik=None)]
    thirteen_f = {"1364742": date(2026, 2, 14)}
    merged, _ = AdvisorMergeService().merge(iapd, thirteen_f)
    assert merged[0].files_13f is False
    assert merged[0].cik is None


def test_normalizes_iapd_cik_against_efts_padding():
    """IAPD CSV has ``"1364742"``; EFTS yields ``"0001364742"``. The merge
    must normalize both sides before comparing — the join key in the
    13F dict has already been stripped by ``_normalize_cik``."""
    iapd = [_iapd_record(crd="1", cik="0001364742")]  # zero-padded on the IAPD side
    thirteen_f = {"1364742": date(2026, 2, 14)}  # post-normalization
    merged, _ = AdvisorMergeService().merge(iapd, thirteen_f)
    assert merged[0].files_13f is True
    assert merged[0].cik == "1364742"  # normalized in output


def test_drops_record_with_no_crd():
    iapd = [_iapd_record(crd="", name="Junk")]
    merged, report = AdvisorMergeService().merge(iapd, {})
    assert merged == []
    assert report.dropped_no_crd == 1


def test_drops_record_with_no_name():
    iapd = [_iapd_record(crd="1", name="")]
    merged, report = AdvisorMergeService().merge(iapd, {})
    assert merged == []
    assert report.dropped_no_name == 1


def test_default_status_when_iapd_status_blank():
    iapd = [_iapd_record(crd="1", status=None)]
    merged, _ = AdvisorMergeService().merge(iapd, {})
    assert merged[0].status == "active"


def test_status_lowercased():
    iapd = [_iapd_record(crd="1", status="Approved")]
    merged, _ = AdvisorMergeService().merge(iapd, {})
    assert merged[0].status == "approved"


def test_website_source_iapd_when_website_present():
    iapd = [_iapd_record(crd="1", website="https://example.com")]
    merged, _ = AdvisorMergeService().merge(iapd, {})
    assert merged[0].website_source == "iapd"


def test_website_source_none_when_website_absent():
    iapd = [_iapd_record(crd="1", website=None)]
    merged, _ = AdvisorMergeService().merge(iapd, {})
    assert merged[0].website_source is None


def test_report_aggregates_counts():
    iapd = [
        _iapd_record(crd="1", cik="100"),     # matched 13F
        _iapd_record(crd="2", cik="200"),     # not matched
        _iapd_record(crd="", name="bad"),     # dropped
    ]
    thirteen_f = {"100": date(2026, 1, 1)}
    merged, report = AdvisorMergeService().merge(iapd, thirteen_f)
    assert report.iapd_input_count == 3
    assert report.merged_count == 2
    assert report.files_13f_count == 1
    assert report.dropped_no_crd == 1
    assert len(merged) == 2
