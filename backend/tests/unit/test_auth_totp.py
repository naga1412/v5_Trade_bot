"""Tests for TOTP setup + verify (SP-0.7 Phase F3)."""

import pyotp

from app.auth.totp import (
    generate_backup_codes,
    generate_totp_setup,
    verify_totp,
)


def test_generate_totp_setup_returns_secret_and_qr_uri() -> None:
    setup = generate_totp_setup(account_email="alice@example.com")
    assert len(setup.secret) >= 16
    assert setup.provisioning_uri.startswith("otpauth://totp/")
    # `@` is URL-encoded as `%40` by pyotp's provisioning_uri.
    assert "alice%40example.com" in setup.provisioning_uri
    lower_uri = setup.provisioning_uri.lower()
    assert "issuer=trading-radar" in lower_uri


def test_verify_totp_accepts_current_code() -> None:
    setup = generate_totp_setup(account_email="alice@example.com")
    code = pyotp.TOTP(setup.secret).now()
    assert verify_totp(setup.secret, code) is True


def test_verify_totp_rejects_wrong_code() -> None:
    setup = generate_totp_setup(account_email="alice@example.com")
    assert verify_totp(setup.secret, "000000") is False


def test_verify_totp_rejects_malformed_code() -> None:
    setup = generate_totp_setup(account_email="alice@example.com")
    assert verify_totp(setup.secret, "abc") is False
    assert verify_totp(setup.secret, "") is False
    assert verify_totp(setup.secret, "1234567") is False


def test_generate_backup_codes_returns_8_unique() -> None:
    codes = generate_backup_codes()
    assert len(codes) == 8
    assert len(set(codes)) == 8
    for c in codes:
        assert len(c) == 8
        assert c.isalnum()
