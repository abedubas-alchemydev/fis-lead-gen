"""Tests for ``BrokerDealerMergeService.apply_apollo_website_fallback``.

Locks three properties of the post-merge fallback:

* FINRA-sourced websites win — Apollo is NOT called for firms that
  already have a value, so we don't burn Apollo spend on a firm we
  already know.
* Apollo organizations is called for firms FINRA missed, and a hit
  stamps ``website`` + ``website_source = 'apollo'`` so the FE source
  badge and the master-list filter both see the upstream provenance.
* ``ApolloError`` (5xx / 429-after-retries / network) is the
  provider-error path: log + count + leave the row's website NULL so
  the next pipeline run retries naturally instead of caching the
  empty result.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.services.apollo import ApolloClient, ApolloError, ApolloOrganization
from app.services.data_merge import BrokerDealerMergeService
from app.services.service_models import MergedBrokerDealerRecord


def _record(
    *,
    name: str,
    crd_number: str | None = "123456",
    website: str | None = None,
    website_source: str | None = None,
) -> MergedBrokerDealerRecord:
    return MergedBrokerDealerRecord(
        cik=None,
        crd_number=crd_number,
        sec_file_number="8-12345",
        name=name,
        city="New York",
        state="NY",
        status="Active",
        branch_count=1,
        business_type=None,
        registration_date=None,
        matched_source="finra_only",
        last_filing_date=None,
        filings_index_url=None,
        website=website,
        website_source=website_source,
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the inter-call sleep so the suite runs without delay."""
    import asyncio

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


async def test_finra_website_skips_apollo_call(no_sleep: None) -> None:
    """When FINRA already populated ``website``, Apollo MUST NOT be hit.
    Each Apollo call costs spend; re-asking for a value we already have
    is the easy regression to introduce when wiring fallbacks."""
    record = _record(
        name="Acme Securities LLC",
        website="https://acme-securities.example.com",
        website_source="finra",
    )
    apollo_client = AsyncMock(spec=ApolloClient)

    counts = await BrokerDealerMergeService().apply_apollo_website_fallback(
        [record], cast(ApolloClient, apollo_client), delay_s=0
    )

    apollo_client.search_organization.assert_not_called()
    assert record.website == "https://acme-securities.example.com"
    assert record.website_source == "finra"
    assert counts == {"apollo_filled": 0, "apollo_no_match": 0, "apollo_error": 0}


async def test_apollo_called_when_finra_missing(no_sleep: None) -> None:
    """For firms FINRA missed, Apollo is called and a hit stamps
    ``website`` + ``website_source='apollo'``."""
    record = _record(name="Bravo Capital LLC")
    apollo_client = AsyncMock(spec=ApolloClient)
    apollo_client.search_organization.return_value = ApolloOrganization(
        name="Bravo Capital LLC",
        website_url="https://bravo.example.com",
        domain="bravo.example.com",
    )

    counts = await BrokerDealerMergeService().apply_apollo_website_fallback(
        [record], cast(ApolloClient, apollo_client), delay_s=0
    )

    apollo_client.search_organization.assert_awaited_once_with(
        "Bravo Capital LLC", "123456"
    )
    assert record.website == "https://bravo.example.com"
    assert record.website_source == "apollo"
    assert counts["apollo_filled"] == 1
    assert counts["apollo_no_match"] == 0
    assert counts["apollo_error"] == 0


async def test_apollo_no_match_leaves_website_null(no_sleep: None) -> None:
    """Apollo returning None is the normal "we don't know this firm"
    path — the row's website stays NULL so the FE renders the
    "Search Google for this firm" widget."""
    record = _record(name="Charlie Holdings LLC")
    apollo_client = AsyncMock(spec=ApolloClient)
    apollo_client.search_organization.return_value = None

    counts = await BrokerDealerMergeService().apply_apollo_website_fallback(
        [record], cast(ApolloClient, apollo_client), delay_s=0
    )

    assert record.website is None
    assert record.website_source is None
    assert counts["apollo_no_match"] == 1
    assert counts["apollo_filled"] == 0
    assert counts["apollo_error"] == 0


async def test_apollo_error_logged_and_counted(
    no_sleep: None, caplog: pytest.LogCaptureFixture
) -> None:
    """ApolloError (5xx / 429-after-retries / network) is logged with
    the ``apollo_org_lookup_failed`` reason marker and counted, but the
    row's website stays NULL so the next pipeline run retries."""
    record = _record(name="Delta Partners LLC")
    apollo_client = AsyncMock(spec=ApolloClient)
    apollo_client.search_organization.side_effect = ApolloError(
        "Apollo organizations returned 503"
    )

    with caplog.at_level("WARNING"):
        counts = await BrokerDealerMergeService().apply_apollo_website_fallback(
            [record], cast(ApolloClient, apollo_client), delay_s=0
        )

    assert record.website is None
    assert record.website_source is None
    assert counts["apollo_error"] == 1
    assert any(
        "apollo_org_lookup_failed" in record_msg.getMessage()
        for record_msg in caplog.records
    )


async def test_one_record_error_does_not_stop_the_pass(no_sleep: None) -> None:
    """A single ApolloError must not abort the whole merge run — the
    next firm in the list still gets its Apollo lookup."""
    error_record = _record(name="Echo Brokers LLC", crd_number="111")
    success_record = _record(name="Foxtrot LLC", crd_number="222")

    apollo_client = AsyncMock(spec=ApolloClient)
    apollo_client.search_organization.side_effect = [
        ApolloError("Apollo organizations returned 503"),
        ApolloOrganization(
            name="Foxtrot LLC",
            website_url="https://foxtrot.example.com",
            domain="foxtrot.example.com",
        ),
    ]

    counts = await BrokerDealerMergeService().apply_apollo_website_fallback(
        [error_record, success_record],
        cast(ApolloClient, apollo_client),
        delay_s=0,
    )

    assert error_record.website is None
    assert success_record.website == "https://foxtrot.example.com"
    assert success_record.website_source == "apollo"
    assert counts["apollo_error"] == 1
    assert counts["apollo_filled"] == 1
