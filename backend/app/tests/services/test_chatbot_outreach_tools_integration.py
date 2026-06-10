"""Integration round-trip for the Doxie outreach-copilot tools.

CI-only (integration-marked): exercises the real Postgres persistence the
unit suite stubs out — ``save_outreach_draft`` writes an ``outreach_drafts``
row, list/get read it back, and ``send_outreach_draft`` deletes the draft
and reports the send id. The provider seams (account resolution +
transmission) are monkeypatched because CI has no linked OAuth accounts;
everything DB-side is real.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.core.feature_permissions import SENT_OUTREACH
from app.db.session import SessionLocal
from app.models.auth import AuthUser
from app.models.outreach_draft import OutreachDraft
from app.schemas.auth import AuthenticatedUser
from app.services import chatbot_tools

pytestmark = pytest.mark.integration


def _tool_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Doxie Tool Tester",
        email=f"{user_id}@example.com",
        role="viewer",
        feature_permissions=[SENT_OUTREACH],
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user() -> str:
    user_id = f"test-user-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Doxie Tool Tester",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _cleanup_user(user_id: str) -> None:
    # outreach_drafts.user_id is ON DELETE CASCADE, so dropping the user
    # clears any rows a failed assertion left behind.
    async with SessionLocal() as session:
        await session.execute(delete(AuthUser).where(AuthUser.id == user_id))
        await session.commit()


async def test_save_list_get_send_round_trip(monkeypatch) -> None:
    user_id = await _seed_user()
    user = _tool_user(user_id)
    registry = chatbot_tools.TOOL_REGISTRY
    try:
        async with SessionLocal() as db:
            saved = await registry["save_outreach_draft"].execute(
                user,
                db,
                {
                    "subject": "Quick intro",
                    "body": "Hi,\n\nValue.\n\n- Me",
                    "to_email": "sarah@example.com",
                    "to_name": "Sarah",
                    "cc": ["colleague@example.com"],
                },
            )
            assert saved.get("error") is None
            draft_id = saved["draft_id"]

            listed = await registry["list_outreach_drafts"].execute(
                user, db, {}
            )
            row = next(
                d for d in listed["drafts"] if d["draft_id"] == draft_id
            )
            assert row["source"] == "doxie"
            assert row["to"] == ["sarah@example.com"]

            detail = await registry["get_outreach_draft"].execute(
                user, db, {"draft_id": draft_id}
            )
            assert detail["subject"] == "Quick intro"
            assert detail["to"][0]["email"] == "sarah@example.com"
            assert detail["cc"] == ["colleague@example.com"]

            # Stub the provider seams; the owned-draft load and the
            # post-send delete below run against the real database.
            monkeypatch.setattr(
                chatbot_tools,
                "resolve_sender_account",
                AsyncMock(
                    return_value=SimpleNamespace(
                        provider_id="google", id="acct"
                    )
                ),
            )
            sender = AsyncMock(return_value=SimpleNamespace(id=4242))
            monkeypatch.setattr(
                chatbot_tools, "provider_send_and_record", sender
            )

            sent = await registry["send_outreach_draft"].execute(
                user, db, {"draft_id": draft_id, "confirm": True}
            )
            assert sent.get("error") is None
            assert sent["sent"] is True
            assert sent["send_id"] == 4242
            assert sent["draft_deleted"] is True
            kwargs = sender.await_args.kwargs
            assert kwargs["subject"] == "Quick intro"
            assert kwargs["to_emails"] == ["sarah@example.com"]
            assert kwargs["cc_emails"] == ["colleague@example.com"]

        async with SessionLocal() as db:
            remaining = (
                await db.execute(
                    select(OutreachDraft).where(OutreachDraft.id == draft_id)
                )
            ).scalar_one_or_none()
            assert remaining is None

            # Cross-user opacity: a foreign draft id reads as not_found.
            other = _tool_user(f"other-{secrets.token_hex(4)}")
            ghost = await registry["get_outreach_draft"].execute(
                other, db, {"draft_id": draft_id}
            )
            assert ghost["error"] == "not_found"
    finally:
        await _cleanup_user(user_id)
