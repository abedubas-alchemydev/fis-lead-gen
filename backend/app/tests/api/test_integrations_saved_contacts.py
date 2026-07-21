"""API-layer tests for /api/v1/integrations/saved-contacts (CRM M2M read).

Integration-marked -- touches a real Postgres so the cross-user join to
``user`` and the newest-first ordering actually exercise. Unlike the
owner-scoped ``test_saved_contacts.py`` (which overrides ``get_current_user``),
auth here is a shared bearer key set via ``monkeypatch`` on
``settings.crm_integration_api_key`` -- there is no session/feature override.

Because the endpoint returns EVERY user's saved contacts, the shared dev DB may
hold unrelated saves; assertions therefore filter to the users each test seeds,
and the ordering/filter case uses a per-run unique ``source`` so it can assert
an exact row set regardless of other data.
"""

from __future__ import annotations

import secrets

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.auth import AuthUser
from app.models.saved_contact import SavedContact

pytestmark = pytest.mark.integration

# A random key so a stray real env value can never accidentally satisfy the gate.
_VALID_KEY = "crm-integration-test-key-" + secrets.token_hex(8)


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _seed_user() -> str:
    """Seed one ``user`` row (the saved-contact owner) and return its id."""
    user_id = f"crm-user-{secrets.token_hex(6)}"
    async with SessionLocal() as session:
        session.add(
            AuthUser(
                id=user_id,
                name=f"Owner {user_id[-4:]}",
                email=f"{user_id}@example.com",
                email_verified=False,
                role="viewer",
                status="active",
            )
        )
        await session.commit()
    return user_id


async def _seed_saved_contact(
    user_id: str,
    *,
    contact_id: int,
    source: str = "discovered_email",
    name: str | None = "Jane Doe",
    email: str | None = "jane.doe@acme.com",
) -> int:
    """Insert a ``saved_contact`` row directly (the read path never joins the
    source ``discovered_email``, so no extraction_run scaffolding is needed).

    Returns the new row's id."""
    async with SessionLocal() as session:
        row = SavedContact(
            user_id=user_id,
            source=source,
            contact_id=contact_id,
            name=name,
            title="Head of Trading",
            email=email,
            company="Acme Securities",
            phone="+1-212-555-0100",
            linkedin_url="https://www.linkedin.com/in/janedoe",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _cleanup(user_ids: list[str]) -> None:
    async with SessionLocal() as session:
        if user_ids:
            await session.execute(
                delete(SavedContact).where(SavedContact.user_id.in_(user_ids))
            )
            await session.execute(delete(AuthUser).where(AuthUser.id.in_(user_ids)))
        await session.commit()


def _cid() -> int:
    """A random source contact_id (bare int reference, no FK)."""
    return secrets.randbelow(1_000_000) + 1


# ── (a) valid key -> 200, cross-user rows carry owner id/name/email ──────────


async def test_valid_key_returns_all_users_contacts_with_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "crm_integration_api_key", _VALID_KEY)
    owner_a = await _seed_user()
    owner_b = await _seed_user()
    cid_a, cid_b = _cid(), _cid()
    await _seed_saved_contact(owner_a, contact_id=cid_a, name="Alice A", email="alice@a.com")
    await _seed_saved_contact(owner_b, contact_id=cid_b, name="Bob B", email="bob@b.com")
    try:
        async with _client() as client:
            resp = await client.get(
                "/api/v1/integrations/saved-contacts",
                headers={"Authorization": f"Bearer {_VALID_KEY}"},
            )
        assert resp.status_code == 200
        body = resp.json()

        # Cross-user read: BOTH owners' saves come back (filtered to the users
        # this test seeded so unrelated dev rows can't fail the assertion).
        mine = {
            row["saved_by_id"]: row
            for row in body
            if row["saved_by_id"] in {owner_a, owner_b}
        }
        assert set(mine) == {owner_a, owner_b}

        a = mine[owner_a]
        # Owner attribution is populated from the joined user row...
        assert a["saved_by_id"] == owner_a
        assert a["saved_by_name"] == f"Owner {owner_a[-4:]}"
        assert a["saved_by_email"] == f"{owner_a}@example.com"
        # ...alongside the base saved-contact snapshot fields.
        assert a["source"] == "discovered_email"
        assert a["contact_id"] == cid_a
        assert a["name"] == "Alice A"
        assert a["email"] == "alice@a.com"
        assert isinstance(a["id"], int)
        assert a["created_at"]  # ISO string present

        b = mine[owner_b]
        assert b["saved_by_email"] == f"{owner_b}@example.com"
        assert b["contact_id"] == cid_b
        assert b["name"] == "Bob B"
    finally:
        await _cleanup([owner_a, owner_b])


# ── (b) missing / wrong bearer -> 401 ────────────────────────────────────────


