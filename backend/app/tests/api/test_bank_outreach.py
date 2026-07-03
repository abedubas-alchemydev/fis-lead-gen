"""API-layer tests for the bank Outreach draft/send surface.

Integration-marked: touches a real Postgres. Mirrors the BD/advisor outreach
tests. Covers the ``_load_bank_outreach_inputs`` ownership isolation (opaque
404), the no-email guard, and — the point of the "first-class bank kind" —
that an ``outreach_sends`` row carrying ``bank_id`` + ``bank_contact_id`` is
accepted by the re-cast CHECK constraints (migration 20260703_0003).

No Gemini / Gmail mocking: the happy-path draft/send transports are covered by
the shared polymorphic drafter tests; here we pin the bank-specific plumbing.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.bank import Bank, BankContact
from app.models.outreach_send import OutreachSend
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.services.auth import get_current_user

pytestmark = pytest.mark.integration


def _override_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        name="Caller",
        email=f"{user_id}@example.com",
        role="admin",
        session_expires_at=datetime(2099, 1, 1),
    )


async def _seed_user() -> str:
    user_id = f"test-user-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name="Bank Outreach Test",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="admin",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _seed_bank_and_contact(*, email: str | None) -> tuple[int, int]:
    async with SessionLocal() as session:
        bank = Bank(name=f"Test Bank {secrets.token_hex(4)}", charter_status="opened")
        session.add(bank)
        await session.flush()
        contact = BankContact(
            bank_id=bank.id,
            name="Jane Doe",
            title="President",
            email=email,
            source="application_pdf",
            role_context="contact_person",
            source_url="https://occ.gov/pub/app.pdf",
        )
        session.add(contact)
        await session.flush()
        ids = (bank.id, contact.id)
        await session.commit()
    return ids


async def _seed_folder(user_id: str) -> int:
    async with SessionLocal() as session:
        folder = VaultFolder(user_id=user_id, name=f"Folder {secrets.token_hex(4)}", description="")
        session.add(folder)
        await session.flush()
        folder_id = folder.id
        await session.commit()
    return folder_id


async def _cleanup(*, user_ids: list[str], bank_ids: list[int]) -> None:
    async with SessionLocal() as session:
        if bank_ids:
            # ON DELETE CASCADE takes bank_contacts + any outreach_sends.bank_id.
            await session.execute(delete(Bank).where(Bank.id.in_(bank_ids)))
        if user_ids:
            await session.execute(delete(OutreachSend).where(OutreachSend.user_id.in_(user_ids)))
            await session.execute(delete(VaultFolder).where(VaultFolder.user_id.in_(user_ids)))
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        await session.commit()


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_bank_send_row_satisfies_check_constraints() -> None:
    """The whole reason for migration 20260703_0003: an outreach_sends row with
    the bank firm+contact pair (and no recipient_email) must commit cleanly
    against ck_outreach_sends_at_most_one_firm / _at_most_one_contact /
    _adhoc_has_recipient."""
    user_id = await _seed_user()
    bank_id, contact_id = await _seed_bank_and_contact(email="jane@testbank.com")
    try:
        async with SessionLocal() as session:
            send = OutreachSend(
                user_id=user_id,
                bank_id=bank_id,
                bank_contact_id=contact_id,
                subject="Hello from the bank vertical",
                body="Body.",
                provider="google",
                status="sent",
            )
            session.add(send)
            await session.flush()
            send_id = send.id
            await session.commit()

        async with SessionLocal() as session:
            row = (
                await session.execute(select(OutreachSend).where(OutreachSend.id == send_id))
            ).scalar_one()
            assert row.bank_id == bank_id
            assert row.bank_contact_id == contact_id
            assert row.broker_dealer_id is None
            assert row.advisor_id is None
            assert row.contact_id is None
    finally:
        await _cleanup(user_ids=[user_id], bank_ids=[bank_id])


async def test_bank_draft_unknown_ids_returns_404() -> None:
    user_id = await _seed_user()
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/bank-draft",
                json={"bank_id": 999999999, "bank_contact_id": 999999999, "folder_id": 999999999},
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "outreach_inputs_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_ids=[user_id], bank_ids=[])


async def test_bank_send_contact_without_email_returns_400() -> None:
    """Ownership passes (real folder+bank+contact owned by caller) but the
    contact has no email — the send guard returns 400 before any mailbox call."""
    user_id = await _seed_user()
    bank_id, contact_id = await _seed_bank_and_contact(email=None)
    folder_id = await _seed_folder(user_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(user_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/bank-send",
                json={
                    "bank_id": bank_id,
                    "bank_contact_id": contact_id,
                    "folder_id": folder_id,
                    "subject": "Hi",
                    "body": "Body.",
                },
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "recipient_no_email"
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_ids=[user_id], bank_ids=[bank_id])


async def test_bank_send_cross_user_folder_returns_404() -> None:
    """A folder owned by a different user must collapse to the opaque 404 —
    no cross-user leakage."""
    owner_id = await _seed_user()
    other_id = await _seed_user()
    bank_id, contact_id = await _seed_bank_and_contact(email="jane@testbank.com")
    other_folder_id = await _seed_folder(other_id)
    app.dependency_overrides[get_current_user] = lambda: _override_user(owner_id)
    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/outreach/bank-send",
                json={
                    "bank_id": bank_id,
                    "bank_contact_id": contact_id,
                    "folder_id": other_folder_id,
                    "subject": "Hi",
                    "body": "Body.",
                },
            )
        assert response.status_code == 404
        assert response.json()["detail"] == "outreach_inputs_not_found"
    finally:
        app.dependency_overrides.clear()
        await _cleanup(user_ids=[owner_id, other_id], bank_ids=[bank_id])
