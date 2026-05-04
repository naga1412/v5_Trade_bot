"""Tests for AES-256-GCM per-user secrets module (SP-0.7 Phase F1)."""

import pytest

from app.auth.secrets import (
    SecretsConfigError,
    decrypt_for_user,
    derive_user_key,
    encrypt_for_user,
)

PASSPHRASE = "test-passphrase-with-mixed-Case-and-symbol-!"


def test_derive_user_key_is_stable_per_user_id() -> None:
    k1a = derive_user_key(PASSPHRASE, user_id=1)
    k1b = derive_user_key(PASSPHRASE, user_id=1)
    k2 = derive_user_key(PASSPHRASE, user_id=2)
    assert k1a == k1b
    assert k1a != k2
    assert len(k1a) == 32  # AES-256


def test_encrypt_decrypt_round_trip() -> None:
    plaintext = "binance-secret-abc-123"
    ciphertext = encrypt_for_user(plaintext, passphrase=PASSPHRASE, user_id=1)
    assert ciphertext != plaintext
    # base64 alphabet sanity (letters, digits, +, /, =)
    assert all(
        c.isalnum() or c in "+/=" for c in ciphertext
    )
    recovered = decrypt_for_user(ciphertext, passphrase=PASSPHRASE, user_id=1)
    assert recovered == plaintext


def test_decrypt_with_wrong_user_id_fails() -> None:
    ciphertext = encrypt_for_user("secret", passphrase=PASSPHRASE, user_id=1)
    with pytest.raises(SecretsConfigError):
        decrypt_for_user(ciphertext, passphrase=PASSPHRASE, user_id=2)


def test_decrypt_with_tampered_ciphertext_fails() -> None:
    ciphertext = encrypt_for_user("secret", passphrase=PASSPHRASE, user_id=1)
    # Flip a byte in the middle
    midpoint = len(ciphertext) // 2
    flip = "A" if ciphertext[midpoint] != "A" else "B"
    tampered = ciphertext[:midpoint] + flip + ciphertext[midpoint + 1 :]
    with pytest.raises(SecretsConfigError):
        decrypt_for_user(tampered, passphrase=PASSPHRASE, user_id=1)


def test_two_encryptions_of_same_plaintext_differ() -> None:
    """GCM nonce reuse would be catastrophic; ensure each call uses a fresh nonce."""
    a = encrypt_for_user("secret", passphrase=PASSPHRASE, user_id=1)
    b = encrypt_for_user("secret", passphrase=PASSPHRASE, user_id=1)
    assert a != b


def test_short_passphrase_rejected() -> None:
    with pytest.raises(SecretsConfigError):
        encrypt_for_user("secret", passphrase="too-short", user_id=1)
