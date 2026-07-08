"""DOX Share service — shared core for the admin and public share endpoints.

Everything that isn't an HTTP concern lives here: token/password/cookie
crypto (stdlib ``secrets`` + ``hashlib.scrypt`` + ``hmac`` — no new deps), the
snapshot-from-favorite-list builder, the admin lifecycle ops, and the public
read + brute-force-throttle helpers. Both ``endpoints/shares_admin.py`` and
``endpoints/shares_public.py`` import from here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import AuthUser
from app.models.bank import Bank
from app.models.broker_dealer import BrokerDealer
from app.models.favorite_list import FavoriteList, FavoriteListItem
from app.models.investment_advisor import InvestmentAdvisor
from app.models.lead_share import LeadShare, LeadShareItem
from app.schemas.lead_share import (
    PublicShareItemRow,
    ShareItemAdminRow,
    ShareSkippedItems,
    ShareSummary,
)

# ── Tunables ────────────────────────────────────────────────────────────────
MAX_SHARE_ITEMS = 80
SHARE_TOKEN_BYTES = 32
SHARE_COOKIE_TTL_SECONDS = 43_200  # 12h
UNLOCK_MAX_FAILURES = 10
UNLOCK_WINDOW_SECONDS = 900  # 15m
SHARE_COOKIE_PREFIX = "dox_share_"

# Password: 12 chars from an unambiguous alphabet (no 0/O/1/I/L), grouped
# XXXX-XXXX-XXXX for legibility over the phone. ~59 bits — the online-guess
# ceiling is the per-share brute-force throttle, not the entropy.
_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_PASSWORD_GROUPS = 3
_PASSWORD_GROUP_LEN = 4

# scrypt cost — 16 MiB working set (128*N*r), comfortably under OpenSSL's
# 32 MiB default maxmem. Encoded into the hash string so these can change
# without breaking already-stored rows.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_COOKIE_CONTEXT = "dox-share-v1"


# ══════════════════════════════════════════════════════════════════════════
# Token + password + cookie primitives (pure, sync)
# ══════════════════════════════════════════════════════════════════════════


def generate_share_token() -> str:
    """256-bit URL-safe capability token stored plaintext (a locator, not a
    standalone secret — the password gates the content)."""
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def generate_share_password() -> str:
    groups = [
        "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_GROUP_LEN))
        for _ in range(_PASSWORD_GROUPS)
    ]
    return "-".join(groups)


def hash_share_password(password: str) -> str:
    """Self-describing scrypt string: ``scrypt$N$r$p$<b64salt>$<b64dk>``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_share_password(password: str, stored: str) -> bool:
    """Constant-time verify against a stored ``hash_share_password`` string.

    Callers on the async path wrap this in ``asyncio.to_thread`` so the
    ~50 ms scrypt work never blocks the event loop.
    """
    try:
        scheme, n_s, r_s, p_s, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, expected)


def share_cookie_name(share_id: int) -> str:
    return f"{SHARE_COOKIE_PREFIX}{share_id}"


