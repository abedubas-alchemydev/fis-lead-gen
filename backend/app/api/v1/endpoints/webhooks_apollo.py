"""Apollo phone-reveal webhook handler.

Apollo's ``/people/match`` request with ``reveal_phone_number=true`` returns
the person object synchronously (email + linkedin) but phone_numbers arrive
asynchronously — Apollo POSTs them to the ``webhook_url`` we provide on the
original request. Apollo does NOT sign these callbacks (no HMAC header per
their docs), so we secure the endpoint by embedding a shared secret in the
URL path: every webhook URL we hand Apollo looks like
``{public_base_url}/api/v1/webhooks/apollo/{secret}/phone-reveal``, and the
handler below constant-time-compares the path segment to
``settings.apollo_webhook_secret``. Bad/missing secret → 404 (not 401, so
crawlers can't distinguish a guarded endpoint from a missing one).

Apollo retries failed deliveries, so the handler is idempotent: phones are
appended to the row's ``phones`` JSONB with a sanitized-value dedupe key,
and the scalar ``phone`` is only set when previously NULL.

Payload (from Apollo's docs, verbatim shape):

    {
      "status": "success",
      "total_requested_enrichments": 1,
      "unique_enriched_records": 1,
      "missing_records": 0,
      "credits_consumed": 1,
      "people": [
        {
          "id": "587cf802f65125cad923a266",
          "phone_numbers": [
            {
              "raw_number": "+1 555-123-4567",
              "sanitized_number": "+15551234567",
              "confidence_cd": "...",
              "status_cd": "...",
              "type_cd": "mobile",
              ...
            }
          ],
          ...
        }
      ]
    }

The handler scans the three contact tables (``advisor_contacts``,
``executive_contacts``, ``investor_contacts``) plus ``discovered_email``
(email-extractor enrichment) and ``form4_transactions`` (Investors "Find
contact") for rows whose ``apollo_person_id`` matches each person in the
payload, then merges their phones in. Unknown person ids are logged and
skipped (not an error — Apollo may send late callbacks for rows we've since
deleted).
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.advisor_contact import AdvisorContact
from app.models.discovered_email import DiscoveredEmail
from app.models.executive_contact import ExecutiveContact
from app.models.form4_transaction import Form4Transaction
from app.models.investor_contact import InvestorContact
from app.services.contact_discovery.base import PhoneHit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/apollo")


# Apollo's type_cd → our PhoneHit.type literal. Anything unknown maps to
# "work" so the row still renders (the type badge is just a UX label, not a
# correctness signal).
_TYPE_MAP: dict[str, str] = {
    "mobile": "mobile",
    "cell": "mobile",
    "cellphone": "mobile",
    "work_direct": "work",
    "work": "work",
    "office": "work",
    "direct": "work",
    "home": "hq",
    "hq": "hq",
    "headquarters": "hq",
    "main": "hq",
}

# Apollo's confidence_cd / status_cd are coarse string labels; map a few we
# recognise to our 0-100 confidence scale. Unknown values get a neutral 50.
# The exact mapping isn't load-bearing — _apply_gap_fill / the channel cell
# never compare across providers on phone confidence (it's only used for
# scalar-promotion tiebreak when both raw phones land at the same row).
_CONFIDENCE_MAP: dict[str, float] = {
    "high": 90.0,
    "medium": 70.0,
    "low": 50.0,
    "very_high": 95.0,
    "very_low": 30.0,
    "verified": 90.0,
    "unverified": 50.0,
}


def _phone_type(type_cd: Any) -> str:
    if not isinstance(type_cd, str):
        return "work"
    return _TYPE_MAP.get(type_cd.strip().lower(), "work")


def _phone_confidence(confidence_cd: Any) -> float | None:
    if not isinstance(confidence_cd, str):
        return None
    return _CONFIDENCE_MAP.get(confidence_cd.strip().lower())


def _phone_value(entry: dict[str, Any]) -> str | None:
    """Pick the canonical phone string from an Apollo phone_numbers entry.

    Prefer ``sanitized_number`` (E.164-ish, dedup-friendly) over
    ``raw_number`` (human-formatted). Returns ``None`` if neither is a
    non-empty string."""
    for key in ("sanitized_number", "raw_number"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _to_hit(entry: dict[str, Any]) -> PhoneHit | None:
    value = _phone_value(entry)
    if not value:
        return None
    return PhoneHit(
        value=value,
        type=_phone_type(entry.get("type_cd")),  # type: ignore[arg-type]
        confidence=_phone_confidence(entry.get("confidence_cd")),
        source="apollo_phone_reveal",
    )


def _merge_phones_into_row(
    row: AdvisorContact | ExecutiveContact | InvestorContact,
    new_hits: list[PhoneHit],
) -> int:
    """Append new hits to the row's ``phones`` array (dedupe by sanitized
    value, raw string compare), and fill the scalar ``phone`` when NULL.
    Returns the count of newly-appended hits."""
    existing: list[dict[str, Any]] = list(row.phones or [])
    seen: set[str] = set()
    for entry in existing:
        if isinstance(entry, dict):
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                seen.add(value.strip())
    appended = 0
    for hit in new_hits:
        if hit.value.strip() in seen:
            continue
        seen.add(hit.value.strip())
        existing.append(asdict(hit))
        appended += 1
    if appended:
        row.phones = existing
        if row.phone is None and new_hits:
            # Pick the highest-confidence value from the new hits as the
            # scalar (None confidence treated as 0, ties → first in list).
            best = max(
                new_hits,
                key=lambda h: h.confidence if h.confidence is not None else 0.0,
            )
            row.phone = best.value
        # Stamp discovery attribution on names-only rows that suddenly got
        # phones (apollo found the person, called us back). Don't overwrite
        # an existing provider attribution.
        if row.discovery_source is None:
            row.discovery_source = "apollo_phone_reveal"[:32]
        if row.discovery_confidence is None and new_hits:
            best_conf = next(
                (h.confidence for h in new_hits if h.confidence is not None),
                None,
            )
            if best_conf is not None:
                row.discovery_confidence = Decimal(str(round(best_conf, 2)))
    return appended


def _merge_phone_into_discovered_email(
    row: DiscoveredEmail,
    new_hits: list[PhoneHit],
) -> int:
    """Fill the scalar ``enriched_phone`` on a discovered_email row from the
    highest-confidence revealed hit, when it's currently empty. Returns 1 if a
    phone was written, else 0.

    DiscoveredEmail keeps a single scalar phone (no ``phones`` JSONB like the
    contact tables), so this is set-if-null rather than the append/merge above.
    ``enriched_at`` was already stamped by the sync enrich, so it's left as-is."""
    if row.enriched_phone is not None or not new_hits:
        return 0
    best = max(
        new_hits,
        key=lambda h: h.confidence if h.confidence is not None else 0.0,
    )
    row.enriched_phone = best.value
    return 1


