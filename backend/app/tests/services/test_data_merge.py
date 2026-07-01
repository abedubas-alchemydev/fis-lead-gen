"""Service-contract tests for ``BrokerDealerMergeService.merge``.

Pins the EDGAR/FINRA merge contract that both the ``scripts.initial_load``
script and the ``_run_initial_load_background`` API call site depend on:

* ``merge(edgar_records, finra_records)`` — EDGAR is the FIRST positional
  argument, FINRA the SECOND, and the method returns a
  ``(records, report)`` tuple. The initial-load background task once called
  this with the arguments INVERTED and never unpacked the tuple, so the
  scheduled run never inserted a single broker-dealer.
* A FINRA row that matches an EDGAR row on normalized SEC file number is
  emitted exactly once, classified ``matched_source == "both"``, carrying
  the CIK from EDGAR and the CRD from FINRA.
"""

from __future__ import annotations

import pytest

from app.services.data_merge import BrokerDealerMergeService
from app.services.service_models import (
    EdgarBrokerDealerRecord,
    FinraBrokerDealerRecord,
)


def _edgar_record(
    *,
    cik: str = "0001234567",
    name: str = "Acme Securities LLC",
    sec_file_number: str | None = "8-12345",
    state: str | None = "NY",
    city: str | None = "New York",
) -> EdgarBrokerDealerRecord:
    return EdgarBrokerDealerRecord(
        cik=cik,
        name=name,
        sic="6211",
        state=state,
        city=city,
        sec_file_number=sec_file_number,
        registration_date=None,
        last_filing_date=None,
        filings_index_url="https://www.sec.gov/cgi-bin/browse-edgar",
    )


def _finra_record(
    *,
    crd_number: str = "111",
    name: str = "Acme Securities LLC",
    sec_file_number: str | None = "8-12345",
    registration_status: str = "Active",
    address_state: str | None = "NY",
    address_city: str | None = "New York",
) -> FinraBrokerDealerRecord:
    return FinraBrokerDealerRecord(
        crd_number=crd_number,
        name=name,
        sec_file_number=sec_file_number,
        registration_status=registration_status,
        branch_count=1,
        address_city=address_city,
        address_state=address_state,
        business_type=None,
    )


def test_matches_edgar_and_finra_on_sec_file_number() -> None:
    """One active FINRA firm sharing a SEC file number with one EDGAR firm
    merges into a single ``matched_source == "both"`` row, EDGAR CIK + FINRA
    CRD carried through."""
    edgar = [_edgar_record()]
    finra = [_finra_record()]

    merged, report = BrokerDealerMergeService().merge(edgar, finra)

    assert len(merged) == 1
    row = merged[0]
    assert row.matched_source == "both"
    assert row.sec_file_number == "8-12345"
    assert row.cik == "0001234567"  # carried from EDGAR
    assert row.crd_number == "111"  # carried from FINRA
    assert report.matched_both_count == 1
    assert report.output_count == 1


def test_merge_arg_order_is_edgar_then_finra() -> None:
    """Guard the positional contract: EDGAR first, FINRA second.

    Inverting the arguments feeds an ``EdgarBrokerDealerRecord`` into the
    FINRA loop, which immediately reads ``.registration_status`` — a field
    EDGAR records don't have — and raises. The initial-load call site shipped
    exactly this inversion; pinning it here keeps a refactor from silently
    swapping the order back.
    """
    edgar = [_edgar_record()]
    finra = [_finra_record()]

    with pytest.raises(AttributeError):
        BrokerDealerMergeService().merge(finra, edgar)  # WRONG order on purpose
