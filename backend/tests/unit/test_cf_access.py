import time
from unittest.mock import patch

import jwt as pyjwt
import pytest

from app.deps import verify_cf_access_jwt, CFAccessConfig, CFAccessError


# Generate an RSA keypair fixture (for HS testing we'd use HS256;
# Cloudflare uses RS256 — we use a fake public key fetcher here)
@pytest.fixture
def signing_key() -> str:
    # Use HS256 in tests for simplicity; production uses RS256 from CF JWKS.
    return "test-secret-do-not-use-in-prod"


def make_jwt(*, aud: str, iss: str, secret: str, exp_offset: int = 3600,
             email: str = "user@example.com") -> str:
    return pyjwt.encode(
        {"aud": aud, "iss": iss, "email": email,
         "iat": int(time.time()), "exp": int(time.time()) + exp_offset},
        secret, algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_valid_jwt_returns_email(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="my-app-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key)
    user = await verify_cf_access_jwt(token, cfg=cfg)
    assert user.email == "user@example.com"


@pytest.mark.asyncio
async def test_wrong_aud_raises(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="OTHER-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key)
    with pytest.raises(CFAccessError):
        await verify_cf_access_jwt(token, cfg=cfg)


@pytest.mark.asyncio
async def test_expired_jwt_raises(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="my-app-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key, exp_offset=-1)
    with pytest.raises(CFAccessError):
        await verify_cf_access_jwt(token, cfg=cfg)


@pytest.mark.asyncio
async def test_no_token_raises() -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud")
    with pytest.raises(CFAccessError):
        await verify_cf_access_jwt("", cfg=cfg)
