"""Cold-email draft generation for the Outreach modal.

Backs the per-contact "Outreach" button on ``/master-list/{id}``. Given a
broker-dealer, an ``executive_contact`` row, and a ``vault_folder``
describing one of the user's services, calls Gemini Flash to produce a
``{subject, body}`` JSON payload tailored to the contact + service.

Distinct from ``services/gemini_responses.py`` — that module is the heavy
PDF-extraction client (clearing/financial/CEO from FOCUS reports). This
module is text-only, single-shot, and uses the cheap ``gemini-2.5-flash``
model so a draft costs ~$0.0005. Draft-only — there is no send path yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.investment_advisor import InvestmentAdvisor

logger = logging.getLogger(__name__)

_GEMINI_KEY_SHAPE = re.compile(r"^AIzaSy[A-Za-z0-9_\-]{33}$")
_OUTREACH_MODEL = "gemini-2.5-flash"
_MAX_RETRIES = 2
_TIMEOUT_SECONDS = 30.0
_DESCRIPTION_BUDGET = 8_000
_FIRM_OPS_BUDGET = 2_000
# Sum cap on retrieved-chunk text injected into the prompt. Each chunk
# averages ~500 tokens; 5 chunks × 500 = ~2,500 tokens of context, well
# under Gemini Flash's effective input limit while leaving headroom for
# instructions + firm/contact/service blocks.
_RETRIEVED_CHUNKS_BUDGET = 12_000
# Per-service ``instructions`` field cap mirrors the schema's max_length
# of 10_000 — quoted again here so a client bypassing the schema can't
# blow out the prompt size.
_INSTRUCTIONS_BUDGET = 10_000


class OutreachConfigurationError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or malformed."""


class OutreachDraftError(RuntimeError):
    """Raised when Gemini returns a non-success status or unparseable body."""


@dataclass(frozen=True)
class FirmContext:
    """Subset of broker-dealer fields relevant to a cold email."""

    name: str
    city: str | None
    state: str | None
    current_clearing_partner: str | None
    firm_operations_text: str | None


@dataclass(frozen=True)
class ContactContext:
    """Subset of executive_contact fields relevant to a cold email."""

    name: str
    title: str
    email: str | None


@dataclass(frozen=True)
class ServiceContext:
    """Subset of vault_folder fields the LLM is expected to sell.

    ``instructions`` is the per-service permanent prompt guidance
    (Deshorn's 2026-05-04 ask) — empty string means "no extra rules".
    ``retrieved_chunks`` are top-K excerpts from the user's attached
    files (RAG); empty tuple means description-only generation, which
    is exactly what the original Outreach MVP did.
    """

    name: str
    description: str
    instructions: str = ""
    retrieved_chunks: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutreachDraft:
    subject: str
    body: str


def advisor_firm_operations(advisor: InvestmentAdvisor) -> str | None:
    """Synthesize a firm_operations_text-equivalent blurb for advisors.

    Advisors don't have a free-text firm_operations field on the model
    -- the closest analog is the Form ADV Item 5.G advisory_activities
    list. Joining them produces a one-line summary the LLM can use as
    soft context.
    """

    activities = advisor.advisory_activities or []
    if not activities:
        return None
    return "Advisory activities: " + ", ".join(str(a) for a in activities if a)


_TONE_CONSTRAINTS = (
    "You are an experienced clearing-and-custody business-development representative writing "
    "a cold email on behalf of the user. The user's firm offers a service described below; "
    "you must pitch that specific service to a contact at a broker-dealer. Be specific, "
    "concise, and credible."
)

_OUTPUT_FORMAT = (
    "Return a single JSON object with exactly two string fields: \"subject\" and \"body\". "
    "The body must use \"\\n\\n\" between paragraphs and end with a sign-off line."
)

_LENGTH_AND_STYLE = (
    "Do not use emojis. Do not open with "
    "\"I hope this email finds you well\" or any equivalent platitude. Keep the body to 3 "
    "short paragraphs (greeting, value, ask) totalling 80–140 words. The subject line must "
    "be under 70 characters and not contain the word \"Re:\"."
)


