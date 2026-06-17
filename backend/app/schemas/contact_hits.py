from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EmailHit(BaseModel):
    value: str
    type: Literal["work", "personal"]
    confidence: float | None = None
    source: str


class PhoneHit(BaseModel):
    value: str
    type: Literal["mobile", "work", "hq"]
    confidence: float | None = None
    source: str


def _order_emails_personal_first(emails: list[EmailHit]) -> list[EmailHit]:
    """Surface a contact's personal address ahead of work ones.

    Personal first, then by descending confidence (unknown confidence last);
    stable for ties so same-rank hits keep their original order.
    """
    return sorted(
        emails,
        key=lambda hit: (
            0 if hit.type == "personal" else 1,
            -(hit.confidence if hit.confidence is not None else -1.0),
        ),
    )


def synthesize_contact_arrays(
    item: object,
) -> tuple[list[EmailHit], list[PhoneHit]]:
    """Project scalar ``email`` / ``phone`` into 1-element lists when the JSONB
    arrays are empty, and order emails personal-first for display.

    Ordering lives here — the single read-time chokepoint shared by all three
    ``*ContactItem`` schemas — so a contact's personal email surfaces ahead of
    the work one on every view (firm detail pages, outreach surfaces) without
    per-view logic.
    """
    emails: list[EmailHit] = list(getattr(item, "emails", None) or [])
    phones: list[PhoneHit] = list(getattr(item, "phones", None) or [])
    source = getattr(item, "discovery_source", None) or getattr(item, "source", "") or ""
    confidence = getattr(item, "discovery_confidence", None)
    if not emails:
        email = getattr(item, "email", None)
        if email:
            emails = [EmailHit(value=email, type="work", confidence=confidence, source=source)]
    if not phones:
        phone = getattr(item, "phone", None)
        if phone:
            phones = [PhoneHit(value=phone, type="work", confidence=confidence, source=source)]
    return _order_emails_personal_first(emails), phones
