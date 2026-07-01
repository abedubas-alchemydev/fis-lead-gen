"""Unit tests for the pure diff at the heart of the enumerate-and-diff
net-new broker-dealer extractor (``scripts/standalone_extract_new_bds.py``).

``select_new_bds`` is the only piece of that script worth unit-testing in
isolation: it has no I/O, decides exactly which enumerated FINRA firms get
ingested, and is the part the rewrite hinges on. The enumeration, enrichment,
EDGAR resolution, merge, and upsert it feeds are all already covered by the
service-level suites (``test_finra.py``, ``data_merge``, ``broker_dealers``).

The script lives at repo-root ``scripts/`` (outside the backend package the
pytest ``pythonpath`` points at), so we add the repo root to ``sys.path`` before
importing it. The module top-level is deliberately light — no ``app.*`` imports
at import time — so collecting this test never drags in the network services.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.services.service_models import FinraBrokerDealerRecord

# Repo root = backend/app/tests/services/<this file> → parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.standalone_extract_new_bds import select_new_bds  # noqa: E402


def _rec(crd: str | None) -> FinraBrokerDealerRecord:
    """Build a minimal enumerated FINRA record. Mirrors the constructor shape
    used by ``app/tests/services/test_finra.py``; only ``crd_number`` matters
    to the diff under test."""
    return FinraBrokerDealerRecord(
        crd_number=crd,  # type: ignore[arg-type]  # exercising null/empty defenses
        name=f"Firm {crd!r}",
        sec_file_number="8-12345",
        registration_status="Active",
        branch_count=1,
        address_city="New York",
        address_state="NY",
        business_type=None,
    )


def _crds(records: list[FinraBrokerDealerRecord]) -> list[str]:
    return [r.crd_number for r in records]


def test_select_new_bds_includes_new_crd() -> None:
    """A CRD absent from the DB is net-new and survives the diff."""
    enumerated = [_rec("100")]

    result = select_new_bds(enumerated, existing_crds=set())

    assert _crds(result) == ["100"]
    # Returns the original record objects (not copies) so downstream enrichment
    # mutates the same instances.
    assert result[0] is enumerated[0]


def test_select_new_bds_excludes_existing_crd() -> None:
    """A CRD already in ``broker_dealers`` is filtered out; only the truly
    net-new firm remains."""
    enumerated = [_rec("100"), _rec("200")]

    result = select_new_bds(enumerated, existing_crds={"200"})

    assert _crds(result) == ["100"]


def test_select_new_bds_excludes_null_and_empty_crd() -> None:
    """Records with a null or blank CRD can't be diffed/ingested and are
    dropped, even when nothing exists in the DB yet."""
    enumerated = [_rec(None), _rec(""), _rec("   "), _rec("300")]

    result = select_new_bds(enumerated, existing_crds=set())

    assert _crds(result) == ["300"]


def test_select_new_bds_dedups_by_crd() -> None:
    """A firm surfaced by more than one enumeration query (same CRD) is
    ingested once; first occurrence wins."""
    first = _rec("400")
    dup = _rec("400")
    enumerated = [first, dup, _rec("500")]

    result = select_new_bds(enumerated, existing_crds=set())

    assert _crds(result) == ["400", "500"]
    assert result[0] is first  # first occurrence kept, not the later duplicate


def test_select_new_bds_strips_whitespace_before_comparing() -> None:
    """CRDs are compared as stripped strings, so a padded enumerated CRD still
    matches an existing (already-stripped) DB CRD and is excluded."""
    result = select_new_bds([_rec("  600 ")], existing_crds={"600"})

    assert result == []


def test_select_new_bds_normalizes_crd_on_store() -> None:
    """A net-new CRD that arrives whitespace-padded is returned stripped, so it
    isn't persisted padded by the downstream enrich → merge → upsert path."""
    padded = _rec("  700 ")

    result = select_new_bds([padded], existing_crds=set())

    assert _crds(result) == ["700"]
    assert result[0].crd_number == "700"  # normalized in place


def test_select_new_bds_combined_ordering_and_filters() -> None:
    """End-to-end of the four behaviors at once, preserving input order."""
    enumerated = [
        _rec("100"),   # new            → kept
        _rec("200"),   # existing       → dropped
        _rec(""),      # empty          → dropped
        _rec(None),    # null           → dropped
        _rec("100"),   # dup of kept    → dropped
        _rec("300"),   # new            → kept
    ]

    result = select_new_bds(enumerated, existing_crds={"200"})

    assert _crds(result) == ["100", "300"]
