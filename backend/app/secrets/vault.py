"""SP-8 Phase E — AES-256-GCM secret vault.

Spec §9. Round-trip encrypt → bytes → decrypt with a passphrase. The
passphrase derives a 32-byte AES key via PBKDF2-HMAC-SHA256 over a
random per-file salt with 200,000 iterations (OWASP 2023 recommendation
for SHA-256 PBKDF2). The encrypted blob layout is:

    salt (16) || nonce (12) || ciphertext+tag (variable)

The first 16 bytes are the salt, the next 12 are the GCM nonce, the
remainder is the AES-GCM ciphertext with the 16-byte tag appended (the
cryptography library handles tag concatenation internally — encrypt()
returns ciphertext_with_tag, decrypt() expects the same).

Wrong passphrase or any tampering of the blob → ``cryptography``
raises ``InvalidTag``; we wrap that in ``VaultDecryptError`` so callers
don't depend on the underlying library type.

Usage:

    secrets = {"binance_api_key": "...", "binance_api_secret": "..."}
    blob = encrypt_secrets(secrets, passphrase="...")
    Path("secrets.enc").write_bytes(blob)

    blob = Path("secrets.enc").read_bytes()
    secrets = decrypt_secrets(blob, passphrase="...")
    api_key = secrets["binance_api_key"]
"""
from __future__ import annotations

import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
_PBKDF2_ITERATIONS = 200_000

_MIN_PASSPHRASE_LEN = 16


class VaultError(Exception):
    """Base for all vault errors."""


class VaultDecryptError(VaultError):
    """Wrong passphrase or tampered blob."""


class WeakPassphraseError(VaultError):
    """Passphrase fails the minimum-length check."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secrets(
    secrets: dict[str, Any],
    *,
    passphrase: str,
    enforce_passphrase_strength: bool = True,
) -> bytes:
    """Encrypt a dict of secrets to bytes.

    The dict is serialised as compact JSON, then encrypted with a fresh
    salt + nonce. Output layout: salt (16) || nonce (12) || ciphertext.

    Args:
        secrets: arbitrary JSON-serialisable dict (typically string→string).
        passphrase: at least 16 chars unless ``enforce_passphrase_strength``
            is False (escape hatch for tests with deterministic short
            passphrases).
        enforce_passphrase_strength: set False only in tests.

    Raises:
        WeakPassphraseError: passphrase is too short and enforcement is on.
    """
    if enforce_passphrase_strength and len(passphrase) < _MIN_PASSPHRASE_LEN:
        raise WeakPassphraseError(
            f"passphrase must be at least {_MIN_PASSPHRASE_LEN} characters"
        )
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)
    plaintext = json.dumps(secrets, separators=(",", ":")).encode("utf-8")
    ciphertext = aes.encrypt(nonce, plaintext, associated_data=None)
    return salt + nonce + ciphertext


def decrypt_secrets(
    blob: bytes,
    *,
    passphrase: str,
) -> dict[str, Any]:
    """Decrypt a blob produced by ``encrypt_secrets``.

    Raises:
        VaultDecryptError: wrong passphrase or tampered blob.
        ValueError: blob is shorter than the salt+nonce header (truncated).
    """
    if len(blob) < _SALT_LEN + _NONCE_LEN:
        raise ValueError(
            f"vault blob too short ({len(blob)} bytes); minimum "
            f"{_SALT_LEN + _NONCE_LEN} for header"
        )
    salt = blob[:_SALT_LEN]
    nonce = blob[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext = blob[_SALT_LEN + _NONCE_LEN :]
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)
    try:
        plaintext = aes.decrypt(nonce, ciphertext, associated_data=None)
    except InvalidTag as e:
        raise VaultDecryptError("wrong passphrase or tampered vault") from e
    result = json.loads(plaintext.decode("utf-8"))
    if not isinstance(result, dict):
        raise VaultDecryptError("vault payload is not a dict")
    return result


__all__ = [
    "VaultError",
    "VaultDecryptError",
    "WeakPassphraseError",
    "encrypt_secrets",
    "decrypt_secrets",
]
