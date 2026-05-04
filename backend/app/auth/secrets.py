"""AES-256-GCM encryption keyed per user (SP-0.7 Phase F1).

A master passphrase (env MASTER_PASSPHRASE, >=16 chars) is HKDF-expanded with
the user's id as info into a 32-byte AES key. Each ciphertext carries its own
random 12-byte nonce, so nonce reuse is impossible across encryptions.

On-disk format: base64(nonce || ciphertext || tag). The 16-byte GCM tag is
appended to the ciphertext by the cryptography library.

Per-user salt = HKDF(SHA-256, master_key=SHA256(passphrase), salt=str(user_id),
info=b"trading-radar-user-key-v1") -> 32-byte key. Spec: §3 per-user encrypted
columns. Library: cryptography (already a transitive dep via httpx).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.models import User

_KEY_SIZE: Final[int] = 32  # AES-256
_NONCE_SIZE: Final[int] = 12
_GCM_TAG_SIZE: Final[int] = 16
_INFO: Final[bytes] = b"trading-radar-user-key-v1"
_MIN_PASSPHRASE_LEN: Final[int] = 16


class SecretsConfigError(Exception):
    """Encryption / decryption failed (wrong key, tampered ciphertext, etc.)."""


def _master_key_from_passphrase(passphrase: str) -> bytes:
    """SHA-256 of passphrase. We then HKDF-expand per user.

    A passphrase shorter than 16 chars is rejected -- too weak to derive 32-byte
    keys safely. Deployments running the dev fallback see a startup warning.
    """
    if not passphrase or len(passphrase) < _MIN_PASSPHRASE_LEN:
        raise SecretsConfigError(
            f"MASTER_PASSPHRASE must be >= {_MIN_PASSPHRASE_LEN} chars"
        )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(passphrase.encode("utf-8"))
    return digest.finalize()


def derive_user_key(passphrase: str, *, user_id: int) -> bytes:
    """HKDF-expand master key with user_id as salt -> 32-byte AES key."""
    master = _master_key_from_passphrase(passphrase)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=str(user_id).encode("utf-8"),
        info=_INFO,
    )
    return hkdf.derive(master)


def encrypt_for_user(plaintext: str, *, passphrase: str, user_id: int) -> str:
    """AES-256-GCM encrypt; returns base64(nonce || ciphertext || tag)."""
    key = derive_user_key(passphrase, user_id=user_id)
    aes = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_for_user(ciphertext: str, *, passphrase: str, user_id: int) -> str:
    """Inverse of encrypt_for_user. Raises SecretsConfigError on any failure."""
    try:
        raw = base64.b64decode(ciphertext.encode("ascii"))
    except Exception as e:  # noqa: BLE001
        raise SecretsConfigError(f"invalid base64 ciphertext: {e}") from e
    if len(raw) < _NONCE_SIZE + _GCM_TAG_SIZE:
        raise SecretsConfigError("ciphertext too short")
    nonce, ct = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    key = derive_user_key(passphrase, user_id=user_id)
    aes = AESGCM(key)
    try:
        pt = aes.decrypt(nonce, ct, associated_data=None)
    except InvalidTag as e:
        raise SecretsConfigError(
            "decryption failed (wrong key or tampered ciphertext)"
        ) from e
    return pt.decode("utf-8")


# --- F2: per-user secrets helpers (binance keys, telegram) ------------------


@dataclass(frozen=True)
class BinanceKeys:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


async def set_binance_keys(
    session: AsyncSession,
    *,
    user: User,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> None:
    """Encrypt + persist binance api_key + api_secret on a User row."""
    user.binance_api_key_encrypted = encrypt_for_user(
        api_key, passphrase=passphrase, user_id=user.id,
    )
    user.binance_api_secret_encrypted = encrypt_for_user(
        api_secret, passphrase=passphrase, user_id=user.id,
    )
    await session.flush()


def get_binance_keys(user: User, *, passphrase: str) -> BinanceKeys:
    """Decrypt and return BinanceKeys for `user`. Raises if unconfigured."""
    if not user.binance_api_key_encrypted or not user.binance_api_secret_encrypted:
        raise SecretsConfigError("binance keys not configured for this user")
    return BinanceKeys(
        api_key=decrypt_for_user(
            user.binance_api_key_encrypted,
            passphrase=passphrase,
            user_id=user.id,
        ),
        api_secret=decrypt_for_user(
            user.binance_api_secret_encrypted,
            passphrase=passphrase,
            user_id=user.id,
        ),
    )


async def set_telegram(
    session: AsyncSession,
    *,
    user: User,
    bot_token: str,
    chat_id: str,
    passphrase: str,
) -> None:
    """Encrypt + persist telegram bot_token; chat_id stored plain (not a secret)."""
    user.telegram_bot_token_encrypted = encrypt_for_user(
        bot_token, passphrase=passphrase, user_id=user.id,
    )
    user.telegram_chat_id = chat_id
    await session.flush()


def get_telegram(user: User, *, passphrase: str) -> TelegramConfig:
    """Decrypt and return TelegramConfig for `user`. Raises if unconfigured."""
    if not user.telegram_bot_token_encrypted or not user.telegram_chat_id:
        raise SecretsConfigError("telegram not configured for this user")
    return TelegramConfig(
        bot_token=decrypt_for_user(
            user.telegram_bot_token_encrypted,
            passphrase=passphrase,
            user_id=user.id,
        ),
        chat_id=user.telegram_chat_id,
    )
