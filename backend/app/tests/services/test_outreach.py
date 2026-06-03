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
    _build_adhoc_prompt,
    _build_personalized_prompt,
    generate_outreach_draft,
    optimize_instructions,
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


# ── Prompt builders ─────────────────────────────────────────────────────────


def _service_with_extras() -> ServiceContext:
    return ServiceContext(
        name="Stock Loan",
        description="We offer competitive rebates on hard-to-borrow names.",
        instructions="Always mention our 24/7 desk.",
        retrieved_chunks=("Excerpt A about rebates.", "Excerpt B about coverage."),
    )


def test_personalized_prompt_includes_firm_and_contact_blocks() -> None:
    prompt = _build_personalized_prompt(
        firm=_firm(), contact=_contact(), service=_service_with_extras()
    )

    assert "── Recipient ──" in prompt
    assert "Name: Jane Doe" in prompt
    assert "Title: Chief Operating Officer" in prompt
    assert "Firm: Acme Securities LLC" in prompt
    assert "Location: New York, NY" in prompt
    assert "They currently use Pershing as their clearing partner." in prompt
    assert "Firm operations text (raw, possibly noisy): Provides retail brokerage services." in prompt
    assert "Reference one concrete detail about the recipient's firm" in prompt
    assert "── Service the user is pitching ──" in prompt
    assert "Service name: Stock Loan" in prompt
    assert "Always mention our 24/7 desk." in prompt
    assert "Excerpt A about rebates." in prompt
    assert "Excerpt B about coverage." in prompt


def test_personalized_prompt_handles_missing_firm_fields() -> None:
    """No city/state, no clearing partner, no firm_operations_text — fallbacks fire."""
    firm = FirmContext(
        name="Lone LLC",
        city=None,
        state=None,
        current_clearing_partner=None,
        firm_operations_text=None,
    )
    prompt = _build_personalized_prompt(
        firm=firm, contact=_contact(), service=_service()
    )

    assert "Location: an unspecified location" in prompt
    assert "Their current clearing partner is unknown." in prompt
    assert "(no firm operations text on file)" in prompt


def test_adhoc_prompt_uses_name_when_provided() -> None:
    prompt = _build_adhoc_prompt(
        recipient_name="Sarah", service=_service_with_extras()
    )

    assert "Name: Sarah" in prompt
    assert "── Recipient ──" in prompt
    assert "(No firm context available" in prompt


def test_adhoc_prompt_defaults_to_there_without_name() -> None:
    prompt_none = _build_adhoc_prompt(recipient_name=None, service=_service())
    prompt_blank = _build_adhoc_prompt(recipient_name="   ", service=_service())

    assert "Name: there" in prompt_none
    assert "Name: there" in prompt_blank


def test_adhoc_prompt_omits_firm_block() -> None:
    """Adhoc must not surface firm-related fields the LLM could hallucinate from."""
    prompt = _build_adhoc_prompt(
        recipient_name="Sarah", service=_service_with_extras()
    )

    assert "Firm operations text" not in prompt
    assert "clearing partner" not in prompt
    assert "Location:" not in prompt
    assert "Reference one concrete detail" not in prompt
    assert "Do NOT invent firm details" in prompt


def test_adhoc_prompt_includes_rag_chunks_and_service_block() -> None:
    prompt = _build_adhoc_prompt(
        recipient_name="Sarah", service=_service_with_extras()
    )

    assert "── Service the user is pitching ──" in prompt
    assert "Service name: Stock Loan" in prompt
    assert "Always mention our 24/7 desk." in prompt
    assert "Excerpt A about rebates." in prompt
    assert "Excerpt B about coverage." in prompt


# ── Adhoc draft path ────────────────────────────────────────────────────────


@respx.mock
async def test_generate_outreach_draft_adhoc_succeeds_without_firm_or_contact(
    patch_gemini,
) -> None:
    """firm=None, contact=None routes through the adhoc prompt path."""
    respx.post(_GEMINI_URL).mock(
        return_value=_gemini_response(
            subject="Quick intro on stock-loan rebates",
            body="Hi Sarah,\n\nValue para.\n\n- Sender",
        )
    )

    draft = await generate_outreach_draft(
        service=_service(), recipient_name="Sarah"
    )

    assert draft.subject == "Quick intro on stock-loan rebates"
    assert draft.body.startswith("Hi Sarah")


@respx.mock
async def test_generate_outreach_draft_partial_firm_falls_to_adhoc(
    patch_gemini,
) -> None:
    """Partial context (firm but no contact, or vice versa) treated as adhoc.

    Guards against a caller that constructs FirmContext but forgets the
    ContactContext — without this routing the LLM would see firm details
    but no recipient and could invent a contact identity.
    """
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["prompt"] = payload["contents"][0]["parts"][0]["text"]
        return _gemini_response(subject="S", body="B\n\n- Sender")

    respx.post(_GEMINI_URL).mock(side_effect=_capture)

    await generate_outreach_draft(
        firm=_firm(), contact=None, service=_service()
    )

    assert "Firm operations text" not in captured["prompt"]
    assert "Do NOT invent firm details" in captured["prompt"]


# ── Optimize instructions ─────────────────────────────────────────────────────


def _gemini_text_response(text: str) -> httpx.Response:
    """Mock a plain-text (non-JSON) Gemini response for optimize_instructions."""
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


@respx.mock
async def test_optimize_instructions_returns_cleaned_text(patch_gemini) -> None:
    route = respx.post(_GEMINI_URL).mock(
        return_value=_gemini_text_response(
            "Keep emails under 100 words. Always mention the 24-hour turnaround."
        )
    )

    result = await optimize_instructions("keep emails shrot, mention 24hr turnaround")

    assert result == "Keep emails under 100 words. Always mention the 24-hour turnaround."
    assert route.call_count == 1


@respx.mock
async def test_optimize_instructions_strips_markdown_code_fence(patch_gemini) -> None:
    """Flash sometimes wraps output in a fence despite the no-Markdown rule."""
    respx.post(_GEMINI_URL).mock(
        return_value=_gemini_text_response("```\nKeep it formal and concise.\n```")
    )

    result = await optimize_instructions("be formal")

    assert result == "Keep it formal and concise."


async def test_optimize_instructions_missing_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)

    with pytest.raises(OutreachConfigurationError):
        await optimize_instructions("some instructions")


async def test_optimize_instructions_empty_text_raises_draft_error(patch_gemini) -> None:
    """Whitespace-only input never reaches Gemini — guarded as a draft error."""
    with pytest.raises(OutreachDraftError):
        await optimize_instructions("   ")


@respx.mock
async def test_optimize_instructions_gemini_500_raises_draft_error(
    patch_gemini, no_backoff_sleep
) -> None:
    respx.post(_GEMINI_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(OutreachDraftError):
        await optimize_instructions("some instructions")
