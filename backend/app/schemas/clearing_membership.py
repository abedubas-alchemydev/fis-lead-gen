from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ClearingMembershipItem(BaseModel):
    """One clearing-agency / SRO membership with provenance, for the firm
    detail/profile response.

    Carries the directory's verbatim name + member number and how the match
    was made so a label is auditable. ``status`` is ``active`` or
    ``needs_review`` (``rejected`` rows are filtered out before
    serialization). The compact set of active agency codes also rides on the
    list rows as ``member_agencies`` — this fuller shape is profile-only.
    """

    model_config = ConfigDict(from_attributes=True)

    agency: str
    member_number: str | None = None
    member_name_raw: str
    source_file: str
    source_version: str | None = None
    match_method: str
    match_confidence: float | None = None
    status: str
