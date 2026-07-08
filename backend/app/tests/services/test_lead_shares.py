"""Pure-unit tests for the DOX Share crypto/cookie/throttle primitives.

No app, no database — just ``services/lead_shares.py``'s stdlib-only helpers.
Runs in the default (non-integration) suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import lead_shares as svc

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


# ── password ────────────────────────────────────────────────────────────────


def test_password_format_and_alphabet() -> None:
    pw = svc.generate_share_password()
    assert len(pw) == 14  # 3×4 chars + 2 dashes
    groups = pw.split("-")
    assert len(groups) == 3 and all(len(g) == 4 for g in groups)
    body = pw.replace("-", "")
    # unambiguous alphabet: never 0/O/1/I/L
    assert all(c in svc._PASSWORD_ALPHABET for c in body)
    assert not (set("O01IL") & set(body))


def test_passwords_are_distinct() -> None:
    assert len({svc.generate_share_password() for _ in range(50)}) > 1


def test_hash_verify_roundtrip_and_rejection() -> None:
    pw = svc.generate_share_password()
    stored = svc.hash_share_password(pw)
    assert stored.startswith("scrypt$16384$8$1$")
    assert svc.verify_share_password(pw, stored) is True
    assert svc.verify_share_password("WRONG-PASS-2345", stored) is False


def test_hash_is_salted_unique() -> None:
    pw = "ABCD-EFGH-JKMN"
    assert svc.hash_share_password(pw) != svc.hash_share_password(pw)


def test_verify_rejects_tampered_or_garbage_stored() -> None:
    pw = svc.generate_share_password()
    stored = svc.hash_share_password(pw)
    tampered = stored[:-2] + ("AA" if not stored.endswith("AA") else "BB")
    assert svc.verify_share_password(pw, tampered) is False
    assert svc.verify_share_password(pw, "not-a-hash") is False
    assert svc.verify_share_password(pw, "md5$x$y") is False


# ── token ───────────────────────────────────────────────────────────────────


def test_token_length_and_charset() -> None:
    tok = svc.generate_share_token()
    assert len(tok) >= 43
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert set(tok) <= allowed
    assert len({svc.generate_share_token() for _ in range(20)}) == 20


# ── cookie ──────────────────────────────────────────────────────────────────


def _share(share_id: int = 7, generation: int = 3):
    return SimpleNamespace(id=share_id, password_generation=generation)


def test_cookie_roundtrip_and_name() -> None:
    share = _share()
    name, value, max_age = svc.mint_share_cookie(share, now=NOW)
    assert name == "dox_share_7"
    assert max_age == svc.SHARE_COOKIE_TTL_SECONDS
    assert svc.verify_share_cookie(share, value, now=NOW) is True


def test_cookie_rejects_wrong_share_id() -> None:
    _, value, _ = svc.mint_share_cookie(_share(share_id=7), now=NOW)
    assert svc.verify_share_cookie(_share(share_id=8), value, now=NOW) is False


def test_cookie_rejects_rotated_generation() -> None:
    _, value, _ = svc.mint_share_cookie(_share(generation=3), now=NOW)
    # rotation bumps password_generation → old cookie dies
    assert svc.verify_share_cookie(_share(generation=4), value, now=NOW) is False


def test_cookie_rejects_expired() -> None:
    share = _share()
    _, value, _ = svc.mint_share_cookie(share, now=NOW)
    later = NOW + timedelta(seconds=svc.SHARE_COOKIE_TTL_SECONDS + 1)
    assert svc.verify_share_cookie(share, value, now=later) is False


def test_cookie_rejects_flipped_signature() -> None:
    share = _share()
    _, value, _ = svc.mint_share_cookie(share, now=NOW)
    head, _, sig = value.rpartition(".")
    forged = head + "." + ("A" if not sig.startswith("A") else "B") + sig[1:]
    assert svc.verify_share_cookie(share, forged, now=NOW) is False


def test_cookie_rejects_garbage_and_none() -> None:
    share = _share()
    for bad in (None, "", "v1.7.3", "garbage", "v2.7.3.999.sig"):
        assert svc.verify_share_cookie(share, bad, now=NOW) is False


# ── unlock throttle window math ──────────────────────────────────────────────


def _throttle_share(failed: int, window_start):
    return SimpleNamespace(
        failed_unlocks=failed, failed_unlock_window_started_at=window_start
    )


def test_retry_after_under_limit_is_zero() -> None:
    assert svc.unlock_retry_after(_throttle_share(3, NOW), now=NOW) == 0


def test_retry_after_at_limit_inside_window() -> None:
    share = _throttle_share(svc.UNLOCK_MAX_FAILURES, NOW)
    after = NOW + timedelta(seconds=60)
    retry = svc.unlock_retry_after(share, now=after)
    assert retry == svc.UNLOCK_WINDOW_SECONDS - 60


def test_retry_after_zero_once_window_expired() -> None:
    share = _throttle_share(svc.UNLOCK_MAX_FAILURES, NOW)
    after = NOW + timedelta(seconds=svc.UNLOCK_WINDOW_SECONDS + 5)
    assert svc.unlock_retry_after(share, now=after) == 0


def test_retry_after_no_window_is_zero() -> None:
    share = _throttle_share(svc.UNLOCK_MAX_FAILURES, None)
    assert svc.unlock_retry_after(share, now=NOW) == 0
