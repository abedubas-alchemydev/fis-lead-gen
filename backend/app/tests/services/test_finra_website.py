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


def test_firm_bc_scope_url_is_not_used_as_fallback() -> None:
    """``firm_bc_scope_url`` is FINRA's pointer to the firm's own
    BrokerCheck profile/PDF — not a website the firm filed. It must not
    be persisted, otherwise it short-circuits the on-demand
    Apollo→Hunter→SerpAPI resolver (which only fires when website IS
    NULL). Regression guard for the 303 SECURITIES, LP class of bug
    where every Form-BD key was empty and the chain fell through to
    this self-reference."""
    record = _record()
    detail = {
        "firm_name": "303 Securities, LP",
        "firm_bc_scope_url": "https://files.brokercheck.finra.org/firm/firm_288569.pdf",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website is None
    assert record.website_source is None


def test_blocklisted_host_in_canonical_key_is_not_persisted() -> None:
    """Defensive guard: even when a brokercheck/finra/sec.gov URL lands in
    the Form-BD-canonical key (some firms file the BrokerCheck URL as
    their "Web Address"), we refuse to persist it. The on-demand
    resolver runs on first visit and sources a real website instead."""
    record = _record()
    detail = {
        "firm_name": "Acme",
        "firm_ia_main_web_address": "https://brokercheck.finra.org/firm/12345",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website is None
    assert record.website_source is None


def test_blocklisted_host_in_legacy_key_is_not_persisted() -> None:
    """Same guard applied to the legacy ``firm_website`` key."""
    record = _record()
    detail = {
        "firm_name": "Acme",
        "firm_website": "https://files.brokercheck.finra.org/firm/firm_99.pdf",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website is None
    assert record.website_source is None


def test_blocklisted_host_does_not_block_real_canonical_value() -> None:
    """When the canonical key holds a real website AND a legacy key holds
    a blocklisted URL, canonical still wins (and persists). The blocklist
    only filters the value that would otherwise be persisted; it doesn't
    cascade across keys."""
    record = _record()
    detail = {
        "firm_name": "Acme",
        "firm_ia_main_web_address": "https://acme.example.com",
        "firm_website": "https://brokercheck.finra.org/firm/12345",
    }

    FinraService()._apply_detail_to_record(record, detail)

    assert record.website == "https://acme.example.com"
    assert record.website_source == "finra"


@respx.mock
async def test_fetch_website_by_crd_returns_none_for_blocklisted_host() -> None:
    """Backfill helper mirrors the live writer: a BrokerCheck-self URL
    in the canonical key returns None so the caller's Apollo fallback
    kicks in instead of writing the FINRA URL."""
    respx.get("https://api.brokercheck.finra.org/search/firm/288569").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "firm_name": "303 Securities, LP",
                                "firm_ia_main_web_address": (
                                    "https://files.brokercheck.finra.org/firm/firm_288569.pdf"
                                ),
                            }
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        website = await FinraService().fetch_website_by_crd(client, "288569")

    assert website is None


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
