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


def synthesize_contact_arrays(
    item: object,
) -> tuple[list[EmailHit], list[PhoneHit]]:
    """Project scalar ``email`` / ``phone`` into 1-element lists when the JSONB arrays are empty."""
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
    return emails, phones