def _service_block(service: ServiceContext) -> str:
    """Service description + per-service instructions + RAG excerpts.

    Shared between personalized and adhoc prompt paths. The recipient
    block above this is what differs between the two — the service-side
    instructions, description, and retrieved chunks are identical.
    """
    description = (service.description or "").strip()[:_DESCRIPTION_BUDGET] or "(no description provided)"

    instructions = (service.instructions or "").strip()[:_INSTRUCTIONS_BUDGET]
    instructions_block = (
        f"\n── User-supplied instructions for THIS service (must follow exactly) ──\n{instructions}\n"
        if instructions
        else ""
    )

    retrieved = list(service.retrieved_chunks or ())
    if retrieved:
        joined = "\n\n--- excerpt boundary ---\n\n".join(
            chunk.strip() for chunk in retrieved if chunk and chunk.strip()
        )
        joined = joined[:_RETRIEVED_CHUNKS_BUDGET]
        retrieved_block = (
            "\n── Reference excerpts from the user's attached materials "
            "(use them as factual ground truth — do NOT make claims that aren't here) ──\n"
            f"{joined}\n"
        )
    else:
        retrieved_block = ""

    return (
        "── Service the user is pitching ──\n"
        f"Service name: {service.name}\n"
        f"Description: {description}\n"
        f"{instructions_block}"
        f"{retrieved_block}"
    )


def _build_personalized_prompt(
    *, firm: FirmContext, contact: ContactContext, service: ServiceContext
) -> str:
    """Compose the inline prompt sent to Gemini when firm + contact are known.

    Truncates ``service.description`` and ``firm.firm_operations_text`` to
    their respective budgets so a verbose vault description doesn't blow
    out the token cost. The hard tone constraints (length, no emojis, no
    "I hope this email finds you well") live here so the FE can't loosen
    them by passing a different description.
    """
    location_bits = [bit for bit in (firm.city, firm.state) if bit]
    location = ", ".join(location_bits) if location_bits else "an unspecified location"
    firm_ops = (firm.firm_operations_text or "").strip()[:_FIRM_OPS_BUDGET] or "(no firm operations text on file)"
    partner_line = (
        f"They currently use {firm.current_clearing_partner} as their clearing partner."
        if firm.current_clearing_partner
        else "Their current clearing partner is unknown."
    )

    return (
        f"{_TONE_CONSTRAINTS} Reference one concrete detail about the recipient's firm if the "
        f"context provides one — never invent facts. {_LENGTH_AND_STYLE}\n\n"
        "── Recipient ──\n"
        f"Name: {contact.name}\n"
        f"Title: {contact.title}\n"
        f"Firm: {firm.name}\n"
        f"Location: {location}\n"
        f"{partner_line}\n"
        f"Firm operations text (raw, possibly noisy): {firm_ops}\n\n"
        f"{_service_block(service)}\n"
        f"{_OUTPUT_FORMAT}"
    )


def _build_adhoc_prompt(
    *, recipient_name: str | None, service: ServiceContext
) -> str:
    """Compose the inline prompt for adhoc sends with no firm/contact context.

    Three deliberate departures from the personalized prompt:

    1. No "Recipient firm / location / clearing partner / firm operations"
       block — we don't have that data and inviting the LLM to fill it
       guarantees hallucination.
    2. Greeting uses ``recipient_name`` if provided, else "there". We
       intentionally do NOT pass the email address — the domain tempts
       the model to guess a firm name from the TLD.
    3. The anti-hallucination instruction inverts: instead of "reference
       one concrete firm detail", it's "do not guess anything about the
       recipient or their firm — lean entirely on the service description
       and excerpts below". The service block does all the specificity.
    """
    greeting_name = (recipient_name or "").strip() or "there"

    return (
        f"{_TONE_CONSTRAINTS} You do not have any information about the recipient's firm. "
        "Do NOT invent firm details, locations, or current vendors. The pitch must stand on "
        "the service description and reference excerpts below — that's where the specificity "
        f"comes from. {_LENGTH_AND_STYLE}\n\n"
        "── Recipient ──\n"
        f"Name: {greeting_name}\n"
        "(No firm context available — this is a direct outreach to an individual.)\n\n"
        f"{_service_block(service)}\n"
        f"{_OUTPUT_FORMAT}"
    )


