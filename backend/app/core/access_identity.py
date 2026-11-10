"""Who is this? Cloudflare Access identity for office actions only Stef may do.

Cloudflare Access puts a signed JWT on every request it lets through, in the
``Cf-Access-Jwt-Assertion`` header (and the ``CF_Authorization`` cookie for
browsers). A human login carries an ``email`` claim; a service token (the
bots) carries ``common_name`` and no email. So "answered by Stef" means: the
JWT verifies against the team's public keys, isn't expired, matches the app's
AUD (if configured), has an email, and that email is on ANSWER_EMAILS.

Plain headers like ``Cf-Access-Authenticated-User-Email`` are not trusted on
their own because a bot could set them.
"""

import asyncio
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request

from app.config import get_settings

LOCAL_IDENTITY = "local"


@lru_cache(maxsize=4)
def _jwks_client(team_domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{team_domain.rstrip('/')}/cdn-cgi/access/certs", cache_keys=True)


def _verify(token: str, team_domain: str, aud: str) -> dict[str, Any]:
    key = _jwks_client(team_domain).get_signing_key_from_jwt(token)
    claims: dict[str, Any] = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=aud or None,
        issuer=team_domain.rstrip("/"),
        options={"verify_aud": bool(aud)},
    )
    return claims


async def require_human(request: Request) -> str:
    """Return Stef's Access email, or raise 401/403. "local" when not behind Access."""
    settings = get_settings()
    team = settings.ACCESS_TEAM_DOMAIN.strip()
    if not team:
        return LOCAL_IDENTITY
    token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get(
        "CF_Authorization"
    )
    if not token:
        raise HTTPException(status_code=401, detail="No Cloudflare Access login")
    try:
        claims = await asyncio.to_thread(_verify, token, team, settings.ACCESS_AUD.strip())
    except Exception as exc:  # bad signature, expired, wrong aud/issuer, keys unreachable
        raise HTTPException(status_code=401, detail="Invalid Cloudflare Access login") from exc
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=403, detail="Only Stef can do this, not a service token")
    allowed = {e.strip().lower() for e in settings.ANSWER_EMAILS.split(",") if e.strip()}
    if allowed and email not in allowed:
        raise HTTPException(status_code=403, detail="Not allowed to answer")
    return email
