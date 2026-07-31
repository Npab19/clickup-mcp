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

    # The consumed refresh token stays usable for a short grace window — see
    # REFRESH_TOKEN_GRACE. Deleting it outright meant a lost response or a
    # concurrent refresh forced a full re-authorization.
    assert await provider.load_refresh_token(client, first.refresh_token) is not None
    await _close_grace_window(store, first.refresh_token)
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


# --- scope resolution -------------------------------------------------------
# Regression: a client that omits `scope` from the authorization request reaches
# the provider with scopes=None (validate_scope(None) returns None). Stored
# verbatim that mints an access token with scopes=[], which can never satisfy
# AuthSettings.required_scopes — so the OAuth dance reports success and then every
# /mcp request is rejected 403 insufficient_scope. Observed as: Claude's web client
# worked, the VS Code client showed "needs-auth" right after authenticating.


def _params_without_scope() -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=None,  # the client sent no `scope` parameter
        code_challenge="challenge-value",
        redirect_uri=AnyHttpUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )


@respx.mock
async def test_client_omitting_scope_still_gets_a_usable_token(provider, client, store):
    _mock_clickup()
    url = await provider.authorize(client, _params_without_scope())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    redirect = await provider.handle_clickup_callback("code", state)
    mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, mcp_code)
    token = await provider.exchange_authorization_code(client, auth_code)

    issued = await provider.load_access_token(token.access_token)
    assert issued is not None
    assert "clickup" in issued.scopes, (
        "token was minted with no scopes; every /mcp call would 403 insufficient_scope"
    )


async def test_effective_scopes_prefers_request_then_registration_then_default(provider):
    registered = OAuthClientInformationFull(
        redirect_uris=[AnyHttpUrl(REDIRECT)], scope="clickup extra"
    )
    scopeless = OAuthClientInformationFull(redirect_uris=[AnyHttpUrl(REDIRECT)])

    assert provider._effective_scopes(registered, ["clickup"]) == ["clickup"]
    assert provider._effective_scopes(registered, None) == ["clickup", "extra"]
    assert provider._effective_scopes(registered, []) == ["clickup", "extra"]
    assert provider._effective_scopes(scopeless, None) == ["clickup"]


@respx.mock
async def test_refresh_never_downgrades_to_an_unusable_empty_scope(provider, client, store):
    _mock_clickup()
    url = await provider.authorize(client, _params_without_scope())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    redirect = await provider.handle_clickup_callback("code", state)
    mcp_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]
    first = await provider.exchange_authorization_code(
        client, await provider.load_authorization_code(client, mcp_code)
    )

    refresh = await provider.load_refresh_token(client, first.refresh_token)
    second = await provider.exchange_refresh_token(client, refresh, [])

    issued = await provider.load_access_token(second.access_token)
    assert "clickup" in issued.scopes


# --- refresh durability -----------------------------------------------------
# The ClickUp grant never expires and neither does the refresh token, so once a
# user connects they should never have to authorize again. Strict single-use
# rotation broke that: a lost response, a client retry, or two editor windows
# refreshing at once left the only valid refresh token already deleted, and the
# client fell back to a full re-authorization.


async def _close_grace_window(store, refresh_token: str) -> None:
    """Force a rotated refresh token's grace window shut.

    retire_refresh_token() only sets expires_at when it is still NULL, so it
    cannot be used to shorten a window it already opened.
    """
    from clickup_mcp.store import token_hash

    await store._db.execute(
        "UPDATE refresh_tokens SET expires_at = ? WHERE token_hash = ?",
        (0.0, token_hash(refresh_token)),
    )
    await store._db.commit()


async def _connect(provider, client):
    """Complete one authorization and return the issued token pair."""
    url = await provider.authorize(client, _params())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    redirect = await provider.handle_clickup_callback("code", state)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)["code"][0]
    return await provider.exchange_authorization_code(
        client, await provider.load_authorization_code(client, code)
    )


@respx.mock
async def test_a_lost_refresh_response_does_not_strand_the_client(provider, client):
    """The client refreshes, never sees the reply, and retries with the same token."""
    _mock_clickup()
    first = await _connect(provider, client)

    rt = await provider.load_refresh_token(client, first.refresh_token)
    await provider.exchange_refresh_token(client, rt, [])

    # Same refresh token again — as a retry would.
    replay = await provider.load_refresh_token(client, first.refresh_token)
    assert replay is not None, "retrying a refresh must not force re-authorization"
    second = await provider.exchange_refresh_token(client, replay, [])
    assert second.access_token


@respx.mock
async def test_concurrent_refreshes_both_succeed(provider, client, store):
    """Two editor windows sharing one stored token refresh at the same moment."""
    import asyncio

    _mock_clickup()
    first = await _connect(provider, client)
    grant_id = (await store.get_access_token(first.access_token))["grant_id"]

    async def refresh():
        rt = await provider.load_refresh_token(client, first.refresh_token)
        return await provider.exchange_refresh_token(client, rt, [])

    a, b = await asyncio.gather(refresh(), refresh())
    for token in (a, b):
        record = await store.get_access_token(token.access_token)
        assert record is not None and record["grant_id"] == grant_id


@respx.mock
async def test_retired_refresh_token_stops_working_after_the_grace_window(provider, client, store):
    """The window is a safety net, not an indefinite second key."""
    _mock_clickup()
    first = await _connect(provider, client)
    rt = await provider.load_refresh_token(client, first.refresh_token)
    await provider.exchange_refresh_token(client, rt, [])

    await _close_grace_window(store, first.refresh_token)
    assert await provider.load_refresh_token(client, first.refresh_token) is None


@respx.mock
async def test_legacy_empty_scope_refresh_token_is_healed(provider, client, store):
    """Tokens minted before the scope fix carry []. The SDK rejects a refresh whose
    requested scope is missing from the refresh token, so without healing these the
    user could only recover by re-authorizing."""
    _mock_clickup()
    first = await _connect(provider, client)
    record = await store.get_refresh_token(first.refresh_token)
    await store.put_refresh_token(
        first.refresh_token, record["client_id"], record["grant_id"], []
    )

    loaded = await provider.load_refresh_token(client, first.refresh_token)
    assert loaded.scopes == ["clickup"]
    refreshed = await provider.exchange_refresh_token(client, loaded, ["clickup"])
    assert (await provider.load_access_token(refreshed.access_token)).scopes == ["clickup"]


@respx.mock
async def test_refresh_chain_survives_many_cycles(provider, client, store):
    """A long-lived session refreshes repeatedly; the grant must follow every hop."""
    _mock_clickup()
    token = await _connect(provider, client)
    grant_id = (await store.get_access_token(token.access_token))["grant_id"]

    for _ in range(10):
        rt = await provider.load_refresh_token(client, token.refresh_token)
        assert rt is not None
        token = await provider.exchange_refresh_token(client, rt, [])

    record = await store.get_access_token(token.access_token)
    assert record["grant_id"] == grant_id
    assert record["scopes"] == ["clickup"]