def _build_payload(prompt: str) -> dict[str, object]:
    """Wrap the prompt in the ``generateContent`` request envelope."""
    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
                "type": "OBJECT",
                "properties": {
                    "subject": {"type": "STRING"},
                    "body": {"type": "STRING"},
                },
                "required": ["subject", "body"],
                "propertyOrdering": ["subject", "body"],
            },
            "temperature": 0.4,
            "topP": 0.9,
        },
    }


def _extract_text(payload: dict[str, object]) -> str:
    """Pull the text part out of a ``generateContent`` response.

    Same shape Gemini's PDF-extraction path uses (see
    ``gemini_responses._extract_response_text``); duplicated here so the
    outreach module stays a self-contained ~200 LOC file rather than
    cross-importing private helpers from the heavy client.
    """
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        raise OutreachDraftError("Gemini response did not include any candidates.")

    first = candidates[0]
    if not isinstance(first, dict):
        raise OutreachDraftError("Gemini response candidate was malformed.")

    content = first.get("content", {})
    if not isinstance(content, dict):
        raise OutreachDraftError("Gemini response content was malformed.")

    parts = content.get("parts", [])
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            return text

    raise OutreachDraftError("Gemini response did not include structured text output.")


async def _post_with_retries(payload: dict[str, object]) -> dict[str, object]:
    """POST the prompt to ``generateContent``, retrying transient failures.

    Mirrors the retry policy in ``gemini_responses.GeminiResponsesClient``:
    treat 408/409/429/5xx as transient with an exponential backoff capped
    at 8s; all other status codes raise immediately.
    """
    base_url = settings.gemini_api_base.rstrip("/")
    url = f"{base_url}/models/{_OUTREACH_MODEL}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key}
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            transient = {408, 409, 429, 500, 502, 503, 504}
            if exc.response.status_code not in transient or attempt == _MAX_RETRIES:
                detail = exc.response.text.strip()
                raise OutreachDraftError(
                    f"Gemini request failed with status {exc.response.status_code}: "
                    f"{detail or 'No response body.'}"
                ) from exc
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == _MAX_RETRIES:
                raise OutreachDraftError("Gemini request failed due to a network error.") from exc

        await asyncio.sleep(min(2**attempt, 8))

    raise OutreachDraftError("Gemini request failed after retries.") from last_error


async def generate_outreach_draft(
    *,
    service: ServiceContext,
    firm: FirmContext | None = None,
    contact: ContactContext | None = None,
    recipient_name: str | None = None,
) -> OutreachDraft:
    """Generate a single ``{subject, body}`` cold-email draft.

    Two modes:

    * **Personalized** (``firm`` and ``contact`` both provided): used by
      the BD / advisor / investor detail-page flows. The prompt anchors
      on the firm's location, current clearing partner, and operations
      text, plus the contact's name + title.
    * **Adhoc** (``firm=None`` and ``contact=None``): used by the
      free-form-email path on ``/outreach/sent?tab=create``. The prompt
      drops the firm block entirely and uses ``recipient_name`` (or
      "there") as the greeting. The pitch relies on the service
      description + RAG excerpts.

    Passing one of ``firm``/``contact`` without the other is treated as
    adhoc — partial firm context invites the LLM to invent the missing
    half.

    Raises ``OutreachConfigurationError`` if ``GEMINI_API_KEY`` is missing
    or has an invalid shape (same 39-char ``^AIzaSy…`` guard the PDF
    client uses — past CRLF-corruption incident). All other failures (bad
    JSON from Gemini, 5xx after retries, network errors) raise
    ``OutreachDraftError`` so the endpoint can map them to a 502.
    """
    api_key = settings.gemini_api_key
    if not api_key:
        raise OutreachConfigurationError("GEMINI_API_KEY is not configured.")
    if not _GEMINI_KEY_SHAPE.match(api_key):
        raise OutreachConfigurationError(
            f"GEMINI_API_KEY has invalid shape (length={len(api_key)}). "
            f"Expected 39 chars matching ^AIzaSy[A-Za-z0-9_-]{{33}}$."
        )

    if firm is not None and contact is not None:
        prompt = _build_personalized_prompt(firm=firm, contact=contact, service=service)
    else:
        prompt = _build_adhoc_prompt(recipient_name=recipient_name, service=service)
    payload = _build_payload(prompt)
    response_payload = await _post_with_retries(payload)
    response_text = _extract_text(response_payload)

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise OutreachDraftError("Gemini returned invalid JSON for outreach draft.") from exc

    subject = parsed.get("subject") if isinstance(parsed, dict) else None
    body = parsed.get("body") if isinstance(parsed, dict) else None
    if not isinstance(subject, str) or not subject.strip():
        raise OutreachDraftError("Gemini draft is missing a subject.")
    if not isinstance(body, str) or not body.strip():
        raise OutreachDraftError("Gemini draft is missing a body.")

    return OutreachDraft(subject=subject.strip(), body=body.strip())


