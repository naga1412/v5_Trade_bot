"""TOTP (time-based one-time password) setup + verify, thin wrapper over pyotp.

The secret is what gets stored encrypted in `users.totp_secret_encrypted`; the
provisioning URI is rendered to a QR code on the frontend (any QR library).

Backup codes: 8 alphanumeric, 8 chars each, generated at setup time and shown
to the user once. Stored as a JSON-encoded list, encrypted with the same per-user
AES key as the TOTP secret.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Final

import pyotp

_TOTP_DIGITS: Final[int] = 6
_BACKUP_CODE_LEN: Final[int] = 8
_BACKUP_CODE_COUNT: Final[int] = 8
_BACKUP_ALPHABET: Final[str] = string.ascii_uppercase + string.digits


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    provisioning_uri: str


def generate_totp_setup(*, account_email: str) -> TotpSetup:
    """Generate a fresh TOTP secret + provisioning URI for `account_email`."""
    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=account_email, issuer_name="trading-radar",
    )
    return TotpSetup(secret=secret, provisioning_uri=uri)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a TOTP `code` against `secret`. valid_window=1 -> +/- 30s skew."""
    if not code or len(code) != _TOTP_DIGITS or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


def generate_backup_codes(
    *, count: int = _BACKUP_CODE_COUNT, length: int = _BACKUP_CODE_LEN,
) -> list[str]:
    """Generate `count` unique alphanumeric backup codes of `length` chars."""
    out: set[str] = set()
    while len(out) < count:
        out.add("".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(length)))
    return sorted(out)
