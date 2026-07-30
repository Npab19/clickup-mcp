"""THE GATE.

No tool may be written until these pass. Everything downstream of the auth core is
mechanical; this is the part that can be architecturally wrong, and the failure
mode is silent — one user quietly reading another user's data.

Covers the three ways isolation can break:
  1. the response cache serving user A's data to user B,
  2. the wrong token going out on the wire,
  3. an MCP token refresh losing its ClickUp binding.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from clickup_mcp.client import ClickUpAuthError, ClickUpClient, ClickUpError
from clickup_mcp.constants import CLICKUP_API_BASE


@pytest.fixture
async def two_users(store):
    """Two ClickUp users authorized against the same server."""
    ada = await store.upsert_grant("111", "ada@example.com", "Ada", "token-ADA", [{"id": "9001"}])
    bob = await store.upsert_grant("222", "bob@example.com", "Bob", "token-BOB", [{"id": "9002"}])
    return await store.get_grant(ada), await store.get_grant(bob)


@pytest.fixture
async def api():
    client = ClickUpClient()
    try:
        yield client
    finally:
        await client.close()


@respx.mock
async def test_cache_never_crosses_users(api, two_users):
    """The regression this whole design exists to prevent.

    Whoop caches on `(path, params)`. Copied as-is, the second user's identical
    request hits the first user's cached entry. Here the key is namespaced by
    grant, so each user must get their own data and both must reach the network.
    """
    ada, bob = two_users

    def responder(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"]
        owner = "ada" if token == "Bearer token-ADA" else "bob"
        return httpx.Response(200, json={"tasks": [{"id": f"{owner}-task"}]})

    route = respx.get(f"{CLICKUP_API_BASE}/v2/list/1/task").mock(side_effect=responder)

    ada_first = await api.for_grant(ada).get("/v2/list/1/task")
    bob_first = await api.for_grant(bob).get("/v2/list/1/task")

    assert ada_first == {"tasks": [{"id": "ada-task"}]}
    assert bob_first == {"tasks": [{"id": "bob-task"}]}
    assert route.call_count == 2, "identical paths for different users must not share a cache entry"

    # Each user still gets their own cache hit on a repeat.
    assert await api.for_grant(ada).get("/v2/list/1/task") == ada_first
    assert await api.for_grant(bob).get("/v2/list/1/task") == bob_first
    assert route.call_count == 2


@respx.mock
async def test_each_user_sends_their_own_token(api, two_users):
    ada, bob = two_users
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"user": {}})

    respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(side_effect=responder)

    await api.for_grant(ada).get("/v2/user")
    await api.for_grant(bob).get("/v2/user")

    assert seen == ["Bearer token-ADA", "Bearer token-BOB"]


@respx.mock
async def test_a_write_clears_only_the_writing_users_cache(api, two_users):
    ada, bob = two_users
    respx.get(f"{CLICKUP_API_BASE}/v2/list/1/task").mock(
        return_value=httpx.Response(200, json={"tasks": []})
    )
    respx.post(f"{CLICKUP_API_BASE}/v2/list/1/task").mock(
        return_value=httpx.Response(200, json={"id": "new"})
    )

    await api.for_grant(ada).get("/v2/list/1/task")
    await api.for_grant(bob).get("/v2/list/1/task")
    assert respx.routes[0].call_count == 2

    await api.for_grant(ada).post("/v2/list/1/task", {"name": "x"})

    # Ada's read must go back to the network; Bob's must still be cached.
    await api.for_grant(ada).get("/v2/list/1/task")
    assert respx.routes[0].call_count == 3
    await api.for_grant(bob).get("/v2/list/1/task")
    assert respx.routes[0].call_count == 3


@respx.mock
async def test_forgetting_a_grant_drops_its_cache(api, two_users):
    ada, _ = two_users
    respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(
        return_value=httpx.Response(200, json={"user": {"id": 1}})
    )

    await api.for_grant(ada).get("/v2/user")
    api.forget_grant(ada.id)
    await api.for_grant(ada).get("/v2/user")

    assert respx.routes[0].call_count == 2


@respx.mock
async def test_revoked_token_asks_for_reauth_instead_of_retrying(api, two_users):
    """ClickUp issues no refresh token, so a dead token can only be replaced by the
    user re-authorizing. Retrying would just burn rate limit."""
    ada, _ = two_users
    route = respx.get(f"{CLICKUP_API_BASE}/v2/user").mock(
        return_value=httpx.Response(401, json={"err": "Token invalid", "ECODE": "OAUTH_025"})
    )

    with pytest.raises(ClickUpAuthError, match="reconnect"):
        await api.for_grant(ada).get("/v2/user")

    assert route.call_count == 1, "a 401 must not be retried"


@respx.mock
async def test_permission_denied_is_not_reported_as_a_dead_token(api, two_users):
    """ClickUp returns 401 both for a revoked token and for an object the user may
    not touch. Only the former should tell the user to reconnect."""
    ada, _ = two_users
    respx.get(f"{CLICKUP_API_BASE}/v2/task/abc").mock(
        return_value=httpx.Response(401, json={"err": "Team not authorized", "ECODE": "ACCESS_083"})
    )

    with pytest.raises(ClickUpError) as excinfo:
        await api.for_grant(ada).get("/v2/task/abc")

    assert not isinstance(excinfo.value, ClickUpAuthError)
    assert "Team not authorized" in str(excinfo.value)


async def test_default_team_id_only_when_unambiguous(api, store):
    one = await store.get_grant(
        await store.upsert_grant("1", None, None, "t", [{"id": "9001", "name": "Solo"}])
    )
    many = await store.get_grant(
        await store.upsert_grant("2", None, None, "t", [{"id": "1"}, {"id": "2"}])
    )

    assert api.for_grant(one).default_team_id() == "9001"
    assert api.for_grant(many).default_team_id() is None
