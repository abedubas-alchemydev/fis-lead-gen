"""Tier-2 Apollo enrichment for ``bank_contacts`` (paid, capped, opt-in).

The conservative extractor (``services/bank_contact_extraction.py``) fills
``bank_contacts`` with people from the public portion of OCC charter
application PDFs — but those filings rarely print an email or phone, and
often name a person without a title. This module is the paid second tier:
it looks each such person up on Apollo ``/people/match`` (~1 credit per
lookup) anchored to their bank, and fills email / phone / title **only
where currently NULL** — a value extracted from the filing itself is never
overwritten by a provider guess.

Match acceptance is deliberately conservative, because these banks are
brand-new entities Apollo often hasn't indexed yet:

- **Name** must match closely: normalized equality, or Levenshtein
  distance <= ``MAX_NAME_EDIT_DISTANCE`` (2) on the normalized
  ``first last`` composite — enough to absorb PDF text-layer artifacts
  ('Hirshrnan' -> 'Hirshman') without accepting a different human.
- **Organization** must be plausible: Apollo's org for the person matches
  the bank's normalized name (``occ_cas.normalize_bank_name`` equality or
  distinctive-token containment) or the bank's website domain. A person
  Apollo can't tie to the bank is rejected, not guessed.

Everything else is rejected **and logged** (reason included). When the
accepted Apollo full name differs from the stored name by 1–2 edits, the
stored name is corrected to Apollo's rendering and a provenance note is
appended to ``context_snippet`` so the original PDF rendering survives as
the audit trail.

Idempotency / cost discipline (the same shape as every paid job in this
repo): each lookup ATTEMPT that reaches a decision stamps
``enriched_at`` + ``enrich_status`` ('matched' | 'no_match'), and the
planner skips stamped rows and rows that already carry an email — so a
re-run never re-spends a credit. Provider errors do NOT stamp (a
transient outage must not permanently mark a row attempted), and the
batch aborts after ``_MAX_CONSECUTIVE_PROVIDER_ERRORS`` so a revoked key
can't hammer Apollo across the whole table. The HTTP client mirrors
``services/apollo.py``'s call pattern: bounded timeout, retries with
exponential backoff + jitter on 429/5xx/network, hard error on other 4xx.

Entry point: ``scripts/enrich_bank_contacts.py`` (dry-run by default;
``--apply`` to spend credits; ``--limit`` caps lookups).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final
from urllib.parse import urlparse

import httpx
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import Bank, BankContact
from app.services.contact_discovery._shared import first_apollo_phone
from app.services.occ_cas import normalize_bank_name

logger = logging.getLogger(__name__)


# Apollo people-match endpoint: single-person enrichment keyed by
# name + organization. Unlike ``services/apollo.py`` (which is names-only
# by PRD constraint for the BD FOCUS-fallback path), this vertical DOES
# persist contact channels — bank_contacts already stores emails/phones
# extracted from the filings themselves.
_APOLLO_PEOPLE_MATCH_URL: Final = "https://api.apollo.io/api/v1/people/match"

_DEFAULT_TIMEOUT_S: Final = 10.0
_DEFAULT_MAX_ATTEMPTS: Final = 3
_BACKOFF_BASE_S: Final = 0.5
_BACKOFF_JITTER_S: Final = 0.25

# Lookup priority: the filing's contact person first (the person the OCC
# application says to call — highest lead value), then the proposed
# executive team, then organizers, then counsel — so a bounded --limit
# spends its credits on the most valuable rows first.
ROLE_LOOKUP_ORDER: Final = (
    "contact_person",
    "proposed_officer",
    "organizer",
    "counsel",
)

# Max Levenshtein distance for "same person, PDF rendered it wrong"
# (e.g. 'Hirshrnan' -> 'Hirshman' is 2: drop the 'r', turn 'n' into 'm').
MAX_NAME_EDIT_DISTANCE: Final = 2

ENRICH_STATUS_MATCHED: Final = "matched"
ENRICH_STATUS_NO_MATCH: Final = "no_match"

# Circuit breaker: a revoked/exhausted key fails every call the same way;
# stop after this many consecutive provider errors instead of burning the
# retry budget row after row. Unstamped rows are retried next run.
_MAX_CONSECUTIVE_PROVIDER_ERRORS: Final = 3

# Apollo returns this sentinel local-part when a plan can't reveal the
# email; it is not an address and must never be persisted.
_APOLLO_LOCKED_EMAIL_MARKER: Final = "email_not_unlocked"


class ApolloEnrichmentError(Exception):
    """Raised when Apollo returns a non-recoverable error (retries
    exhausted, or a non-429 4xx). The batch loop counts it as a provider
    error and leaves the row UNSTAMPED so the next run retries it."""


# ── Pure helpers ─────────────────────────────────────────────────────────────


# Trailing legal-suffix noise stripped from the bank name before it is used
# as Apollo's ``organization_name`` query ("Erebor Bank, N.A." -> "Erebor
# Bank"). Token-anchored (must follow whitespace/comma and end the string)
# so 'Montana' / 'Bank of America' are never clipped. Applied repeatedly:
# "X Bank, N.A. (In Organization)" sheds both suffixes.
_ORG_SUFFIX_RE: Final = re.compile(
    r"(?:^|[\s,]+)(?:n\.?\s?a\.?|national\s+association|\(\s*in\s+organization\s*\)|\(\s*proposed\s*\))[\s,.]*$",
    re.IGNORECASE,
)

_HONORIFICS: Final = frozenset({"mr", "mrs", "ms", "miss", "dr", "hon", "prof"})
_NAME_SUFFIXES: Final = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "v", "esq", "esquire", "cpa", "cfa", "jd", "phd", "md"}
)


def strip_bank_suffixes(name: str) -> str:
    """Bank name -> Apollo ``organization_name`` query string.

    Drops trailing ', N.A.' / 'National Association' / '(In Organization)'
    style suffixes (repeatedly, so stacked suffixes all go) but keeps the
    original casing — Apollo matches better on display-cased names.
    Falls back to the collapsed original if stripping would empty it.
    """
    collapsed = " ".join((name or "").split())
    out = collapsed
    while True:
        stripped = _ORG_SUFFIX_RE.sub("", out).rstrip(" ,.")
        if stripped == out:
            break
        out = stripped
    return out or collapsed


def split_person_name(name: str | None) -> tuple[str, str] | None:
    """Extracted display name -> ``(first, last)`` for the Apollo query.

    The extractor stores display-ordered names ("Jane A. Doe", "John Q.
    Smith, Esq."). Commas/periods become separators; leading honorifics and
    trailing credential suffixes are dropped; the first and last remaining
    tokens are the given/family names (middle names don't help matching).
    Returns None when both halves can't be recovered — the caller logs the
    row as unparseable instead of guessing.
    """
    if not name:
        return None
    tokens = [t for t in re.sub(r"[,.]", " ", name).split() if t]
    while tokens and tokens[0].lower() in _HONORIFICS:
        tokens = tokens[1:]
    while tokens and tokens[-1].lower() in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    if len(tokens) < 2:
        return None
    return tokens[0], tokens[-1]


def levenshtein(a: str, b: str) -> int:
    """Classic two-row Levenshtein distance. Inputs here are short
    normalized names, so the O(len(a)*len(b)) DP is plenty."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,        # deletion
                    current[j - 1] + 1,     # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current
    return previous[-1]


def _normalize_name_token(value: str) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def names_close(
    ours_name: str,
    apollo_first: str | None,
    apollo_last: str | None,
) -> bool:
    """True when Apollo's person name matches our extracted name closely.

    Comparison is on the normalized ``first last`` composite (middle names
    and punctuation dropped on our side): exact equality, or Levenshtein
    distance <= MAX_NAME_EDIT_DISTANCE to absorb PDF text-layer artifacts
    ('Hirshrnan' vs 'Hirshman'). Missing halves on either side -> False
    (never accept on a partial name).
    """
    split = split_person_name(ours_name)
    if split is None or not apollo_first or not apollo_last:
        return False
    ours = f"{_normalize_name_token(split[0])} {_normalize_name_token(split[1])}"
    theirs = f"{_normalize_name_token(apollo_first)} {_normalize_name_token(apollo_last)}"
    if not ours.strip() or not theirs.strip():
        return False
    if ours == theirs:
        return True
    return levenshtein(ours, theirs) <= MAX_NAME_EDIT_DISTANCE


def domain_from_website(website: str | None) -> str | None:
    """``banks.website`` -> bare lowercase domain for the Apollo query
    ('https://www.erebor.example/about' -> 'erebor.example')."""
    if not website or not website.strip():
        return None
    raw = website.strip()
    parsed = urlparse(raw if "//" in raw else f"//{raw}")
    host = (parsed.netloc or parsed.path.split("/")[0]).strip().lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


# Tokens too generic to corroborate an org match on their own (they survive
# ``normalize_bank_name``, which drops 'national'/'association'/... but
# keeps these).
_GENERIC_ORG_TOKENS: Final = frozenset({"bank", "trust", "bancorp", "bankshares", "holdings"})


def org_plausible(
    bank_name: str,
    bank_domain: str | None,
    apollo_org_name: str | None,
    apollo_org_domain: str | None,
) -> bool:
    """True when Apollo's organization for the person plausibly IS the bank.

    Accepts on (a) domain equality with the bank's website, or (b) the
    vertical's own conservative name normalization
    (``occ_cas.normalize_bank_name``): exact equality, or word-boundary
    containment where the contained side still has at least one
    distinctive (non-generic) token — 'erebor' ⊂ 'erebor bank' passes,
    'trust' ⊂ 'alpha trust' does not. No org info from Apollo -> False
    (nothing to corroborate; these are paid writes, so we refuse).
    """
    if bank_domain and apollo_org_domain:
        theirs = apollo_org_domain.strip().lower()
        if theirs.startswith("www."):
            theirs = theirs[4:]
        if theirs == bank_domain:
            return True
    ours = normalize_bank_name(bank_name or "")
    theirs_name = normalize_bank_name(apollo_org_name or "")
    if not ours or not theirs_name:
        return False
    if ours == theirs_name:
        return True
    ours_tokens = set(ours.split())
    theirs_tokens = set(theirs_name.split())
    shorter, longer = (
        (theirs_tokens, ours_tokens)
        if len(theirs_tokens) <= len(ours_tokens)
        else (ours_tokens, theirs_tokens)
    )
    return bool(shorter) and shorter <= longer and bool(shorter - _GENERIC_ORG_TOKENS)


# ── Apollo client (mirrors services/apollo.py call/credit patterns) ─────────


@dataclass(slots=True, frozen=True)
class ApolloPersonMatch:
    """Trimmed view of an Apollo ``/people/match`` person.

    Parsed at the client boundary — only the fields the enrichment needs
    survive (channels + title + the org identity used for the plausibility
    gate). ``email`` is pre-filtered: Apollo's 'email_not_unlocked@…'
    placeholder never escapes this module.
    """

    first_name: str
    last_name: str
    title: str | None
    email: str | None
    phone: str | None
    organization_name: str | None
    organization_domain: str | None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class BankContactApolloClient:
    """Thin ``/people/match`` client with the repo's standard Apollo
    discipline: bounded timeout, retries with exponential backoff + jitter
    on 429/5xx/network errors, ``ApolloEnrichmentError`` on exhaustion or
    a non-retryable 4xx. Tracks calls and credits so the batch can report
    real spend: ``credits_used`` counts 200 responses that returned a
    person (Apollo bills match-with-result; a clean no-match is free-ish,
    hence the summary's '≈').
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if not api_key:
            raise ValueError("Apollo API key is required")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)
        self.lookups = 0       # match_person() invocations
        self.http_calls = 0    # HTTP requests fired (includes retries)
        self.credits_used = 0  # 200 responses that carried a person

    async def match_person(
        self,
        *,
        first_name: str,
        last_name: str,
        organization_name: str,
        domain: str | None = None,
    ) -> ApolloPersonMatch | None:
        """One paid person lookup. Returns None when Apollo has no match
        (the normal partial-coverage path for brand-new banks); raises
        ``ApolloEnrichmentError`` on provider failure so the caller can
        leave the row unstamped for a retry next run."""
        self.lookups += 1
        payload: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "organization_name": organization_name,
            # Corporate channels only — personal-email reveals cost extra
            # credits and aren't outreach-appropriate here.
            "reveal_personal_emails": False,
        }
        if domain:
            payload["domain"] = domain
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self._api_key,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            self.http_calls += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(
                        _APOLLO_PEOPLE_MATCH_URL, headers=headers, json=payload
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Apollo people/match network error for '%s %s' (attempt %d/%d): %s",
                    first_name, last_name, attempt, self._max_attempts, exc,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            if response.status_code == 200:
                match = self._parse_person(response.json())
                if match is not None:
                    self.credits_used += 1
                return match

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = ApolloEnrichmentError(
                    f"Apollo people/match returned {response.status_code}"
                )
                logger.warning(
                    "Apollo people/match transient error %d for '%s %s' (attempt %d/%d)",
                    response.status_code, first_name, last_name, attempt, self._max_attempts,
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt)
                continue

            raise ApolloEnrichmentError(
                f"Apollo people/match returned {response.status_code} "
                f"for '{first_name} {last_name}'"
            )

        raise ApolloEnrichmentError(
            f"Apollo people/match retries exhausted for "
            f"'{first_name} {last_name}': {last_error}"
        )

    @staticmethod
    async def _backoff(attempt: int) -> None:
        base = _BACKOFF_BASE_S * (2 ** (attempt - 1))
        await asyncio.sleep(base + random.uniform(0, _BACKOFF_JITTER_S))

    @staticmethod
    def _parse_person(payload: object) -> ApolloPersonMatch | None:
        person = payload.get("person") if isinstance(payload, dict) else None
        if not isinstance(person, dict):
            return None

        first = str(person.get("first_name") or "").strip()
        last = str(person.get("last_name") or "").strip()
        if not first or not last:
            full = str(person.get("name") or "").strip()
            parts = full.split(maxsplit=1)
            if len(parts) == 2:
                first, last = parts[0], parts[1]
        if not first or not last:
            return None

        title_raw = person.get("title")
        title = str(title_raw).strip() if title_raw else None

        email_raw = str(person.get("email") or "").strip()
        email: str | None = None
        if email_raw and "@" in email_raw and _APOLLO_LOCKED_EMAIL_MARKER not in email_raw:
            email = email_raw

        phone = first_apollo_phone(person.get("phone_numbers"))

        org = person.get("organization")
        org = org if isinstance(org, dict) else {}
        org_name_raw = org.get("name") or person.get("organization_name")
        org_name = str(org_name_raw).strip() if org_name_raw else None
        org_domain_raw = org.get("primary_domain") or org.get("domain")
        org_domain = str(org_domain_raw).strip().lower() if org_domain_raw else None

        return ApolloPersonMatch(
            first_name=first,
            last_name=last,
            title=title or None,
            email=email,
            phone=phone,
            organization_name=org_name or None,
            organization_domain=org_domain or None,
        )


# ── Plan / decide / apply ────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class PlannedLookup:
    """One contact the batch intends to spend a lookup on. Carries the
    bank context so the execute phase never has to query ``banks``."""

    contact_id: int
    bank_id: int
    bank_name: str
    contact_name: str
    role_context: str
    first_name: str
    last_name: str
    org_query: str
    domain: str | None
    missing: tuple[str, ...]  # of ('email', 'phone', 'title') — for the plan print


@dataclass(slots=True)
class EnrichmentPlan:
    eligible: int = 0                 # rows matching the SQL predicate
    unparseable: int = 0              # eligible but name won't split — skipped, logged
    planned: list[PlannedLookup] = field(default_factory=list)


@dataclass(slots=True)
class BankContactEnrichmentStats:
    eligible: int = 0
    looked_up: int = 0
    matched: int = 0
    no_match: int = 0
    emails_added: int = 0
    phones_added: int = 0
    titles_added: int = 0
    names_corrected: int = 0
    provider_errors: int = 0
    skipped_stale: int = 0            # row changed between plan and execute
    credits_used: int = 0

    def summary_line(self) -> str:
        """The exact one-line summary every paid job in this repo prints."""
        return (
            "bank_contacts_enrich: "
            f"eligible={self.eligible} "
            f"looked_up={self.looked_up} "
            f"matched={self.matched} "
            f"emails_added={self.emails_added} "
            f"phones_added={self.phones_added} "
            f"titles_added={self.titles_added} "
            f"names_corrected={self.names_corrected} "
            f"credits_used≈{self.credits_used}"
        )


async def plan_enrichment(db: AsyncSession, *, limit: int) -> EnrichmentPlan:
    """Phase 1 — read-only. Which contacts qualify, in spend order.

    Zero Apollo calls: eligibility is pure SQL. A contact qualifies when it
    has no email AND has never been attempted (``enriched_at IS NULL`` —
    which also skips 'matched' rows Apollo had no email for, so a re-run
    never re-spends their credit). Ordered contact_person first, then
    proposed officers, organizers, counsel; capped at ``limit`` lookups.
    """
    role_rank = case(
        {role: rank for rank, role in enumerate(ROLE_LOOKUP_ORDER)},
        value=BankContact.role_context,
        else_=len(ROLE_LOOKUP_ORDER),
    )
    stmt = (
        select(BankContact, Bank.name, Bank.website)
        .join(Bank, Bank.id == BankContact.bank_id)
        .where(BankContact.email.is_(None), BankContact.enriched_at.is_(None))
        .order_by(role_rank, BankContact.bank_id.asc(), BankContact.id.asc())
    )
    rows = (await db.execute(stmt)).all()

    plan = EnrichmentPlan(eligible=len(rows))
    for contact, bank_name, bank_website in rows:
        if len(plan.planned) >= max(0, limit):
            break
        split = split_person_name(contact.name)
        if split is None:
            plan.unparseable += 1
            logger.info(
                "bank_contacts_enrich: skipping contact id=%s (bank_id=%s) — "
                "name %r won't split into first/last",
                contact.id, contact.bank_id, contact.name,
            )
            continue
        missing = tuple(
            channel
            for channel, value in (
                ("email", contact.email),
                ("phone", contact.phone),
                ("title", contact.title),
            )
            if value is None
        )
        plan.planned.append(
            PlannedLookup(
                contact_id=contact.id,
                bank_id=contact.bank_id,
                bank_name=bank_name,
                contact_name=contact.name,
                role_context=contact.role_context,
                first_name=split[0],
                last_name=split[1],
                org_query=strip_bank_suffixes(bank_name),
                domain=domain_from_website(bank_website),
                missing=missing,
            )
        )
    return plan


def evaluate_match(
    item: PlannedLookup, match: ApolloPersonMatch | None
) -> tuple[bool, str]:
    """Accept/reject one Apollo response. Returns ``(accepted, reason)``;
    reasons feed the reject log ('no_person' | 'name_mismatch' |
    'org_mismatch' | 'accepted')."""
    if match is None:
        return False, "no_person"
    if not names_close(item.contact_name, match.first_name, match.last_name):
        return False, "name_mismatch"
    if not org_plausible(
        item.bank_name, item.domain, match.organization_name, match.organization_domain
    ):
        return False, "org_mismatch"
    return True, "accepted"


@dataclass(slots=True, frozen=True)
class ProposedUpdates:
    """What an accepted match would change — computed pure so the write
    path (and the tests) can inspect it before any mutation."""

    email: str | None            # fill only (current is NULL)
    phone: str | None            # fill only
    title: str | None            # fill only
    corrected_name: str | None   # replaces contact.name (provenance kept)
    provenance_note: str | None


def propose_updates(contact: BankContact, match: ApolloPersonMatch) -> ProposedUpdates:
    """NULL-fill proposals + the near-name correction.

    Never proposes overwriting a value extracted from the filing: email /
    phone / title are proposed only when the stored column is NULL. The
    stored NAME is corrected only when Apollo's accepted full name is
    1..MAX_NAME_EDIT_DISTANCE edits away (case-insensitive) — the classic
    PDF text-layer artifact window — and the original rendering is
    preserved in a provenance note appended to ``context_snippet``.
    """
    email = match.email if contact.email is None and match.email else None
    phone = match.phone if contact.phone is None and match.phone else None
    title = match.title if contact.title is None and match.title else None

    corrected_name: str | None = None
    provenance_note: str | None = None
    stored = " ".join((contact.name or "").split())
    apollo_full = " ".join(match.full_name.split())
    if stored and apollo_full and stored != apollo_full:
        distance = levenshtein(stored.lower(), apollo_full.lower())
        if 1 <= distance <= MAX_NAME_EDIT_DISTANCE:
            corrected_name = apollo_full
            provenance_note = (
                f" [name corrected via Apollo match; PDF rendered '{contact.name}']"
            )
    return ProposedUpdates(
        email=email,
        phone=phone,
        title=title,
        corrected_name=corrected_name,
        provenance_note=provenance_note,
    )


async def _dedupe_key_collides(
    db: AsyncSession,
    *,
    bank_id: int,
    name: str,
    title: str | None,
    source: str,
    exclude_id: int,
) -> bool:
    """True when another row already occupies the ``uq_bank_contacts_dedupe``
    key ``(bank_id, name, coalesce(title,''), source)`` this update would
    move onto — a name correction or title fill must not abort the batch
    with an IntegrityError."""
    stmt = (
        select(BankContact.id)
        .where(
            BankContact.bank_id == bank_id,
            BankContact.name == name,
            func.coalesce(BankContact.title, "") == (title or ""),
            BankContact.source == source,
            BankContact.id != exclude_id,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def execute_enrichment(
    db: AsyncSession,
    client: BankContactApolloClient,
    planned: list[PlannedLookup],
) -> BankContactEnrichmentStats:
    """Phase 2 — spend the credits. One lookup per planned contact, commit
    per row (a crash mid-batch loses only the in-flight contact; stamped
    rows are skipped on the resume run).

    ``stats.eligible`` is NOT set here — the caller carries it over from
    the plan so the summary line reflects the full backlog, not the cap.
    """
    stats = BankContactEnrichmentStats()
    consecutive_provider_errors = 0

    for item in planned:
        contact = await db.get(BankContact, item.contact_id)
        if contact is None or contact.email is not None or contact.enriched_at is not None:
            # Row deleted / enriched between plan and execute (or by a
            # concurrent run) — don't spend the credit.
            stats.skipped_stale += 1
            continue

        try:
            match = await client.match_person(
                first_name=item.first_name,
                last_name=item.last_name,
                organization_name=item.org_query,
                domain=item.domain,
            )
        except ApolloEnrichmentError as exc:
            stats.provider_errors += 1
            consecutive_provider_errors += 1
            logger.warning(
                "bank_contacts_enrich: provider error for contact id=%s (%s @ %s), "
                "row left unstamped for retry: %s",
                item.contact_id, item.contact_name, item.bank_name, exc,
            )
            if consecutive_provider_errors >= _MAX_CONSECUTIVE_PROVIDER_ERRORS:
                logger.error(
                    "bank_contacts_enrich: %d consecutive provider errors — "
                    "aborting the batch (key revoked / Apollo down?)",
                    consecutive_provider_errors,
                )
                break
            continue
        consecutive_provider_errors = 0
        stats.looked_up += 1
        now = datetime.now(timezone.utc)

        accepted, reason = evaluate_match(item, match)
        if not accepted:
            contact.enrich_status = ENRICH_STATUS_NO_MATCH
            contact.enriched_at = now
            stats.no_match += 1
            logger.info(
                "bank_contacts_enrich: REJECT contact id=%s '%s' @ '%s' — %s%s",
                item.contact_id,
                item.contact_name,
                item.bank_name,
                reason,
                (
                    f" (apollo returned '{match.full_name}' @ "
                    f"'{match.organization_name or '?'}')"
                    if match is not None
                    else ""
                ),
            )
            await db.commit()
            continue

        updates = propose_updates(contact, match)
        final_name = updates.corrected_name or contact.name
        final_title = updates.title or contact.title
        apply_name_or_title = updates.corrected_name is not None or updates.title is not None
        if apply_name_or_title and await _dedupe_key_collides(
            db,
            bank_id=contact.bank_id,
            name=final_name,
            title=final_title,
            source=contact.source,
            exclude_id=contact.id,
        ):
            # Another row already holds the (name, title) key this update
            # would move onto — keep the channel fills, skip the rename/title.
            logger.warning(
                "bank_contacts_enrich: dedupe-key collision for contact id=%s — "
                "skipping name/title update ('%s' / '%s'), keeping channel fills",
                contact.id, final_name, final_title,
            )
            updates = ProposedUpdates(
                email=updates.email,
                phone=updates.phone,
                title=None,
                corrected_name=None,
                provenance_note=None,
            )

        if updates.email is not None:
            contact.email = updates.email
            stats.emails_added += 1
        if updates.phone is not None:
            contact.phone = updates.phone
            stats.phones_added += 1
        if updates.title is not None:
            contact.title = updates.title
            stats.titles_added += 1
        if updates.corrected_name is not None:
            contact.name = updates.corrected_name
            note = updates.provenance_note or ""
            existing_snippet = (contact.context_snippet or "").rstrip()
            contact.context_snippet = (
                f"{existing_snippet}{note}" if existing_snippet else note.strip()
            )
            stats.names_corrected += 1

        contact.enrich_status = ENRICH_STATUS_MATCHED
        contact.enriched_at = now
        stats.matched += 1
        logger.info(
            "bank_contacts_enrich: MATCH contact id=%s '%s' @ '%s' — "
            "email=%s phone=%s title=%s name_corrected=%s",
            contact.id,
            contact.name,
            item.bank_name,
            "added" if updates.email else ("kept" if contact.email else "none"),
            "added" if updates.phone else ("kept" if contact.phone else "none"),
            "added" if updates.title else ("kept" if contact.title else "none"),
            "yes" if updates.corrected_name else "no",
        )
        await db.commit()

    stats.credits_used = client.credits_used
    return stats
