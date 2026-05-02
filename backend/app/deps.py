import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt as pyjwt
from fastapi import Header, HTTPException, status

from app.config import get_settings


class CFAccessError(Exception):
    pass


@dataclass
class CFAccessUser:
    email: str
    sub: str
    raw: dict[str, Any]


_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL = 3600.0


def _fetch_jwks(team_domain: str) -> dict[str, Any]:
    now = time.time()
    cached = _JWKS_CACHE.get(team_domain)
    if cached and (now - cached[0]) < _JWKS_TTL:
        return cached[1]
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    jwks = r.json()
    _JWKS_CACHE[team_domain] = (now, jwks)
    return jwks


def _default_key_resolver(team_domain: str) -> Callable[[str], str]:
    def resolve(kid: str) -> str:
        jwks = _fetch_jwks(team_domain)
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return pyjwt.algorithms.RSAAlgorithm.from_jwk(key)  # type: ignore[return-value]
        raise CFAccessError(f"kid {kid} not found in JWKS")
    return resolve


@dataclass
class CFAccessConfig:
    team_domain: str
    aud: str
    _algorithm: str = field(default="RS256")
    _key_resolver: Callable[[str], Any] | None = field(default=None)

    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    def resolver(self) -> Callable[[str], Any]:
        if self._key_resolver is not None:
            return self._key_resolver
        return _default_key_resolver(self.team_domain)


def verify_cf_access_jwt(token: str, *, cfg: CFAccessConfig) -> CFAccessUser:
    if not token:
        raise CFAccessError("missing token")
    try:
        if cfg._algorithm == "RS256":
            unverified_header = pyjwt.get_unverified_header(token)
        else:
            unverified_header = {"kid": "test"}
        kid = unverified_header.get("kid", "test")
        key = cfg.resolver()(kid)
        payload = pyjwt.decode(
            token, key, algorithms=[cfg._algorithm],
            audience=cfg.aud, issuer=cfg.issuer(),
        )
    except Exception as e:
        raise CFAccessError(str(e)) from e
    return CFAccessUser(
        email=payload.get("email", ""),
        sub=payload.get("sub", ""),
        raw=payload,
    )


# FastAPI dependency
async def require_cf_user(
    cf_access_jwt_assertion: str = Header(default="", alias="Cf-Access-Jwt-Assertion"),
) -> CFAccessUser:
    settings = get_settings()
    if settings.env == "development":
        # Bypass in dev; LAN access is unauthenticated by design (§2.7)
        return CFAccessUser(email="dev@local", sub="dev", raw={})
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        raise HTTPException(status_code=503, detail="auth not configured")
    cfg = CFAccessConfig(
        team_domain=settings.cf_access_team_domain,
        aud=settings.cf_access_aud,
    )
    try:
        return verify_cf_access_jwt(cf_access_jwt_assertion, cfg=cfg)
    except CFAccessError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
