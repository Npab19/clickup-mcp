"""User Groups (ClickUp calls these "Teams" in the UI — not to be confused with
Workspaces, which the API also calls teams). Admin-gated."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, require_confirm, require_id


@tool(phase=4, admin=True)
async def list_user_groups(
    workspace_id: str | None = None, group_ids: list[str] | str | None = None
) -> dict:
    """List User Groups in the Workspace.

    These are the groups ClickUp's UI labels "Teams". A Workspace is a different
    thing, even though the API calls that a team too.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        group_ids: Only these group ids.
    """
    client, team_id = await client_and_workspace(workspace_id)
    params = clean({"team_id": team_id, "group_ids": as_list(group_ids)})
    return await client.get("/v2/group", params=params)


@tool(phase=4, admin=True)
async def create_user_group(
    name: str, member_ids: list[str] | str, workspace_id: str | None = None
) -> dict:
    """Create a User Group.

    Args:
        name: Group name.
        member_ids: ClickUp user ids to put in the group.
        workspace_id: Omit if the user has only one Workspace.
    """
    members = as_list(member_ids)
    if not members:
        raise ValueError("member_ids must contain at least one user id.")
    client, team_id = await client_and_workspace(workspace_id)
    return await client.post(
        f"/v2/team/{team_id}/group", {"name": name, "members": members}
    )


@tool(phase=4, admin=True)
async def update_user_group(
    group_id: str,
    name: str | None = None,
    add_member_ids: list[str] | str | None = None,
    remove_member_ids: list[str] | str | None = None,
    handle: str | None = None,
) -> dict:
    """Rename a User Group or change its membership. Members edit additively.

    Args:
        group_id: ClickUp User Group id.
        name: New group name.
        add_member_ids: ClickUp user ids to add.
        remove_member_ids: ClickUp user ids to remove.
        handle: New @-mention handle for the group.
    """
    client = await current_client()
    members = clean({"add": as_list(add_member_ids), "rem": as_list(remove_member_ids)})
    body = clean({"name": name, "handle": handle})
    if members:
        body["members"] = members
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/group/{require_id(group_id, 'group_id')}", body)


@tool(phase=4, admin=True, destructive=True)
async def delete_user_group(group_id: str, confirm: bool = False) -> dict:
    """Delete a User Group.

    The people in it keep their Workspace access; only the grouping is destroyed,
    along with any group assignments that referenced it.

    Args:
        group_id: ClickUp User Group id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this User Group")
    client = await current_client()
    await client.delete(f"/v2/group/{require_id(group_id, 'group_id')}")
    return {"deleted": True, "group_id": group_id}
