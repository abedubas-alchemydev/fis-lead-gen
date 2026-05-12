"""Unit tests for the IAPD Compilation Report parser.

Doesn't hit the network — exercises ``IapdService._parse_compilation_zip``
on a synthetic in-memory ZIP that mirrors the real SEC schema (column
names locked from the May 2026 ``ia050126.zip`` snapshot). The fixture
covers the parser's defensive paths:

- Required-field guards (no CRD → row skipped; no name → row skipped)
- AUM with thousands separators / dollar signs
- Date parsing across MM/DD/YYYY (canonical SEC format)
- Empty-cell handling (CIK absent, AUM absent)
- Truthy/falsy flag detection ("Y"/"yes"/" Y "/etc.)
- Per-type client counts (5D(2) sibling cells)
- Header look-up by name (positional drift wouldn't break the parser
  if the SEC reorders columns mid-year)
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from app.services.iapd import IapdService


# Minimal subset of the real 448-column header — enough columns to
# exercise every code path in the parser. Order is intentionally
# DIFFERENT from the SEC's column order to prove header lookup works
# regardless of position.
_HEADER = [
    "Primary Business Name",         # NAME (required)
    "Organization CRD#",             # CRD (required)
    "Legal Name",
    "SEC#",
    "CIK#",
    "Main Office City",
    "Main Office State",
    "SEC Current Status",
    "Latest ADV Filing Date",
    "Website Address",
    "5F(2)(a)",                      # discretionary AUM
    "5F(2)(b)",                      # non-discretionary AUM
    "5F(2)(c)",                      # total RAUM
    "5F(2)(f)",                      # total clients
    "5G(1)",                         # advisory: financial planning
    "5G(2)",                         # advisory: PM individuals
    "5G(7)",                         # advisory: selection of other advisers
    "5D(1)(a)",                      # client type: individuals (yes/no)
    "5D(2)(a)",                      # client type: individuals (count)
    "5D(1)(b)",                      # client type: HNW (yes/no)
    "5D(2)(b)",                      # client type: HNW (count)
]


def _build_zip(rows: list[dict[str, str]]) -> bytes:
    """Build a single-CSV ZIP matching the IAPD bulk format."""
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=_HEADER)
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = csv_buf.getvalue().encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_TEST.CSV", csv_bytes)
    return zip_buf.getvalue()


@pytest.fixture
def temp_zip(tmp_path: Path):
    """Materialize a ZIP file on disk for the parser to read."""

    def _make(rows: list[dict[str, str]]) -> Path:
        zip_path = tmp_path / "iapd.zip"
        zip_path.write_bytes(_build_zip(rows))
        return zip_path

    return _make


def test_parses_complete_row(temp_zip):
    rows = [
        {
            "Primary Business Name": "BlackRock Fund Advisors",
            "Organization CRD#": "107218",
            "Legal Name": "BlackRock Fund Advisors LLC",
            "SEC#": "801-56972",
            "CIK#": "1364742",
            "Main Office City": "San Francisco",
            "Main Office State": "CA",
            "SEC Current Status": "Approved",
            "Latest ADV Filing Date": "03/31/2025",
            "Website Address": "https://www.blackrock.com",
            "5F(2)(a)": "$3,500,000,000,000",
            "5F(2)(b)": "$1,000,000,000,000",
            "5F(2)(c)": "$4,500,000,000,000",
            "5F(2)(f)": "1,250",
            "5G(1)": "Y",
            "5G(2)": "Y",
            "5G(7)": "N",
            "5D(1)(a)": "Y",
            "5D(2)(a)": "850",
            "5D(1)(b)": "Y",
            "5D(2)(b)": "400",
        }
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=None)
    assert len(records) == 1
    rec = records[0]
    assert rec.crd_number == "107218"
    assert rec.name == "BlackRock Fund Advisors"
    assert rec.legal_name == "BlackRock Fund Advisors LLC"
    assert rec.cik == "1364742"
    assert rec.sec_file_number == "801-56972"
    assert rec.city == "San Francisco"
    assert rec.state == "CA"
    assert rec.status == "Approved"
    assert rec.last_filing_date == date(2025, 3, 31)
    assert rec.website == "https://www.blackrock.com"
    assert rec.regulatory_aum == 4_500_000_000_000.0
    assert rec.discretionary_aum == 3_500_000_000_000.0
    assert rec.non_discretionary_aum == 1_000_000_000_000.0
    assert rec.total_clients == 1250
    assert "financial_planning_services" in rec.advisory_activities
    assert "portfolio_management_individuals_or_small_businesses" in rec.advisory_activities
    assert "selection_of_other_advisers" not in rec.advisory_activities  # 5G(7) was N
    assert "individuals" in rec.client_types
    assert "high_net_worth_individuals" in rec.client_types
    assert rec.client_counts == {"individuals": 850, "high_net_worth_individuals": 400}


def test_skips_row_with_no_crd(temp_zip):
    """No CRD ⇒ defensive skip rather than insert a junk row."""
    rows = [
        {
            "Primary Business Name": "Junk Firm",
            "Organization CRD#": "",
            "Legal Name": "Junk LLC",
            "SEC#": "",
            "CIK#": "",
            "Main Office City": "",
            "Main Office State": "",
            "SEC Current Status": "",
            "Latest ADV Filing Date": "",
            "Website Address": "",
            "5F(2)(a)": "",
            "5F(2)(b)": "",
            "5F(2)(c)": "",
            "5F(2)(f)": "",
            "5G(1)": "",
            "5G(2)": "",
            "5G(7)": "",
            "5D(1)(a)": "",
            "5D(2)(a)": "",
            "5D(1)(b)": "",
            "5D(2)(b)": "",
        }
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=None)
    assert records == []


def test_skips_row_with_no_name(temp_zip):
    """No Primary Business Name ⇒ defensive skip."""
    rows = [
        {
            "Primary Business Name": "",
            "Organization CRD#": "999999",
            "Legal Name": "",
            "SEC#": "",
            "CIK#": "",
            "Main Office City": "",
            "Main Office State": "",
            "SEC Current Status": "",
            "Latest ADV Filing Date": "",
            "Website Address": "",
            "5F(2)(a)": "",
            "5F(2)(b)": "",
            "5F(2)(c)": "",
            "5F(2)(f)": "",
            "5G(1)": "",
            "5G(2)": "",
            "5G(7)": "",
            "5D(1)(a)": "",
            "5D(2)(a)": "",
            "5D(1)(b)": "",
            "5D(2)(b)": "",
        }
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=None)
    assert records == []


def test_handles_empty_optional_cells(temp_zip):
    """Empty AUM / CIK / activity cells parse as None / [] rather than crash."""
    rows = [
        {
            "Primary Business Name": "Tiny Adviser LLC",
            "Organization CRD#": "999",
            "Legal Name": "",
            "SEC#": "",
            "CIK#": "",
            "Main Office City": "",
            "Main Office State": "",
            "SEC Current Status": "Approved",
            "Latest ADV Filing Date": "",
            "Website Address": "",
            "5F(2)(a)": "",
            "5F(2)(b)": "",
            "5F(2)(c)": "",
            "5F(2)(f)": "",
            "5G(1)": "",
            "5G(2)": "",
            "5G(7)": "",
            "5D(1)(a)": "",
            "5D(2)(a)": "",
            "5D(1)(b)": "",
            "5D(2)(b)": "",
        }
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=None)
    assert len(records) == 1
    rec = records[0]
    assert rec.crd_number == "999"
    assert rec.name == "Tiny Adviser LLC"
    assert rec.cik is None
    assert rec.regulatory_aum is None
    assert rec.advisory_activities == []
    assert rec.client_types == []
    assert rec.client_counts == {}


def test_truthy_flag_variants(temp_zip):
    """Y, yes, true, 1, with trailing space — all truthy. N / blank — falsy."""
    rows = [
        {
            "Primary Business Name": "Variants Test LLC",
            "Organization CRD#": "1",
            "Legal Name": "",
            "SEC#": "",
            "CIK#": "",
            "Main Office City": "",
            "Main Office State": "",
            "SEC Current Status": "",
            "Latest ADV Filing Date": "",
            "Website Address": "",
            "5F(2)(a)": "",
            "5F(2)(b)": "",
            "5F(2)(c)": "",
            "5F(2)(f)": "",
            "5G(1)": "yes",
            "5G(2)": " Y ",
            "5G(7)": "n",
            "5D(1)(a)": "1",
            "5D(2)(a)": "5",
            "5D(1)(b)": "true",
            "5D(2)(b)": "10",
        }
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=None)
    assert records[0].advisory_activities == [
        "financial_planning_services",
        "portfolio_management_individuals_or_small_businesses",
    ]
    assert records[0].client_types == ["individuals", "high_net_worth_individuals"]


def test_limit_truncates_records(temp_zip):
    rows = [
        {
            "Primary Business Name": f"Firm {i}",
            "Organization CRD#": str(100 + i),
            "Legal Name": "",
            "SEC#": "",
            "CIK#": "",
            "Main Office City": "",
            "Main Office State": "",
            "SEC Current Status": "",
            "Latest ADV Filing Date": "",
            "Website Address": "",
            "5F(2)(a)": "",
            "5F(2)(b)": "",
            "5F(2)(c)": "",
            "5F(2)(f)": "",
            "5G(1)": "",
            "5G(2)": "",
            "5G(7)": "",
            "5D(1)(a)": "",
            "5D(2)(a)": "",
            "5D(1)(b)": "",
            "5D(2)(b)": "",
        }
        for i in range(5)
    ]
    records = IapdService()._parse_compilation_zip(temp_zip(rows), limit=3)
    assert len(records) == 3


def test_missing_required_column_raises(tmp_path):
    """If the SEC removes ``Organization CRD#``, fail loudly at parse time
    rather than insert thousands of CRD-less rows."""
    csv_buf = io.StringIO()
    csv_buf.write("Primary Business Name\nFirm A\n")
    zip_path = tmp_path / "broken.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("data.csv", csv_buf.getvalue().encode("utf-8"))
    with pytest.raises(RuntimeError, match="Organization CRD#"):
        IapdService()._parse_compilation_zip(zip_path, limit=None)
