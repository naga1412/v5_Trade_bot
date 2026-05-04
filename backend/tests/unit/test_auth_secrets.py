"""Tests for AES-256-GCM per-user secrets module (SP-0.7 Phase F1+F2)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.models import Base, User
from app.auth.secrets import (
    SecretsConfigError,
    decrypt_for_user,
    derive_user_key,
    encrypt_for_user,
    get_binance_keys,
    get_telegram,
    set_binance_keys,
    set_telegram,
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


@pytest.mark.asyncio
async def test_set_get_binance_keys_round_trip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        u = User(email="a@x.com", display_name="A")
        session.add(u)
        await session.commit()
        await session.refresh(u)

        await set_binance_keys(
            session,
            user=u,
            api_key="bnc-key-XYZ",
            api_secret="bnc-secret-ABC",
            passphrase=PASSPHRASE,
        )
        await session.commit()
        await session.refresh(u)
        uid = u.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        loaded = await session.get(User, uid)
        assert loaded is not None
        keys = get_binance_keys(loaded, passphrase=PASSPHRASE)

    assert keys.api_key == "bnc-key-XYZ"
    assert keys.api_secret == "bnc-secret-ABC"


@pytest.mark.asyncio
async def test_set_get_telegram_round_trip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        u = User(email="b@x.com", display_name="B")
        session.add(u)
        await session.commit()
        await session.refresh(u)

        await set_telegram(
            session,
            user=u,
            bot_token="bot-token-123",
            chat_id="chat-456",
            passphrase=PASSPHRASE,
        )
        await session.commit()
        await session.refresh(u)
        tg = get_telegram(u, passphrase=PASSPHRASE)

    assert tg.bot_token == "bot-token-123"
    assert tg.chat_id == "chat-456"


def test_get_binance_keys_unconfigured_raises() -> None:
    u = User(id=1, email="z@x.com", display_name="Z")
    with pytest.raises(SecretsConfigError):
        get_binance_keys(u, passphrase=PASSPHRASE)


def test_get_telegram_unconfigured_raises() -> None:
    u = User(id=1, email="z@x.com", display_name="Z")
    with pytest.raises(SecretsConfigError):
        get_telegram(u, passphrase=PASSPHRASE)
