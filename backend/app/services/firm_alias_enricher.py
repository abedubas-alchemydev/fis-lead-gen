"""LLM-driven firm-alias enrichment for the website resolver.

Augments the resolver's token pool with parent-company brand
expansions, acronym expansions, and stylized variants that FINRA's
``firm_other_names`` (the source of ``broker_dealers.dba_names``)
rarely captures. Concrete repro this addresses:

  - Firm: ``BOFA SECURITIES, INC.`` (CRD 283942)
  - FINRA DBAs: empty / not parent-brand-related
  - Resolver tokens from legal name: ``["bofasecu"]`` only
    ("BOFA" is below the 5-char per-word minimum, "SECURITIES" is a
     stop-word, single-significant-word names can't form an acronym)
  - Apollo / SerpAPI return ``bankofamerica.com`` (the parent's
    primary digital surface, where BofA Securities's contact emails
    actually live), but ``"bofasecu"`` doesn't anchor on that host
    so the validator rejects it -> ``no_valid_candidate``.

After enrichment, ``resolver_aliases`` lands a list like
``["Bank of America Securities", "Bank of America"]``. The 8-char
prefix token of "Bank of America Securities" is ``"bankofam"``, which
anchors cleanly on ``bankofamerica.com`` and lets the validator pass
the candidate.

Storage / contract
------------------
- Result writes to ``broker_dealers.resolver_aliases`` (JSONB list).
- Kept SEPARATE from ``dba_names`` so FINRA-sourced trade names
  retain their first-class column and the LLM-augmented alternates
  are auditable in their own column. The resolver merges both pools
  when building tokens.
- Empty list is a legitimate outcome (e.g., for genuinely-no-brand
  small firms). Persisting ``[]`` vs ``NULL`` matters: ``NULL`` means
  "never tried", ``[]`` means "tried, nothing useful". Callers should
  prefer ``[]`` over ``NULL`` after a successful Gemini call so the
  enricher doesn't re-run on every page-mount.

Failure-mode contract
---------------------
- Gemini configuration / extraction error -> ``None``. Caller leaves
  ``resolver_aliases`` as ``NULL`` so a future request can retry.
  The website-resolver chain still runs without the augmented tokens
  (graceful degradation).
- Gemini returns aliases that match the legal name (case-insensitive,
  whitespace-normalized) -> filtered out. Same-as-legal aliases add
  no new tokens.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiFirmAliasExtraction,
    GeminiResponsesClient,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are researching alternate names for a U.S. broker-dealer firm. "
    "The firm's legal name and FINRA CRD are given below. Return any "
    "common alternate names this firm is known by, including:\n\n"
    "  1. Acronym expansions of the legal name (e.g., \"BOFA\" -> "
    "\"Bank of America\").\n"
    "  2. Parent-company or affiliated brand names whose website "
    "typically serves as the firm's public digital presence (e.g., a "
    "subsidiary firm that lives at parent.com).\n"
    "  3. Common stylized variants of the legal name (e.g., "
    "\"BofA Securities\", \"Bank of America Securities, Inc.\").\n\n"
    "STRICT RULES:\n"
    "  - Only include names you are confident about. Empty list is the "
    "correct answer when no widely-known alternate names exist.\n"
    "  - Do NOT include direct competitors, unrelated firms, or "
    "made-up variations.\n"
    "  - Do NOT include the legal name itself or trivial case/"
    "punctuation variants of it -- those add no new signal.\n"
    "  - Each alias should be a real name a person might naturally "
    "use to refer to this firm in writing.\n"
    "  - Cap the list at 10 entries; quality over quantity."
)


@dataclass(frozen=True, slots=True)
class FirmAliasResult:
    """Outcome of one alias-enrichment call.

    ``aliases`` is the cleaned list to persist (legal-name dupes
    filtered, whitespace-normalized, deduplicated). ``confidence`` and
    ``rationale`` mirror the LLM response for telemetry / audit. On
    LLM failure callers receive ``None`` from the module-level
    ``enrich_firm_aliases``; this dataclass only models the success path.
    """

    aliases: list[str]
    confidence: float
    rationale: str


def _build_prompt(firm_name: str, crd: str | None) -> str:
    """Compose the user-message body for the Gemini call.

    Embeds the legal name verbatim and the CRD when available so the
    model can disambiguate firms that share a name (e.g., regional
    branches or near-collisions).
    """
    crd_line = f"FINRA CRD: {crd}\n" if crd else "FINRA CRD: (not provided)\n"
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"--- FIRM ---\n"
        f"Legal name: {firm_name}\n"
        f"{crd_line}"
    )


_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALPHA_RE = re.compile(r"[^a-z]")


def _normalize_for_compare(value: str) -> str:
    """Strip everything but lowercase letters.

    Used by the legal-name-dedupe filter to collapse aliases like
    ``"BOFA Securities, Inc."`` / ``"bofa securities inc"`` /
    ``"BOFA SECURITIES, INC."`` to the same comparison key
    (``"bofasecuritiesinc"``). Mirrors the resolver's own token
    generator (``website_resolver._NON_ALPHA_RE``) so an alias that
    would tokenize identically to the legal name is correctly seen as
    a no-new-signal entry. The persisted form keeps the LLM's casing
    and punctuation.
    """
    return _NON_ALPHA_RE.sub("", value.lower())


def _clean_aliases(raw: list[str], legal_name: str) -> list[str]:
    """Post-process the LLM's alias list.

    - Strip surrounding whitespace; drop empty entries.
    - Collapse internal runs of whitespace to single spaces.
    - Deduplicate (case-insensitive, whitespace-normalized).
    - Filter out aliases that normalize to the legal name -- those add
      no new tokens to the resolver pool.
    """
    legal_key = _normalize_for_compare(legal_name)
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        normalized_value = _WHITESPACE_RE.sub(" ", entry).strip()
        if not normalized_value:
            continue
        compare_key = _normalize_for_compare(normalized_value)
        if not compare_key or compare_key == legal_key or compare_key in seen:
            continue
        seen.add(compare_key)
        cleaned.append(normalized_value)
    return cleaned


async def enrich_firm_aliases(
    firm_name: str,
    crd: str | None,
    *,
    client: GeminiResponsesClient | None = None,
) -> FirmAliasResult | None:
    """Generate brand/parent aliases for ``firm_name`` via Gemini.

    Returns ``None`` on Gemini configuration / extraction error so
    the caller can leave ``resolver_aliases`` as ``NULL`` and retry
    on a future request. On success returns a ``FirmAliasResult``
    whose ``aliases`` list is cleaned (legal-name dupes filtered,
    deduped, whitespace-normalized) and ready to persist.

    The Gemini client is constructed per-call by default; tests inject
    a mock via the ``client`` kwarg.
    """
    if not firm_name or not firm_name.strip():
        return FirmAliasResult(aliases=[], confidence=0.0, rationale="Empty firm name.")

    gemini = client or GeminiResponsesClient()
    prompt = _build_prompt(firm_name, crd)
    try:
        extraction: GeminiFirmAliasExtraction = await gemini.extract_firm_aliases(
            prompt=prompt
        )
    except (GeminiConfigurationError, GeminiExtractionError) as exc:
        logger.warning(
            "Firm-alias enrichment failed for %r (CRD=%s): %s",
            firm_name, crd, exc,
        )
        return None

    cleaned = _clean_aliases(extraction.aliases, legal_name=firm_name)
    logger.info(
        "Firm-alias enrichment succeeded for %r (CRD=%s): "
        "raw=%d cleaned=%d confidence=%.2f",
        firm_name,
        crd,
        len(extraction.aliases),
        len(cleaned),
        extraction.confidence_score,
    )
    return FirmAliasResult(
        aliases=cleaned,
        confidence=float(extraction.confidence_score),
        rationale=extraction.rationale,
    )


async def ensure_resolver_aliases(
    db: AsyncSession,
    broker_dealer: BrokerDealer,
    *,
    client: GeminiResponsesClient | None = None,
) -> list[str]:
    """Idempotently populate ``broker_dealer.resolver_aliases`` and return it.

    No-op when ``resolver_aliases`` is already populated (returns the
    existing list directly, including ``[]`` for firms previously
    enriched with no useful aliases). On a NULL column, calls the
    Gemini enricher and writes the result (possibly empty) via a
    race-safe ``UPDATE ... WHERE resolver_aliases IS NULL`` so two
    concurrent callers can't double-write.

    On Gemini failure the column stays ``NULL`` so a future request
    retries; the returned list is ``[]`` so the caller can pass it
    straight into the resolver chain without extra null-handling.

    Persisting ``[]`` (rather than leaving NULL) on a successful but
    empty Gemini response is intentional: it records "we tried, found
    nothing useful" so the enricher doesn't re-fire on every page mount
    for firms that genuinely have no widely-used alternate names.
    """
    if broker_dealer.resolver_aliases is not None:
        return broker_dealer.resolver_aliases

    result = await enrich_firm_aliases(
        broker_dealer.name, broker_dealer.crd_number, client=client
    )
    if result is None:
        return []

    stmt = (
        update(BrokerDealer)
        .where(BrokerDealer.id == broker_dealer.id)
        .where(BrokerDealer.resolver_aliases.is_(None))
        .values(resolver_aliases=result.aliases)
    )
    await db.execute(stmt)
    await db.commit()
    await db.refresh(broker_dealer)
    return broker_dealer.resolver_aliases or []
