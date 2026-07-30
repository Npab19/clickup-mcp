"""The full OAuth dance, with ClickUp mocked.

The binding asserted here — MCP token → ClickUp grant, preserved across a refresh —
is the piece Whoop's provider does not have. Without it a refresh silently
de-authorizes the user, which looks like a random "not connected" much later.
"""
from __future__ import annotations

import urllib.parse

import httpx
import pytest
import respx
from pydantic import AnyHttpUrl

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from clickup_mcp.constants import CLICKUP_API_BASE, CLICKUP_AUTHORIZE_URL, CLICKUP_TOKEN_URL
from clickup_mcp.oauth_provider import ClickUpOAuthProvider

REDIRECT = "https://client.example.com/callback"


@pytest.fixture
async def provider(store):
    return ClickUpOAuthProvider(
        server_url="https://mcp.example.com",
        store=store,
        client_id="cid",
        client_secret="csecret",
    )


@pytest.fixture
async def client(provider):
    info = OAuthClientInformationFull(redirect_uris=[AnyHttpUrl(REDIRECT)])
    await provider.register_client(info)
    return info


def _params() -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=["clickup"],
        code_challenge="challenge-value",
        redirect_uri=AnyHttpUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )


def _mock_clickup(user_id: str = "777", email: str = "ada@example.com") -> None:
    respx.post(CLICKUP_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": f"cu_{user_id}"})
    )
    respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(
        return_value=httpx.Response(
            200, json={"user": {"id": int(user_id), "email": email, "username": "Ada"}}
        )
    )
    respx.get(f"{CLICKUP_API_BASE}/v2/team").mock(
        return_value=httpx.Response(200, json={"teams": [{"id": 9001, "name": "Acme"}]})
    )


async def test_authorize_redirects_to_clickup_without_pkce_or_scopes(provider, client):
    url = await provider.authorize(client, _params())

    assert url.startswith(CLICKUP_AUTHORIZE_URL)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["https://mcp.example.com/clickup-callback"]
    assert "state" in query
    # ClickUp supports neither, and sending them is at best ignored.
    assert "code_challenge" not in query
    assert "scope" not in query


@respx.mock
async def test_full_flow_binds_mcp_token_to_the_clickup_grant(provider, client, store):
    _mock_clickup()
    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

    redirect = await provider.handle_clickup_callback("clickup-code", state)
    mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]

    auth_code = await provider.load_authorization_code(client, mcp_code)
    assert auth_code is not None
    token = await provider.exchange_authorization_code(client, auth_code)

    record = await store.get_access_token(token.access_token)
    assert record is not None
    grant = await store.get_grant(record["grant_id"])
    assert grant is not None
    assert grant.clickup_user_id == "777"
    assert grant.access_token == "cu_777"
    assert grant.workspaces == [{"id": "9001", "name": "Acme"}]


@respx.mock
async def test_refresh_preserves_the_clickup_binding(provider, client, store):
    """The bug this guards against: minting a fresh MCP token pair without carrying
    grant_id, so the user's next call finds no ClickUp token behind their session."""
    _mock_clickup()
    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    redirect = await provider.handle_clickup_callback("clickup-code", state)
    mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, mcp_code)
    first = await provider.exchange_authorization_code(client, auth_code)

    original = await store.get_access_token(first.access_token)
    assert original is not None

    refresh = await provider.load_refresh_token(client, first.refresh_token)
    assert refresh is not None
    second = await provider.exchange_refresh_token(client, refresh, [])

    refreshed = await store.get_access_token(second.access_token)
    assert refreshed is not None
    assert refreshed["grant_id"] == original["grant_id"]
    assert refreshed["scopes"] == ["clickup"]

    # The consumed refresh token must not be reusable.
    assert await provider.load_refresh_token(client, first.refresh_token) is None


@respx.mock
async def test_authorization_code_is_single_use(provider, client):
    _mock_clickup()
    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    redirect = await provider.handle_clickup_callback("clickup-code", state)
    mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]

    auth_code = await provider.load_authorization_code(client, mcp_code)
    await provider.exchange_authorization_code(client, auth_code)

    assert await provider.load_authorization_code(client, mcp_code) is None


@respx.mock
async def test_two_users_get_independent_grants(provider, client, store):
    for user_id, email in (("777", "ada@example.com"), ("888", "bob@example.com")):
        respx.clear()
        _mock_clickup(user_id, email)
        url = await provider.authorize(client, _params())
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        redirect = await provider.handle_clickup_callback("code", state)
        mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]
        auth_code = await provider.load_authorization_code(client, mcp_code)
        token = await provider.exchange_authorization_code(client, auth_code)

        record = await store.get_access_token(token.access_token)
        grant = await store.get_grant(record["grant_id"])
        assert grant.clickup_user_id == user_id
        assert grant.access_token == f"cu_{user_id}"

    assert await store.count_grants() == 2


@respx.mock
async def test_callback_with_unknown_state_is_rejected(provider):
    _mock_clickup()
    with pytest.raises(ValueError, match="Invalid or expired state"):
        await provider.handle_clickup_callback("code", "never-issued")


@respx.mock
async def test_failed_identity_lookup_aborts_the_grant(provider, client, store):
    """An unidentifiable grant could not be deduplicated or audited, so the flow
    must fail rather than store an anonymous token."""
    respx.post(CLICKUP_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "cu_x"})
    )
    respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(return_value=httpx.Response(401, json={}))

    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

    with pytest.raises(ValueError, match="user profile"):
        await provider.handle_clickup_callback("code", state)
    assert await store.count_grants() == 0


@respx.mock
async def test_workspace_prefetch_failure_is_not_fatal(provider, client, store):
    respx.post(CLICKUP_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "cu_777"})
    )
    respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(
        return_value=httpx.Response(200, json={"user": {"id": 777, "email": "a@b.c"}})
    )
    respx.get(f"{CLICKUP_API_BASE}/v2/team").mock(return_value=httpx.Response(500, json={}))

    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    await provider.handle_clickup_callback("code", state)

    assert await store.count_grants() == 1
