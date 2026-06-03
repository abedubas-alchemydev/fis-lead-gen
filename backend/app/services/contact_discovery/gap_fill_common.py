"""Generic gap-fill helpers for the three contact tables.

``executive_contacts`` (BD), ``advisor_contacts`` (IA), and
``investor_contacts`` (II) share the exact same shape -- scalar
``email``/``phone``/``linkedin_url`` plus JSONB ``emails``/``phones``
arrays plus a ``discovery_source``/``discovery_confidence`` pair. The
gap-fill merge semantics (non-destructive: append-only on arrays, fill
scalars ONLY when NULL) are identical across the three tables.

This module hosts the two pure helpers --- :func:`is_gap_row` and
:func:`apply_gap_fill` --- so the per-kind runners in
``advisor_refresh_orchestrator``, ``bd_contact_gap_fill``, and
``investor_contact_gap_fill`` reuse one source of truth. The helpers
take any object that quacks like a contact row (Protocol-typed
duck-typing); they don't import the SQLAlchemy models directly so a
test stub can be passed in too.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Protocol

from app.services.contact_discovery.base import (
    DiscoveryResult,
    EmailHit,
    PhoneHit,
)
from app.services.contact_discovery.orchestrator import _highest_confidence_value


class GapFillRow(Protocol):
    """Duck-typed shape of an ``executive_contacts`` / ``advisor_contacts`` /
    ``investor_contacts`` row participating in gap-fill."""

    name: str
    email: str | None
    phone: str | None
    linkedin_url: str | None
    emails: list[Any] | None
    phones: list[Any] | None
    discovery_source: str | None
    discovery_confidence: Decimal | None
    # PR #586 added apollo_person_id to all three contact tables so the
    # async phone-reveal webhook can find a row by its Apollo id. Listed
    # here so the merge below can stamp it consistently.
    apollo_person_id: str | None


def is_gap_row(row: GapFillRow) -> bool:
    """True if ``row`` is missing any of LinkedIn URL, email, or phone.

    A scalar value (``row.email``) or a non-empty JSONB array
    (``row.emails``) both count as "present" -- a row with
    ``emails=[{value:'a@b.com',...}]`` and ``email=None`` is NOT a gap,
    because the schema layer projects the array on read.
    """
    has_email = bool(row.email) or bool(row.emails)
    has_phone = bool(row.phone) or bool(row.phones)
    has_linkedin = bool(row.linkedin_url)
    return not (has_email and has_phone and has_linkedin)


def apply_gap_fill(row: GapFillRow, merged: DiscoveryResult) -> bool:
    """Merge ``merged`` INTO ``row`` in place. Returns True iff anything changed.

    Non-destructive merge semantics:

    - ``emails`` / ``phones``: append new entries only; existing entries are
      never replaced or removed. Dedupe key matches the chain merger
      (lowercase value for emails, raw stripped for phones).
    - ``linkedin_url``: fill ONLY when currently NULL. We never overwrite a
      URL even if the chain returned a different one -- manual edits and the
      first-non-null choice from the original enrichment both stay sticky.
    - Scalar ``email`` / ``phone``: fill ONLY when currently NULL, using
      ``_highest_confidence_value`` over the post-merge array.
    - ``discovery_source`` / ``discovery_confidence``: stamp ONLY when
      previously NULL (names-only rows get a real provider attribution on
      first hit).
    """
    changed = False

    existing_emails: list[dict] = list(row.emails or [])
    seen_email_keys: set[str] = set()
    for entry in existing_emails:
        value = (entry.get("value") if isinstance(entry, dict) else None) or ""
        key = value.lower().strip()
        if key:
            seen_email_keys.add(key)
    for hit in merged.emails:
        key = (hit.value or "").lower().strip()
        if not key or key in seen_email_keys:
            continue
        seen_email_keys.add(key)
        existing_emails.append(asdict(hit))
        changed = True
    if merged.email:
        scalar_key = merged.email.lower().strip()
        if scalar_key and scalar_key not in seen_email_keys:
            seen_email_keys.add(scalar_key)
            existing_emails.append(
                asdict(
                    EmailHit(
                        value=merged.email,
                        type="work",
                        confidence=merged.confidence,
                        source=merged.provider,
                    )
                )
            )
            changed = True
    if changed:
        row.emails = existing_emails

    existing_phones: list[dict] = list(row.phones or [])
    seen_phone_keys: set[str] = set()
    for entry in existing_phones:
        value = (entry.get("value") if isinstance(entry, dict) else None) or ""
        key = value.strip()
        if key:
            seen_phone_keys.add(key)
    phones_changed = False
    for hit in merged.phones:
        key = (hit.value or "").strip()
        if not key or key in seen_phone_keys:
            continue
        seen_phone_keys.add(key)
        existing_phones.append(asdict(hit))
        phones_changed = True
    if merged.phone:
        scalar_key = merged.phone.strip()
        if scalar_key and scalar_key not in seen_phone_keys:
            seen_phone_keys.add(scalar_key)
            existing_phones.append(
                asdict(
                    PhoneHit(
                        value=merged.phone,
                        type="work",
                        confidence=merged.confidence,
                        source=merged.provider,
                    )
                )
            )
            phones_changed = True
    if phones_changed:
        row.phones = existing_phones
        changed = True

    if row.linkedin_url is None and merged.linkedin_url:
        row.linkedin_url = merged.linkedin_url
        changed = True

    if row.email is None and row.emails:
        hits = [
            EmailHit(
                value=e["value"],
                type=e.get("type") or "work",
                confidence=e.get("confidence"),
                source=e.get("source") or "",
            )
            for e in row.emails
            if isinstance(e, dict) and e.get("value")
        ]
        scalar = _highest_confidence_value(hits) if hits else None
        if scalar:
            row.email = scalar
            changed = True

    if row.phone is None and row.phones:
        hits = [
            PhoneHit(
                value=p["value"],
                type=p.get("type") or "work",
                confidence=p.get("confidence"),
                source=p.get("source") or "",
            )
            for p in row.phones
            if isinstance(p, dict) and p.get("value")
        ]
        scalar = _highest_confidence_value(hits) if hits else None
        if scalar:
            row.phone = scalar
            changed = True

    if row.discovery_source is None and merged.provider:
        row.discovery_source = merged.provider[:32]
        changed = True
    if row.discovery_confidence is None:
        row.discovery_confidence = Decimal(str(round(merged.confidence, 2)))
        changed = True
    # Stamp the Apollo person id when previously NULL so the async phone-
    # reveal webhook (PR #586) can find this row later. Never overwrite
    # -- if a prior enrich already linked the row to an Apollo id, keep
    # it; otherwise a webhook still in flight would land on the wrong row.
    if row.apollo_person_id is None and merged.apollo_person_id:
        row.apollo_person_id = merged.apollo_person_id
        changed = True

    return changed


__all__ = ["GapFillRow", "is_gap_row", "apply_gap_fill"]
