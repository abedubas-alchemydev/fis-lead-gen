"""Tests for the FINRA Form BD "Web Address" pluck.

Two surfaces under test:

* ``FinraService._apply_detail_to_record`` — the live extractor used by
  ``FinraService.enrich_with_detail`` during initial-load. Verifies the
  Form-BD-canonical key ``firm_ia_main_web_address`` is plucked and
  stamped, with documented fallback keys in priority order, and that
  ``record.website_source`` is set to ``"finra"`` so downstream merge
  / persistence layers can disclose source.
* ``FinraService.fetch_website_by_crd`` — the public single-firm helper
  the backfill script uses. Locks the same key list as the live path so
  the backfill and live ingestion never disagree on which keys to read.
"""

from __future__ import annotations

import httpx
import respx

from app.services.finra import FinraService
from app.services.service_models import FinraBrokerDealerRecord


def _record(crd: str = "123456") -> FinraBrokerDealerRecord:
    return FinraBrokerDealerRecord(
        crd_number=crd,
        name="Acme Securities LLC",
        sec_file_number="8-12345",
        registration_status="Active",
        branch_count=1,
        address_city="New York",
        address_state="NY",
        business_type=None,
    )


def test_form_bd_canonical_key_is_plucked() -> None:
    record = _record()
    detail = {
        "firm_name": "Acme Securities LLC",
        "firm_ia_main_web_address": "https://acme-securities.example.com",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website == "https://acme-securities.example.com"
    assert record.website_source == "finra"


def test_legacy_firm_website_key_is_plucked_when_canonical_missing() -> None:
    """Some BrokerCheck responses ship the legacy ``firm_website`` key
    instead of the Form-BD canonical one. Older firms live on this path."""
    record = _record()
    detail = {"firm_name": "Acme", "firm_website": "https://acme.example.com"}

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website == "https://acme.example.com"
    assert record.website_source == "finra"


def test_canonical_key_wins_over_legacy_keys() -> None:
    """When BrokerCheck returns both, the Form-BD canonical key wins —
    it's the authoritative Form BD field; the others are scope/listing
    URLs that sometimes drift away from the firm's real website."""
    record = _record()
    detail = {
        "firm_name": "Acme",
        "firm_ia_main_web_address": "https://canonical.example.com",
        "firm_website": "https://legacy.example.com",
        "firm_bc_scope_url": "https://brokercheck.finra.org/firm/12345",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website == "https://canonical.example.com"


def test_no_web_address_leaves_website_null() -> None:
    """When BrokerCheck has no web address on file we leave the record's
    website None so the Apollo organizations fallback kicks in later."""
    record = _record()
    detail = {"firm_name": "Acme"}  # no web address keys

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website is None
    assert record.website_source is None


def test_empty_string_web_address_treated_as_missing() -> None:
    """Some firms return an empty string for the Form BD field. That's
    not a website — leave the record None so Apollo can take a swing."""
    record = _record()
    detail = {
        "firm_name": "Acme",
        "firm_ia_main_web_address": "   ",  # whitespace-only
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website is None
    assert record.website_source is None


@respx.mock
async def test_fetch_website_by_crd_returns_canonical_value() -> None:
    """Public single-firm helper used by the backfill script. Hits the
    BrokerCheck detail endpoint and returns the Web Address."""
    respx.get("https://api.brokercheck.finra.org/search/firm/123456").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "firm_name": "Acme Securities LLC",
                                "firm_ia_main_web_address": "https://acme.example.com",
                            }
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        website = await FinraService().fetch_website_by_crd(client, "123456")

    assert website == "https://acme.example.com"


@respx.mock
async def test_fetch_website_by_crd_returns_none_on_404() -> None:
    """A 404 from BrokerCheck means the CRD isn't on file. The backfill
    treats that as "FINRA has nothing" and falls through to Apollo —
    don't raise, return None."""
    respx.get("https://api.brokercheck.finra.org/search/firm/999999").mock(
        return_value=httpx.Response(404)
    )

    async with httpx.AsyncClient() as client:
        website = await FinraService().fetch_website_by_crd(client, "999999")

    assert website is None
