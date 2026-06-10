"""Unit test for compose-send recipient de-duplication.

Pure function — no DB / no transport — so this runs in the unit job
(unmarked), unlike the integration-marked endpoint tests in
``test_outreach_create_tab.py``. The helper now lives in
``app.services.outreach_send`` (shared with Doxie's send tool) and takes
plain address lists; the compose endpoint unpacks its payload the same way.
"""

from __future__ import annotations

from app.schemas.vault import (
    OutreachComposeRecipient,
    OutreachComposeSendRequest,
)
from app.services.outreach_send import dedupe_recipients


def _dedupe(payload: OutreachComposeSendRequest):
    # Mirrors the unpacking in POST /outreach/compose-send.
    return dedupe_recipients(
        [r.email for r in payload.to], payload.cc, payload.bcc
    )


def test_dedupe_collapses_addresses_across_buckets() -> None:
    """An address lands in exactly one bucket, earliest-wins To>Cc>Bcc,
    matched case-insensitively; order + first-seen casing preserved."""

    payload = OutreachComposeSendRequest(
        to=[
            OutreachComposeRecipient(email="alice@firm.com", name="Alice"),
            OutreachComposeRecipient(email="ALICE@firm.com"),  # case-dup of To
            OutreachComposeRecipient(email="bob@firm.com"),
        ],
        cc=["bob@firm.com", "carol@firm.com"],  # bob already in To -> dropped
        bcc=["carol@firm.com", "dave@firm.com"],  # carol now in Cc -> dropped
        subject="s",
        body="b",
    )

    to, cc, bcc = _dedupe(payload)

    # Compare lower-cased so the test doesn't depend on EmailStr's
    # domain-normalisation; the dedup itself is what's under test.
    assert [a.lower() for a in to] == ["alice@firm.com", "bob@firm.com"]
    assert [a.lower() for a in cc] == ["carol@firm.com"]
    assert [a.lower() for a in bcc] == ["dave@firm.com"]


def test_dedupe_keeps_disjoint_lists_intact() -> None:
    payload = OutreachComposeSendRequest(
        to=[OutreachComposeRecipient(email="a@x.com")],
        cc=["b@x.com"],
        bcc=["c@x.com"],
        subject="s",
        body="b",
    )

    to, cc, bcc = _dedupe(payload)

    assert [a.lower() for a in to] == ["a@x.com"]
    assert [a.lower() for a in cc] == ["b@x.com"]
    assert [a.lower() for a in bcc] == ["c@x.com"]