_OPTIMIZE_INSTRUCTIONS = (
    "You are an expert prompt editor. The text below is a set of permanent "
    "instructions a user wrote to guide an AI that drafts cold outreach emails "
    "for one of their services. Rewrite the instructions so they are clear, "
    "well-organized, and grammatically correct: fix spelling and grammar, "
    "tighten wording, and phrase each rule as a concrete directive the drafting "
    "AI can follow. Preserve the user's intent and every concrete constraint "
    "they specified — do NOT invent new rules, examples, or facts. Keep it "
    "concise. Return ONLY the improved instructions as plain text, with no "
    "preamble, commentary, surrounding quotes, or Markdown code fences."
)


def _build_optimize_payload(text: str) -> dict[str, object]:
    """Plain-text ``generateContent`` envelope for instruction optimization.

    Unlike ``_build_payload`` (which forces the subject/body JSON schema),
    this asks for a single free-text response — the rewritten instructions.
    Temperature is lower than draft generation so the rewrite stays faithful
    to what the user wrote rather than inventing flourishes.
    """
    prompt = f"{_OPTIMIZE_INSTRUCTIONS}\n\n── User instructions ──\n{text}"
    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.9,
        },
    }


def _strip_code_fence(text: str) -> str:
    """Drop a wrapping ```...``` fence if the model added one anyway.

    The prompt forbids Markdown, but Flash occasionally wraps output in a
    fence regardless. Cheap defensive cleanup so the user never sees stray
    backticks pasted into their instructions.
    """
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def optimize_instructions(text: str) -> str:
    """Rewrite per-service outreach instructions for clarity + correctness.

    Backs ``POST /outreach/optimize-instructions`` (the "Optimize Prompt"
    button on the Vault service editor). Sends the user's typed instructions
    to Gemini Flash and returns an improved version — spelling/grammar fixed,
    wording tightened, intent preserved. Plain-text in, plain-text out; no
    RAG, no firm/contact context.

    Same configuration/error contract as ``generate_outreach_draft``:
    ``OutreachConfigurationError`` if ``GEMINI_API_KEY`` is missing or
    malformed (endpoint maps to 503); ``OutreachDraftError`` for an empty
    input or any provider/parse failure (endpoint maps to 502).
    """
    api_key = settings.gemini_api_key
    if not api_key:
        raise OutreachConfigurationError("GEMINI_API_KEY is not configured.")
    if not _GEMINI_KEY_SHAPE.match(api_key):
        raise OutreachConfigurationError(
            f"GEMINI_API_KEY has invalid shape (length={len(api_key)}). "
            f"Expected 39 chars matching ^AIzaSy[A-Za-z0-9_-]{{33}}$."
        )

    cleaned = (text or "").strip()
    if not cleaned:
        raise OutreachDraftError("Cannot optimize empty instructions.")

    payload = _build_optimize_payload(cleaned[:_INSTRUCTIONS_BUDGET])
    response_payload = await _post_with_retries(payload)
    optimized = _strip_code_fence(_extract_text(response_payload).strip())
    if not optimized:
        raise OutreachDraftError("Gemini returned empty optimized instructions.")
    return optimized
