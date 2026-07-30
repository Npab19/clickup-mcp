"""Per-request caller identity.

This is the hinge the whole multi-user design turns on. `get_access_token()`
returns the MCP access token that authenticated *this* request; the store maps it
to exactly one ClickUp grant. Nothing in the tool layer ever touches a global
token, so two users on the same server cannot see each other's data.
"""
from __future__ import annotations

import logging

from mcp.server.auth.middleware.auth_context import get_access_token

from clickup_mcp.client import ClickUpAuthError, ScopedClickUpClient
from clickup_mcp.runtime import clickup, store
from clickup_mcp.store import ClickUpGrant

logger = logging.getLogger(__name__)

_REAUTH_HINT = (
    "Not connected to ClickUp. Reconnect this MCP server to authorize, then retry."
)


async def current_grant() -> ClickUpGrant:
    """The ClickUp grant behind the current request. Raises if absent."""
    token = get_access_token()
    if token is None:
        raise ClickUpAuthError(
            "This request carried no authenticated MCP token. " + _REAUTH_HINT
        )

    record = await store.get_access_token(token.token)
    if record is None:
        raise ClickUpAuthError("MCP token is unknown or expired. " + _REAUTH_HINT)

    grant = await store.get_grant(record["grant_id"])
    if grant is None:
        # The grant row is gone (revoked, or undecryptable after a key rotation).
        raise ClickUpAuthError(
            "The ClickUp authorization behind this session no longer exists. "
            + _REAUTH_HINT
        )
    return grant


async def current_grant_or_none() -> ClickUpGrant | None:
    """Non-raising variant, for governance paths that must not 500 on anonymity."""
    try:
        return await current_grant()
    except ClickUpAuthError:
        return None


async def current_client() -> ScopedClickUpClient:
    """A ClickUp client bound to the calling user."""
    return clickup.for_grant(await current_grant())
