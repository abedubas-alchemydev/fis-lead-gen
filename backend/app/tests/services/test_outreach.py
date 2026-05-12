"""Service-level tests for ``services/outreach.py`` (Gemini Flash draft).

Mocking strategy mirrors ``test_clearing_classifier.py``: respx intercepts
all outbound HTTP, monkeypatch installs a syntactically valid Gemini key
so the per-call shape guard passes. ``asyncio.sleep`` is also patched out
in failure-mode tests so retry-backoff doesn't slow the suite.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.outreach import (
    ContactContext,
    FirmContext,
    OutreachConfigurationError,
    OutreachDraftError,
    ServiceContext,
    generate_outreach_draft,
)

_VALID_KEY = "AIzaSy" + "a" * 33  # 39 chars, matches ^AIzaSy[A-Za-z0-9_\-]{33}$
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)


@pytest.fixture
def patch_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a valid Gemini key + canonical base URL."""
    monkeypatch.setattr(settings, "gemini_api_key", _VALID_KEY)
    monkeypatch.setattr(
        settings, "gemini_api_base", "https://generativelanguage.googleapis.com/v1beta"
    )


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the inter-retry sleep so failure-mode tests are fast."""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.outreach.asyncio.sleep", _instant_sleep)


def _firm() -> FirmContext:
    return FirmContext(
        name="Acme Securities LLC",
        city="New York",
        state="NY",
        current_clearing_partner="Pershing",
        firm_operations_text="Provides retail brokerage services.",
    )


def _contact() -> ContactContext:
    return ContactContext(name="Jane Doe", title="Chief Operating Officer", email=None)


def _service() -> ServiceContext:
    return ServiceContext(
        name="Stock Loan",
        description="We offer competitive rebates on hard-to-borrow names.",
    )


def _gemini_response(*, subject: str, body: str) -> httpx.Response:
    """Build a mock Gemini response that mimics the candidates/parts shape."""
    structured = json.dumps({"subject": subject, "body": body})
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": structured}]}}]},
    )


# ── Happy path ──────────────────────────────────────────────────────────────


@respx.mock
async def test_generate_outreach_draft_returns_subject_and_body(patch_gemini) -> None:
    respx.post(_GEMINI_URL).mock(
        return_value=_gemini_response(
            subject="Cutting your stock-loan friction at Acme",
            body="Para 1\n\nPara 2\n\n- Sender",
        )
    )

    draft = await generate_outreach_draft(
        firm=_firm(), contact=_contact(), service=_service()
    )

    assert draft.subject == "Cutting your stock-loan friction at Acme"
    assert draft.body.startswith("Para 1")
    assert draft.body.endswith("- Sender")


# ── Configuration errors ────────────────────────────────────────────────────


async def test_missing_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)

    with pytest.raises(OutreachConfigurationError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )


async def test_malformed_key_shape_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "not-a-real-google-key")

    with pytest.raises(OutreachConfigurationError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )


# ── Provider-level errors ───────────────────────────────────────────────────


@respx.mock
async def test_gemini_500_after_retries_raises_draft_error(
    patch_gemini, no_backoff_sleep
) -> None:
    respx.post(_GEMINI_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(OutreachDraftError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )


@respx.mock
async def test_gemini_403_raises_draft_error_immediately(
    patch_gemini, no_backoff_sleep
) -> None:
    """Non-transient status (403) must NOT be retried — single call, immediate raise."""
    route = respx.post(_GEMINI_URL).mock(
        return_value=httpx.Response(403, text="forbidden")
    )

    with pytest.raises(OutreachDraftError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )

    assert route.call_count == 1


@respx.mock
async def test_gemini_returns_invalid_json_raises_draft_error(patch_gemini) -> None:
    respx.post(_GEMINI_URL).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "this is not json"}]}}]},
        )
    )

    with pytest.raises(OutreachDraftError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )


@respx.mock
async def test_gemini_returns_empty_subject_raises_draft_error(patch_gemini) -> None:
    respx.post(_GEMINI_URL).mock(
        return_value=_gemini_response(subject="   ", body="A real body.")
    )

    with pytest.raises(OutreachDraftError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )


@respx.mock
async def test_gemini_returns_empty_body_raises_draft_error(patch_gemini) -> None:
    respx.post(_GEMINI_URL).mock(
        return_value=_gemini_response(subject="Real subject", body="")
    )

    with pytest.raises(OutreachDraftError):
        await generate_outreach_draft(
            firm=_firm(), contact=_contact(), service=_service()
        )
