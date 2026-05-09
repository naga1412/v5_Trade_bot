"""SP-8 Phase E — TOTP setup, verify, grace window, lockout."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyotp

from app.secrets.totp import (
    LockoutTracker,
    setup,
    verify,
    verify_backup_code,
)


# ---- Setup -----------------------------------------------------------------


def test_setup_returns_provisioning_uri_and_backup_codes() -> None:
    s = setup(account_email="user@example.com")
    assert s.secret  # non-empty base32 string
    assert s.provisioning_uri.startswith("otpauth://totp/")
    # pyotp URL-encodes the @ in emails — match the encoded form.
    assert "user%40example.com" in s.provisioning_uri
    assert "trading-radar" in s.provisioning_uri
    assert len(s.backup_codes) == 10
    assert all(len(c) == 10 for c in s.backup_codes)
    # All distinct
    assert len(set(s.backup_codes)) == 10


def test_setup_custom_issuer_appears_in_uri() -> None:
    s = setup(account_email="x@y", issuer="my-app")
    assert "issuer=my-app" in s.provisioning_uri


# ---- Verify ----------------------------------------------------------------


def test_verify_accepts_current_step_code() -> None:
    s = setup(account_email="user@x")
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    code = pyotp.TOTP(s.secret, interval=30).at(now.timestamp())
    assert verify(s.secret, code, now=now) is True


def test_verify_accepts_previous_step_for_clock_skew() -> None:
    """30s grace window — code from one step ago should still verify."""
    s = setup(account_email="user@x")
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    older_step = now - timedelta(seconds=30)
    code = pyotp.TOTP(s.secret, interval=30).at(older_step.timestamp())
    assert verify(s.secret, code, now=now) is True


def test_verify_rejects_two_steps_old_code() -> None:
    """Outside the ±1 step window, codes are rejected."""
    s = setup(account_email="user@x")
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    too_old = now - timedelta(seconds=120)
    code = pyotp.TOTP(s.secret, interval=30).at(too_old.timestamp())
    assert verify(s.secret, code, now=now) is False


def test_verify_rejects_random_code() -> None:
    s = setup(account_email="user@x")
    assert verify(s.secret, "000000") is False
    assert verify(s.secret, "123456") is False or verify(s.secret, "123457") is False


def test_verify_rejects_non_six_digit_input() -> None:
    s = setup(account_email="user@x")
    assert verify(s.secret, "") is False
    assert verify(s.secret, "abc") is False
    assert verify(s.secret, "1234") is False
    assert verify(s.secret, "1234567") is False


# ---- Backup codes ----------------------------------------------------------


def test_backup_code_consumed_on_use() -> None:
    s = setup(account_email="user@x")
    target = s.backup_codes[3]
    matched, remaining = verify_backup_code(s.backup_codes, target)
    assert matched is True
    assert len(remaining) == 9
    assert target not in remaining


def test_backup_code_case_insensitive() -> None:
    s = setup(account_email="user@x")
    target = s.backup_codes[0]
    matched, _ = verify_backup_code(s.backup_codes, target.lower())
    assert matched is True


def test_backup_code_rejects_unknown() -> None:
    s = setup(account_email="user@x")
    matched, remaining = verify_backup_code(s.backup_codes, "ZZZZZZZZZZ")
    assert matched is False
    assert remaining == s.backup_codes  # unchanged


def test_backup_code_rejects_empty() -> None:
    s = setup(account_email="user@x")
    matched, _ = verify_backup_code(s.backup_codes, "")
    assert matched is False


# ---- Lockout ---------------------------------------------------------------


def test_lockout_locks_after_5_fails_in_window() -> None:
    tracker = LockoutTracker()
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(4):
        assert tracker.record_fail(1, now=now + timedelta(seconds=i * 10)) is False
    # 5th fail → locked
    assert tracker.record_fail(1, now=now + timedelta(seconds=50)) is True
    assert tracker.is_locked(1, now=now + timedelta(seconds=51)) is True


def test_lockout_does_not_lock_for_old_fails_outside_window() -> None:
    tracker = LockoutTracker()
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    # 4 fails 11 minutes ago
    for i in range(4):
        tracker.record_fail(1, now=now - timedelta(minutes=11) + timedelta(seconds=i))
    # New fail now → only 1 in the 10-min window
    assert tracker.record_fail(1, now=now) is False
    assert tracker.is_locked(1, now=now) is False


def test_lockout_unlocks_after_duration() -> None:
    tracker = LockoutTracker()
    t0 = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        tracker.record_fail(1, now=t0 + timedelta(seconds=i))
    assert tracker.is_locked(1, now=t0 + timedelta(seconds=6)) is True
    # Lock fires at the 5th fail (t0+4s) and expires +1h later (t0+1h+4s).
    # Step well past that to confirm unlock.
    later = t0 + timedelta(hours=1, seconds=30)
    assert tracker.is_locked(1, now=later) is False


def test_lockout_record_success_clears_state() -> None:
    tracker = LockoutTracker()
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(3):
        tracker.record_fail(1, now=now)
    tracker.record_success(1)
    # Now we can fail 4 more times without lock
    for i in range(4):
        assert tracker.record_fail(1, now=now + timedelta(seconds=i)) is False


def test_lockout_isolates_users() -> None:
    tracker = LockoutTracker()
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    # User 1 fails 5 times → locked
    for _ in range(5):
        tracker.record_fail(1, now=now)
    # User 2 unaffected
    assert tracker.is_locked(2, now=now) is False
