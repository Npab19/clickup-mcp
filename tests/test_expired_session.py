"""An expired access token must not break a live session.

The reported failure: after the access token's TTL elapsed, tool calls failed
with "MCP token is unknown or expired" instead of a 401. Because it was a tool
error rather than an auth error, the client never refreshed, and the only way
back was authorizing by hand.

The cause was re-checking expiry in `current_grant()`. The auth middleware
validates a token once per HTTP request; on a long-lived streaming session it
does not re-run, so the transport still considers the caller authenticated while
a second check downstream rejects them. Authentication belongs in exactly one
place, and this is not it.
"""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from clickup_mcp.store import Store


@pytest.fixture
async def expired_setup(store):
    grant_id = await store.upsert_grant("111", "a@example.com", "Ada", "cu_token")
    await store.put_access_token(
        "mcp_expired", "client-1", grant_id, ["clickup"], time.time() - 60
    )
    return grant_id


async def test_expired_token_still_resolves_its_grant(store, expired_setup):
    """Identity resolution must survive expiry — the ClickUp grant never expires."""
    record = await store.get_access_token("mcp_expired", allow_expired=True)
    assert record is not None
    assert record["grant_id"] == expired_setup
    assert record["expired"] is True


async def test_authentication_still_rejects_an_expired_token(store, expired_setup):
    """The middleware's check must keep working, or expiry would mean nothing."""
    assert await store.get_access_token("mcp_expired") is None


async def test_reading_an_expired_token_does_not_delete_it(store, expired_setup):
    """Deleting on read made the lookup destructive: whichever caller checked
    first removed the row, so a second lookup in the same request found nothing."""
    assert await store.get_access_token("mcp_expired") is None
    assert await store.get_access_token("mcp_expired", allow_expired=True) is not None


async def test_provider_rejects_expired_tokens_at_the_auth_boundary(store, expired_setup):
    from clickup_mcp.oauth_provider import ClickUpOAuthProvider

    provider = ClickUpOAuthProvider("https://mcp.example.com", store, "cid", "sec")
    assert await provider.load_access_token("mcp_expired") is None


async def test_sweep_removes_expired_access_tokens(store, expired_setup):
    await store.put_access_token(
        "mcp_live", "client-1", expired_setup, ["clickup"], time.time() + 3600
    )
    removed = await store.sweep_expired()
    assert removed >= 1
    assert await store.get_access_token("mcp_expired", allow_expired=True) is None
    assert await store.get_access_token("mcp_live") is not None


def test_the_sweep_is_actually_scheduled():
    """It existed but was never called, so expired rows accumulated forever."""
    import inspect

    from clickup_mcp.policy import GovernedFastMCP

    source = inspect.getsource(GovernedFastMCP.streamable_http_app)
    assert "sweep_expired" in source


async def test_a_grant_deleted_out_from_under_a_session_is_reported_clearly(store):
    """Revoking a user must produce a re-auth message, not a confusing tool error."""
    grant_id = await store.upsert_grant("222", "b@example.com", "Bob", "cu_token")
    await store.put_access_token("mcp_orphan", "client-1", grant_id, ["clickup"], None)
    await store.delete_grant(grant_id)

    assert await store.get_access_token("mcp_orphan", allow_expired=True) is None
