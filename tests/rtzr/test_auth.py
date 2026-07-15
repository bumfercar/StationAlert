from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from nextstop_stt.rtzr.auth import RTZRCredentials, RTZRTokenProvider
from nextstop_stt.rtzr.errors import RTZRAuthenticationError, RTZRCredentialsError


def test_credentials_require_both_environment_variables() -> None:
    with pytest.raises(RTZRCredentialsError, match="RTZR_CLIENT_SECRET"):
        RTZRCredentials.from_env({"RTZR_CLIENT_ID": "client-id"})


def test_credentials_repr_does_not_expose_values() -> None:
    credentials = RTZRCredentials(client_id="private-id", client_secret="private-secret")

    rendered = repr(credentials)

    assert "private-id" not in rendered
    assert "private-secret" not in rendered


def test_token_is_cached_then_refreshed_before_expiry() -> None:
    now = [800.0]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == "/v1/authenticate"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        return httpx.Response(
            200,
            json={"access_token": f"token-{calls}", "expire_at": 1_000 + calls},
        )

    async def scenario() -> None:
        provider = _provider(handler, clock=lambda: now[0])
        assert await provider.get_access_token() == "token-1"
        assert await provider.get_access_token() == "token-1"
        now[0] = 950.0
        assert await provider.get_access_token() == "token-2"

    asyncio.run(scenario())
    assert calls == 2


def test_authentication_error_redacts_credentials() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"code": "H0002", "msg": "invalid private-id and private-secret"},
        )

    async def scenario() -> str:
        provider = _provider(handler)
        with pytest.raises(RTZRAuthenticationError) as captured:
            await provider.get_access_token()
        return str(captured.value)

    message = asyncio.run(scenario())
    assert "H0002" in message
    assert "private-id" not in message
    assert "private-secret" not in message
    assert "[REDACTED]" in message


def test_invalid_success_response_is_understandable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async def scenario() -> None:
        provider = _provider(handler)
        with pytest.raises(RTZRAuthenticationError, match="invalid token response"):
            await provider.get_access_token()

    asyncio.run(scenario())


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: Callable[[], float] = lambda: 0.0,
) -> RTZRTokenProvider:
    credentials = RTZRCredentials(client_id="private-id", client_secret="private-secret")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RTZRTokenProvider(credentials, client=client, clock=clock)
