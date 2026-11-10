"""Real JWT checks for app.core.access_identity (Cloudflare Access logins)."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

import app.core.access_identity as ident
from app.config import get_settings

TEAM = "https://team.example"
AUD = "aud-tag-123"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    good = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(get_settings(), "ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(get_settings(), "ACCESS_AUD", AUD)
    monkeypatch.setattr(get_settings(), "ANSWER_EMAILS", "stef@example.com")
    client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=good.public_key())
    )
    monkeypatch.setattr(ident, "_jwks_client", lambda _team: client)
    return good


def _req(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"cf-access-jwt-assertion", token.encode())]})


def _token(key, **claims) -> str:  # type: ignore[no-untyped-def]
    base = {"iss": TEAM, "aud": [AUD], "exp": int(time.time()) + 300}
    return jwt.encode({**base, **claims}, key, algorithm="RS256")


async def test_human_login_accepted(keys) -> None:  # type: ignore[no-untyped-def]
    assert await ident.require_human(_req(_token(keys, email="Stef@Example.com"))) == (
        "stef@example.com"
    )


async def test_service_token_refused(keys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(HTTPException) as e:
        await ident.require_human(_req(_token(keys, common_name="abc.access")))
    assert e.value.status_code == 403


@pytest.mark.parametrize(
    "claims",
    [{"aud": ["other-app"]}, {"iss": "https://evil.example"}, {"exp": 1}],
)
async def test_wrong_app_issuer_or_expired_refused(keys, claims) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(HTTPException) as e:
        await ident.require_human(_req(_token(keys, email="stef@example.com", **claims)))
    assert e.value.status_code == 401


async def test_forged_signature_refused(keys) -> None:  # type: ignore[no-untyped-def]
    forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as e:
        await ident.require_human(_req(_token(forger, email="stef@example.com")))
    assert e.value.status_code == 401