def _merge_phone_into_form4(
    row: Form4Transaction,
    new_hits: list[PhoneHit],
) -> int:
    """Fill the scalar ``enriched_phone`` on a form4_transactions row from the
    highest-confidence revealed hit, when currently empty. Returns 1 if written.

    Like discovered_email, Form 4 rows keep a single scalar phone (no ``phones``
    JSONB), so this is set-if-null. A reporting person spans many transaction
    rows; the loop below calls this for every row sharing the matched
    ``apollo_person_id``. ``enriched_at`` was stamped by the sync "Find contact"
    enrich, so it's left as-is."""
    if row.enriched_phone is not None or not new_hits:
        return 0
    best = max(
        new_hits,
        key=lambda h: h.confidence if h.confidence is not None else 0.0,
    )
    row.enriched_phone = best.value
    return 1


def _require_apollo_webhook_secret(secret: str) -> None:
    """Constant-time-compare the path segment to the configured secret.

    Returns 404 (not 401) on mismatch so crawlers can't distinguish a
    guarded webhook from a missing endpoint. 503 when the secret is not
    configured — that's a deployment misconfig that should be surfaced
    rather than silently 404'd."""
    expected = settings.apollo_webhook_secret
    if not expected:
        # The provider stops sending reveal requests when this is unset,
        # so reaching this branch means someone discovered the URL pattern
        # and is probing. 503 makes the misconfig obvious in logs without
        # giving a probe useful timing signal.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apollo webhook is not configured.",
        )
    if not hmac.compare_digest(secret, expected):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )


