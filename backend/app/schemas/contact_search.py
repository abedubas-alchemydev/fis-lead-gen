"""Pydantic shapes for the cross-entity contact search surface.

Backs ``POST /api/v1/contacts/find-by-email`` and
``POST /api/v1/contacts/find-by-domain``. Both endpoints search across
all three contact tables (executive_contacts, advisor_contacts,
investor_contacts) plus discovered_emails, returning a unified result
with provenance metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactSearchByEmailRequest(BaseModel):
    """Body for ``POST /contacts/find-by-email``."""

    email: EmailStr
    # When true, falls back to Apollo /people/match for emails that have
    # no hit in any local table. Gated by the caller so cost stays
    # visible -- the FE button explicitly opts in.
    enrich_via_apollo: bool = False


class ContactSearchByDomainRequest(BaseModel):
    """Body for ``POST /contacts/find-by-domain``."""

    domain: str = Field(..., min_length=3, max_length=255)


class ContactSearchHit(BaseModel):
    """One hit in the contact-search results.

    ``firm_type`` + ``firm_id`` link the hit back to its parent firm
    record so the FE can render a deep-link to the correct detail page.
    ``source`` tells the user where the hit came from (which table or
    provider) so they can judge confidence at a glance.
    """

    model_config = ConfigDict(from_attributes=True)

    source: Literal[
        "executive_contact",
        "advisor_contact",
        "investor_contact",
        "discovered_email",
        "apollo",
    ]
    firm_type: Literal["broker_dealer", "advisor", "institutional_investor"] | None
    firm_id: int | None
    firm_name: str | None
    contact_id: int | None
    name: str | None
    title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None = None


class ContactSearchResponse(BaseModel):
    """Wraps hits + a flat ``count`` so the FE can render "N matches"."""

    hits: list[ContactSearchHit]
    count: int
