"""Workspace-level metadata: plan, seats, roles, and custom task types."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.tools._common import client_and_workspace


@tool(phase=1)
async def get_workspace_plan(workspace_id: str | None = None) -> dict:
    """Get the Workspace's ClickUp plan.

    Worth checking when rate limits bite: the API allows 100 requests/minute per
    user on Free, Unlimited and Business, 1,000 on Business Plus, 10,000 on
    Enterprise.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/plan")


@tool(phase=1)
async def get_workspace_seats(workspace_id: str | None = None) -> dict:
    """Get used and available member/guest seats for the Workspace.

    Check this before inviting people — an invite that exceeds the seat count
    changes billing.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/seats")


@tool(phase=1)
async def list_custom_roles(workspace_id: str | None = None, raw: bool = False) -> dict:
    """List the Workspace's custom roles.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        raw: Return ClickUp's full response including per-role permissions.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(f"/v2/team/{team_id}/customroles")
    if raw:
        return payload
    roles = payload.get("custom_roles") or []
    return {
        "custom_roles": [{"id": r.get("id"), "name": r.get("name")} for r in roles],
        "count": len(roles),
    }


@tool(phase=1)
async def list_custom_task_types(workspace_id: str | None = None) -> dict:
    """List custom task types ("custom items") available in the Workspace.

    Pass the returned id as `custom_item_id` when creating a task that should be a
    Milestone, Bug, or any other custom type. The default task type is id 0.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/custom_item")


@tool(phase=1)
async def get_shared_hierarchy(workspace_id: str | None = None) -> dict:
    """List Tasks, Lists and Folders shared with this user but not owned by them.

    These do not appear when walking the Workspace's own Spaces, so check here when
    a user insists something exists that the hierarchy tools cannot find.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/shared")