@router.post("/{secret}/phone-reveal", status_code=status.HTTP_200_OK)
async def apollo_phone_reveal(
    secret: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Idempotent handler for Apollo's async phone-reveal callback.

    Returns ``{rows_updated, phones_added}`` so an operator can grep
    Cloud Run logs and see how much the webhook actually wrote — useful
    when debugging "I clicked gap-fill 10 minutes ago and still no phones".

    Apollo retries on non-2xx, so we always 200 on a payload we understood,
    even if no rows matched (an unmatched person id is "Apollo's late
    callback for a row we deleted", not a failure)."""
    _require_apollo_webhook_secret(secret)

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be a JSON object.",
        )
    people = body.get("people")
    if not isinstance(people, list):
        return {"rows_updated": 0, "phones_added": 0}

    total_rows_updated = 0
    total_phones_added = 0
    for person in people:
        if not isinstance(person, dict):
            continue
        person_id_raw = person.get("id")
        if not isinstance(person_id_raw, (str, int)):
            continue
        person_id = str(person_id_raw).strip()
        if not person_id:
            continue
        phone_entries = person.get("phone_numbers")
        if not isinstance(phone_entries, list):
            continue
        hits = [h for h in (_to_hit(e) for e in phone_entries if isinstance(e, dict)) if h is not None]
        if not hits:
            continue

        # Look up by apollo_person_id across all three contact tables.
        # The column is indexed on each so each SELECT is a single index
        # probe even with millions of rows.
        for model in (AdvisorContact, ExecutiveContact, InvestorContact):
            stmt = select(model).where(model.apollo_person_id == person_id)
            rows = list((await db.execute(stmt)).scalars().all())
            for row in rows:
                appended = _merge_phones_into_row(row, hits)
                if appended:
                    total_rows_updated += 1
                    total_phones_added += appended

        # Discovered emails (email-extractor enrichment) ride the same reveal
        # flow but keep a single scalar phone, so they get a set-if-null merge
        # rather than the JSONB append used for contact rows above.
        de_stmt = select(DiscoveredEmail).where(
            DiscoveredEmail.apollo_person_id == person_id
        )
        for de_row in (await db.execute(de_stmt)).scalars().all():
            appended = _merge_phone_into_discovered_email(de_row, hits)
            if appended:
                total_rows_updated += 1
                total_phones_added += appended

        # Form 4 insiders (Investors "Find contact") ride the same reveal flow
        # with a scalar phone. A reporting person spans many transaction rows,
        # so fill every still-empty enriched_phone for the matched person id.
        f4_stmt = select(Form4Transaction).where(
            Form4Transaction.apollo_person_id == person_id
        )
        for f4_row in (await db.execute(f4_stmt)).scalars().all():
            appended = _merge_phone_into_form4(f4_row, hits)
            if appended:
                total_rows_updated += 1
                total_phones_added += appended

        if total_rows_updated == 0:
            logger.info(
                "apollo phone-reveal: no matching contact row for person id %s "
                "(may have been deleted, or arrived before the sync write committed)",
                person_id,
            )

    if total_rows_updated:
        await db.commit()
    logger.info(
        "apollo phone-reveal: processed %d person(s), updated %d row(s), added %d phone(s)",
        len([p for p in people if isinstance(p, dict)]),
        total_rows_updated,
        total_phones_added,
    )
    return {"rows_updated": total_rows_updated, "phones_added": total_phones_added}