async def test_missing_or_wrong_bearer_is_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "crm_integration_api_key", _VALID_KEY)
    async with _client() as client:
        no_header = await client.get("/api/v1/integrations/saved-contacts")
        wrong_token = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": "Bearer not-the-key"},
        )
        no_bearer_prefix = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": _VALID_KEY},  # right value, no "Bearer "
        )
        empty_token = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": "Bearer "},
        )
    assert no_header.status_code == 401
    assert wrong_token.status_code == 401
    assert no_bearer_prefix.status_code == 401
    assert empty_token.status_code == 401


# ── (b2) non-ASCII bearer -> 401, never a 500 (compare_digest footgun) ────────


async def test_non_ascii_bearer_is_401_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crafted non-ASCII bearer must fail closed as 401, not crash as 500.

    Starlette decodes the Authorization header as latin-1, so ``Bearer é``
    reaches the gate as the non-ASCII str ``"é"``. A str/str ``compare_digest``
    raises ``TypeError`` on that -- an uncaught 500 any UNAUTHENTICATED caller
    could trigger (DoS / log-spam). The gate compares bytes, which never raises.

    The header is sent as raw bytes because httpx refuses to ASCII-encode a
    non-ASCII str header value client-side; the bytes reproduce the latin-1 wire
    byte the real footgun rides in on.
    """
    monkeypatch.setattr(settings, "crm_integration_api_key", _VALID_KEY)
    async with _client() as client:
        resp = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": "Bearer é".encode("latin-1")},
        )
    assert resp.status_code == 401


# ── (c) key unset/empty -> 503 (fails closed) ────────────────────────────────


async def test_unset_key_fails_closed_with_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unset: even a well-formed bearer can't reach the read.
    monkeypatch.setattr(settings, "crm_integration_api_key", None)
    async with _client() as client:
        unset = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": f"Bearer {_VALID_KEY}"},
        )
    assert unset.status_code == 503

    # Empty string is "not configured" too -- must not match an empty token.
    monkeypatch.setattr(settings, "crm_integration_api_key", "")
    async with _client() as client:
        empty = await client.get(
            "/api/v1/integrations/saved-contacts",
            headers={"Authorization": "Bearer "},
        )
    assert empty.status_code == 503


# ── (d) source filter + newest-first ordering ────────────────────────────────


async def test_source_filter_and_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "crm_integration_api_key", _VALID_KEY)
    owner = await _seed_user()
    # A per-run unique source isolates exactly our rows from other dev data.
    unique_source = f"crmtest_{secrets.token_hex(6)}"
    base = _cid()
    id_first = await _seed_saved_contact(owner, contact_id=base, source=unique_source)
    id_second = await _seed_saved_contact(
        owner, contact_id=base + 1, source=unique_source
    )
    # A row under a different source the filter must EXCLUDE.
    await _seed_saved_contact(owner, contact_id=base + 2, source="discovered_email")
    try:
        async with _client() as client:
            match = await client.get(
                "/api/v1/integrations/saved-contacts",
                params={"source": unique_source},
                headers={"Authorization": f"Bearer {_VALID_KEY}"},
            )
            no_match = await client.get(
                "/api/v1/integrations/saved-contacts",
                params={"source": f"nope_{secrets.token_hex(4)}"},
                headers={"Authorization": f"Bearer {_VALID_KEY}"},
            )
        assert match.status_code == 200
        rows = match.json()
        # Exactly our two rows, newest-first (created_at ties -> id DESC).
        assert [row["id"] for row in rows] == [id_second, id_first]
        assert {row["saved_by_id"] for row in rows} == {owner}
        assert {row["source"] for row in rows} == {unique_source}

        assert no_match.status_code == 200
        assert no_match.json() == []
    finally:
        await _cleanup([owner])


# ── (e) limit caps the page size, newest-first preserved ─────────────────────


async def test_limit_caps_result_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?limit=`` bounds the row count while keeping the newest-first order.

    Seeds three rows under a per-run unique source, asks for ``limit=2``, and
    expects exactly the two newest (id DESC on the created_at tie) -- proving
    the cap applies AFTER ordering, so the CRM's default 1000 yields the newest
    1000 rather than an arbitrary slice.
    """
    monkeypatch.setattr(settings, "crm_integration_api_key", _VALID_KEY)
    owner = await _seed_user()
    unique_source = f"crmtest_{secrets.token_hex(6)}"
    base = _cid()
    await _seed_saved_contact(owner, contact_id=base, source=unique_source)
    id_second = await _seed_saved_contact(
        owner, contact_id=base + 1, source=unique_source
    )
    id_third = await _seed_saved_contact(
        owner, contact_id=base + 2, source=unique_source
    )
    try:
        async with _client() as client:
            resp = await client.get(
                "/api/v1/integrations/saved-contacts",
                params={"source": unique_source, "limit": 2},
                headers={"Authorization": f"Bearer {_VALID_KEY}"},
            )
        assert resp.status_code == 200
        rows = resp.json()
        # Exactly the two newest of the three, newest-first.
        assert [row["id"] for row in rows] == [id_third, id_second]
    finally:
        await _cleanup([owner])