def _cookie_sig(share_id: int, generation: int, expires_epoch: int) -> str:
    payload = f"{_COOKIE_CONTEXT}|{share_id}|{generation}|{expires_epoch}"
    digest = hmac.new(
        settings.auth_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def mint_share_cookie(share: LeadShare, *, now: datetime) -> tuple[str, str, int]:
    """Return ``(cookie_name, cookie_value, max_age_seconds)``.

    Value = ``v1.{share_id}.{generation}.{expires_epoch}.{sig}``. The
    generation is baked in, so rotating the password (which bumps the
    generation) instantly invalidates every previously minted cookie.
    """
    expires_epoch = int(now.timestamp()) + SHARE_COOKIE_TTL_SECONDS
    sig = _cookie_sig(share.id, share.password_generation, expires_epoch)
    value = f"v1.{share.id}.{share.password_generation}.{expires_epoch}.{sig}"
    return share_cookie_name(share.id), value, SHARE_COOKIE_TTL_SECONDS


def verify_share_cookie(
    share: LeadShare, raw_value: str | None, *, now: datetime
) -> bool:
    if not raw_value:
        return False
    parts = raw_value.split(".")
    if len(parts) != 5:
        return False
    version, sid_s, gen_s, exp_s, sig = parts
    if version != "v1":
        return False
    try:
        sid = int(sid_s)
        gen = int(gen_s)
        exp = int(exp_s)
    except ValueError:
        return False
    if sid != share.id or gen != share.password_generation:
        return False
    if exp <= int(now.timestamp()):
        return False
    # ``sig`` comes straight off the (latin-1 decoded) cookie header; a
    # non-ASCII byte would make str/str compare_digest raise TypeError. Fail
    # closed on any such crafted value rather than 500 on the auth path.
    try:
        expected = _cookie_sig(share.id, share.password_generation, exp)
        return hmac.compare_digest(sig, expected)
    except (TypeError, ValueError):
        return False


def build_share_url(token: str) -> str | None:
    base = settings.share_frontend_base_url
    if not base:
        return None
    return f"{base.rstrip('/')}/share/{token}"


def to_share_summary(
    share: LeadShare, *, item_count: int, created_by_email: str | None = None
) -> ShareSummary:
    return ShareSummary(
        id=share.id,
        name=share.name,
        token=share.token,
        url=build_share_url(share.token),
        item_count=item_count,
        created_by=share.created_by,
        created_by_email=created_by_email,
        created_at=share.created_at,
        revoked_at=share.revoked_at,
        password_rotated_at=share.password_rotated_at,
        view_count=share.view_count,
        last_viewed_at=share.last_viewed_at,
        source_favorite_list_id=share.source_favorite_list_id,
    )


# ══════════════════════════════════════════════════════════════════════════
# Admin ops
# ══════════════════════════════════════════════════════════════════════════


class ShareCreateError(Exception):
    """Raised by :func:`create_share_from_favorite_list`; ``code`` maps to an
    HTTP status in the endpoint (404 / 400 / 400)."""

    def __init__(self, code: str, *, eligible: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.eligible = eligible


def _item_kind(item: FavoriteListItem) -> tuple[str, int] | None:
    """Map a favorite-list row to ``(kind, entity_id)`` for the three
    shareable verticals, or None for a not-yet-shareable kind."""
    if item.broker_dealer_id is not None:
        return "broker_dealer", item.broker_dealer_id
    if item.advisor_id is not None:
        return "advisor", item.advisor_id
    # ``bank_id`` lands via migration 20260708_0001 (parallel worktree); read
    # defensively so this module imports + unit-tests without that column.
    bank_id = getattr(item, "bank_id", None)
    if bank_id is not None:
        return "bank", bank_id
    return None


async def create_share_from_favorite_list(
    db: AsyncSession,
    *,
    favorite_list_id: int,
    name: str | None,
    created_by: str,
) -> tuple[LeadShare, str, ShareSkippedItems]:
    """Snapshot a favorite list into a new share.

    The list is looked up by id only — **not** owner-scoped — because this is
    an admin surface (an admin may export a list built by anyone). Items are
    frozen in the favorites display order (created_at desc, id desc → position
    1..N). Investor / reporting-owner members are skipped (counted, warned);
    the 80-cap applies to the eligible remainder.
    """
    favorite_list = await db.get(FavoriteList, favorite_list_id)
    if favorite_list is None:
        raise ShareCreateError("favorite_list_not_found")

    rows = (
        await db.execute(
            select(FavoriteListItem)
            .where(FavoriteListItem.list_id == favorite_list_id)
            .order_by(
                FavoriteListItem.created_at.desc(), FavoriteListItem.id.desc()
            )
        )
    ).scalars().all()

    eligible: list[tuple[str, int]] = []
    skipped = ShareSkippedItems()
    for item in rows:
        kind = _item_kind(item)
        if kind is not None:
            eligible.append(kind)
        elif item.institutional_investor_id is not None:
            skipped.institutional_investors += 1
        elif item.reporting_owner_id is not None:
            skipped.reporting_owners += 1

    if not eligible:
        raise ShareCreateError("share_source_empty")
    if len(eligible) > MAX_SHARE_ITEMS:
        raise ShareCreateError("share_too_large", eligible=len(eligible))

    password = generate_share_password()
    share = LeadShare(
        token=generate_share_token(),
        name=(name or favorite_list.name)[:120],
        password_hash=hash_share_password(password),
        created_by=created_by,
        source_favorite_list_id=favorite_list.id,
    )
    db.add(share)
    await db.flush()

    for position, (kind, entity_id) in enumerate(eligible, start=1):
        item = LeadShareItem(share_id=share.id, position=position)
        if kind == "broker_dealer":
            item.broker_dealer_id = entity_id
        elif kind == "advisor":
            item.advisor_id = entity_id
        else:
            item.bank_id = entity_id
        db.add(item)
    await db.flush()
    # Load DB-side server defaults (created_at, view_count, generation) onto
    # the object so the caller can build a ShareSummary without a round-trip.
    await db.refresh(share)

    return share, password, skipped


async def list_shares(db: AsyncSession) -> list[tuple[LeadShare, int, str | None]]:
    """All shares (newest first) with item counts + creator email."""
    count_sq = (
        select(
            LeadShareItem.share_id.label("share_id"),
            func.count(LeadShareItem.id).label("cnt"),
        )
        .group_by(LeadShareItem.share_id)
        .subquery()
    )
    stmt = (
        select(LeadShare, func.coalesce(count_sq.c.cnt, 0), AuthUser.email)
        .outerjoin(count_sq, LeadShare.id == count_sq.c.share_id)
        .outerjoin(AuthUser, LeadShare.created_by == AuthUser.id)
        .order_by(LeadShare.created_at.desc())
    )
    result = await db.execute(stmt)
    return [(share, int(count), email) for share, count, email in result.all()]


async def get_share(db: AsyncSession, share_id: int) -> LeadShare | None:
    return await db.get(LeadShare, share_id)


async def _creator_email(db: AsyncSession, share: LeadShare) -> str | None:
    if not share.created_by:
        return None
    return (
        await db.execute(select(AuthUser.email).where(AuthUser.id == share.created_by))
    ).scalar_one_or_none()


async def _load_entity_maps(
    db: AsyncSession, items: list[LeadShareItem]
) -> tuple[dict[int, object], dict[int, object], dict[int, object]]:
    """Batch-fetch the headline columns for every referenced entity, keyed by
    id — three small ``IN`` queries, no per-item round-trips."""
    bd_ids = [i.broker_dealer_id for i in items if i.broker_dealer_id is not None]
    adv_ids = [i.advisor_id for i in items if i.advisor_id is not None]
    bank_ids = [i.bank_id for i in items if i.bank_id is not None]

    bd_map: dict[int, object] = {}
    adv_map: dict[int, object] = {}
    bank_map: dict[int, object] = {}

    if bd_ids:
        rows = await db.execute(
            select(
                BrokerDealer.id,
                BrokerDealer.name,
                BrokerDealer.city,
                BrokerDealer.state,
                BrokerDealer.crd_number,
                BrokerDealer.latest_net_capital,
                BrokerDealer.health_status,
                BrokerDealer.current_clearing_partner,
            ).where(BrokerDealer.id.in_(bd_ids))
        )
        bd_map = {row.id: row for row in rows.all()}
    if adv_ids:
        rows = await db.execute(
            select(
                InvestmentAdvisor.id,
                InvestmentAdvisor.name,
                InvestmentAdvisor.city,
                InvestmentAdvisor.state,
                InvestmentAdvisor.crd_number,
                InvestmentAdvisor.regulatory_aum,
                InvestmentAdvisor.total_clients,
            ).where(InvestmentAdvisor.id.in_(adv_ids))
        )
        adv_map = {row.id: row for row in rows.all()}
    if bank_ids:
        rows = await db.execute(
            select(
                Bank.id,
                Bank.name,
                Bank.city,
                Bank.state,
                Bank.charter_status,
                Bank.charter_type,
                Bank.asset,
            ).where(Bank.id.in_(bank_ids))
        )
        bank_map = {row.id: row for row in rows.all()}

    return bd_map, adv_map, bank_map


async def get_share_detail(
    db: AsyncSession, share_id: int
) -> tuple[LeadShare, int, str | None, list[ShareItemAdminRow]] | None:
    share = await db.get(LeadShare, share_id)
    if share is None:
        return None
    items = (
        await db.execute(
            select(LeadShareItem)
            .where(LeadShareItem.share_id == share_id)
            .order_by(LeadShareItem.position)
        )
    ).scalars().all()
    bd_map, adv_map, bank_map = await _load_entity_maps(db, items)

    admin_rows: list[ShareItemAdminRow] = []
    for item in items:
        kind = _item_kind(item)
        if kind is None:
            continue
        kind_name, entity_id = kind
        source = {"broker_dealer": bd_map, "advisor": adv_map, "bank": bank_map}[
            kind_name
        ].get(entity_id)
        admin_rows.append(
            ShareItemAdminRow(
                item_id=item.id,
                kind=kind_name,
                entity_id=entity_id,
                name=getattr(source, "name", None),
                position=item.position,
            )
        )

    email = await _creator_email(db, share)
    return share, len(items), email, admin_rows


async def rotate_password(db: AsyncSession, share: LeadShare, *, now: datetime) -> str:
    """Rotate to a fresh password; bumping ``password_generation`` kills every
    outstanding unlock cookie. Also clears the brute-force counter."""
    password = generate_share_password()
    share.password_hash = hash_share_password(password)
    share.password_generation += 1
    share.password_rotated_at = now
    share.failed_unlocks = 0
    share.failed_unlock_window_started_at = None
    await db.commit()
    return password


async def revoke_share(db: AsyncSession, share: LeadShare, *, now: datetime) -> None:
    share.revoked_at = now
    await db.commit()


async def unrevoke_share(db: AsyncSession, share: LeadShare) -> None:
    share.revoked_at = None
    await db.commit()


async def delete_share(db: AsyncSession, share: LeadShare) -> None:
    await db.delete(share)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════
# Public ops
# ══════════════════════════════════════════════════════════════════════════


async def get_active_share_by_token(db: AsyncSession, token: str) -> LeadShare | None:
    """Resolve a live (non-revoked) share by token. Returns None for unknown
    AND revoked tokens so the endpoint can answer both with an identical 404."""
    return (
        await db.execute(
            select(LeadShare).where(
                LeadShare.token == token, LeadShare.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def get_item_for_share(
    db: AsyncSession, *, share_id: int, item_id: int
) -> LeadShareItem | None:
    """The membership / IDOR guard: an item id only resolves within its own
    share. A foreign item id returns None → the endpoint 404s."""
    return (
        await db.execute(
            select(LeadShareItem).where(
                LeadShareItem.id == item_id, LeadShareItem.share_id == share_id
            )
        )
    ).scalar_one_or_none()


def unlock_retry_after(share: LeadShare, *, now: datetime) -> int:
    """0 if an unlock attempt is allowed now, else seconds until the
    fixed window resets (for the 429 ``Retry-After`` header)."""
    if (
        share.failed_unlocks >= UNLOCK_MAX_FAILURES
        and share.failed_unlock_window_started_at is not None
    ):
        elapsed = (now - share.failed_unlock_window_started_at).total_seconds()
        if elapsed < UNLOCK_WINDOW_SECONDS:
            return max(1, math.ceil(UNLOCK_WINDOW_SECONDS - elapsed))
    return 0


async def register_failed_unlock(
    db: AsyncSession, share: LeadShare, *, now: datetime
) -> None:
    window_start = share.failed_unlock_window_started_at
    if (
        window_start is None
        or (now - window_start).total_seconds() >= UNLOCK_WINDOW_SECONDS
    ):
        share.failed_unlocks = 1
        share.failed_unlock_window_started_at = now
    else:
        # Atomic increment so parallel Cloud Run instances can't undercount
        # (worst case a couple of extra attempts slip through, never fewer).
        await db.execute(
            update(LeadShare)
            .where(LeadShare.id == share.id)
            .values(failed_unlocks=LeadShare.failed_unlocks + 1)
        )
    await db.commit()


async def record_successful_unlock(
    db: AsyncSession, share: LeadShare, *, now: datetime
) -> None:
    share.failed_unlocks = 0
    share.failed_unlock_window_started_at = None
    await db.execute(
        update(LeadShare)
        .where(LeadShare.id == share.id)
        .values(view_count=LeadShare.view_count + 1, last_viewed_at=now)
    )
    await db.commit()


async def touch_last_viewed(
    db: AsyncSession, share: LeadShare, *, now: datetime
) -> None:
    """Refresh ``last_viewed_at`` behind a 60s skew gate so authorized read
    bursts don't write on every request."""
    last = share.last_viewed_at
    if last is None or (now - last).total_seconds() > 60:
        share.last_viewed_at = now
        await db.commit()


async def list_share_items(
    db: AsyncSession, share: LeadShare
) -> list[PublicShareItemRow]:
    """Ordered public list rows; entities deleted since the snapshot (NULL
    join) are skipped."""
    items = (
        await db.execute(
            select(LeadShareItem)
            .where(LeadShareItem.share_id == share.id)
            .order_by(LeadShareItem.position)
        )
    ).scalars().all()
    bd_map, adv_map, bank_map = await _load_entity_maps(db, items)

    rows: list[PublicShareItemRow] = []
    for item in items:
        kind = _item_kind(item)
        if kind is None:
            continue
        kind_name, entity_id = kind
        if kind_name == "broker_dealer":
            src = bd_map.get(entity_id)
            if src is None:
                continue
            rows.append(
                PublicShareItemRow(
                    item_id=item.id,
                    kind="broker_dealer",
                    position=item.position,
                    name=src.name,
                    city=src.city,
                    state=src.state,
                    crd_number=src.crd_number,
                    latest_net_capital=src.latest_net_capital,
                    health_status=src.health_status,
                    current_clearing_partner=src.current_clearing_partner,
                )
            )
        elif kind_name == "advisor":
            src = adv_map.get(entity_id)
            if src is None:
                continue
            rows.append(
                PublicShareItemRow(
                    item_id=item.id,
                    kind="advisor",
                    position=item.position,
                    name=src.name,
                    city=src.city,
                    state=src.state,
                    crd_number=src.crd_number,
                    regulatory_aum=src.regulatory_aum,
                    total_clients=src.total_clients,
                )
            )
        else:
            src = bank_map.get(entity_id)
            if src is None:
                continue
            rows.append(
                PublicShareItemRow(
                    item_id=item.id,
                    kind="bank",
                    position=item.position,
                    name=src.name,
                    city=src.city,
                    state=src.state,
                    charter_status=src.charter_status,
                    charter_type=src.charter_type,
                    asset=src.asset,
                )
            )
    return rows
