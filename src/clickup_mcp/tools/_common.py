"""Helpers shared by every tool module."""
from __future__ import annotations

from typing import Any

from clickup_mcp.client import ScopedClickUpClient
from clickup_mcp.context import current_client
from clickup_mcp.validation import require_id


async def client_and_workspace(workspace_id: str | None) -> tuple[ScopedClickUpClient, str]:
    """Resolve the calling user's client plus the Workspace to act on.

    A user with exactly one authorized Workspace should never have to name it, so
    it is filled in from the grant. Anyone with several must be explicit — guessing
    would silently act on the wrong Workspace.
    """
    client = await current_client()
    if workspace_id:
        return client, require_id(workspace_id, "workspace_id")

    default = client.default_team_id()
    if default:
        return client, default

    names = ", ".join(
        f"{w.get('name')} ({w.get('id')})" for w in client.workspaces
    ) or "none found"
    raise ValueError(
        "workspace_id is required because this user has access to more than one "
        f"Workspace. Available: {names}."
    )


def clean(body: dict[str, Any]) -> dict[str, Any]:
    """Drop unset fields so a partial update never blanks an untouched value."""
    return {k: v for k, v in body.items() if v is not None}
