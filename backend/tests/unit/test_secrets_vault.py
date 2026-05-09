"""SP-8 Phase E — vault round-trip + tampering + edge cases."""
from __future__ import annotations

import pytest

from app.secrets.vault import (
    VaultDecryptError,
    WeakPassphraseError,
    decrypt_secrets,
    encrypt_secrets,
)


_GOOD_PP = "correct-horse-battery-staple-2026"


def test_round_trip_recovers_secrets() -> None:
    payload = {
        "binance_api_key": "bk-abc",
        "binance_api_secret": "bs-xyz",
        "telegram_chat_id": "12345",
    }
    blob = encrypt_secrets(payload, passphrase=_GOOD_PP)
    assert isinstance(blob, bytes) and len(blob) > 32
    out = decrypt_secrets(blob, passphrase=_GOOD_PP)
    assert out == payload


def test_round_trip_two_blobs_differ_for_same_input() -> None:
    """Salt + nonce are random per call, so identical input → different blobs."""
    payload = {"x": "y"}
    a = encrypt_secrets(payload, passphrase=_GOOD_PP)
    b = encrypt_secrets(payload, passphrase=_GOOD_PP)
    assert a != b


def test_wrong_passphrase_raises_decrypt_error() -> None:
    blob = encrypt_secrets({"x": "y"}, passphrase=_GOOD_PP)
    with pytest.raises(VaultDecryptError):
        decrypt_secrets(blob, passphrase="wrong-passphrase-2026-xy")


def test_tampered_ciphertext_raises_decrypt_error() -> None:
    blob = encrypt_secrets({"x": "y"}, passphrase=_GOOD_PP)
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF  # flip a bit in the tag
    with pytest.raises(VaultDecryptError):
        decrypt_secrets(bytes(tampered), passphrase=_GOOD_PP)


def test_truncated_blob_raises_value_error() -> None:
    with pytest.raises(ValueError, match="too short"):
        decrypt_secrets(b"short", passphrase=_GOOD_PP)


def test_short_passphrase_raises_when_enforced() -> None:
    with pytest.raises(WeakPassphraseError, match="16 characters"):
        encrypt_secrets({"x": "y"}, passphrase="abc")


def test_short_passphrase_allowed_in_test_mode() -> None:
    """Tests with deterministic short passphrases use the escape hatch."""
    blob = encrypt_secrets(
        {"x": "y"}, passphrase="short",
        enforce_passphrase_strength=False,
    )
    out = decrypt_secrets(blob, passphrase="short")
    assert out == {"x": "y"}


def test_unicode_secret_round_trips() -> None:
    """JSON serialisation must handle non-ASCII keys/values."""
    payload = {"emoji_key": "🔐 secret value with unicode 日本"}
    blob = encrypt_secrets(payload, passphrase=_GOOD_PP)
    assert decrypt_secrets(blob, passphrase=_GOOD_PP) == payload


def test_decrypt_non_dict_payload_raises() -> None:
    """Defensive: if a corrupted-but-valid blob decrypts to a list, refuse it."""
    # Build a blob whose plaintext is a JSON list rather than dict.
    import json
    import os

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = os.urandom(16)
    nonce = os.urandom(12)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32, salt=salt, iterations=200_000,
    )
    key = kdf.derive(_GOOD_PP.encode("utf-8"))
    aes = AESGCM(key)
    plaintext = json.dumps(["not", "a", "dict"]).encode("utf-8")
    ciphertext = aes.encrypt(nonce, plaintext, None)
    blob = salt + nonce + ciphertext

    with pytest.raises(VaultDecryptError, match="not a dict"):
        decrypt_secrets(blob, passphrase=_GOOD_PP)
