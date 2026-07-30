"""Task operations that exist only in API v3: moving a Task between Lists, and
per-user time estimates."""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, require_id


@tool(phase=1)
async def move_task_to_list(
    task_id: str,
    list_id: str,
    workspace_id: str | None = None,
    move_custom_fields: bool = True,
    custom_field_ids_to_move: list[str] | str | None = None,
    status_mappings: list[dict] | None = None,
) -> dict:
    """Move a Task to a different List, changing its home List.

    This is not the same as `add_task_to_list`, which only adds a secondary List
    and leaves the home List alone.

    If the destination List does not have the Task's current status, pass
    status_mappings or the move can fail — check `get_list` on both Lists first.

    Args:
        task_id: ClickUp Task id.
        list_id: Destination List id, which becomes the Task's home List.
        workspace_id: Omit if the user has only one Workspace.
        move_custom_fields: Bring the Task's custom fields to the new List.
        custom_field_ids_to_move: Move only these custom field ids. Omit for all.
        status_mappings: Map old statuses to new ones, e.g.
            [{"current_status": "in progress", "new_status": "active"}].
    """
    client, team_id = await client_and_workspace(workspace_id)
    body: dict[str, Any] = clean(
        {
            "move_custom_fields": move_custom_fields,
            "custom_fields_to_move": as_list(custom_field_ids_to_move),
            "status_mappings": status_mappings,
        }
    )
    return await client.put(
        f"/v3/workspaces/{team_id}/tasks/{require_id(task_id, 'task_id')}"
        f"/home_list/{require_id(list_id, 'list_id')}",
        body,
    )


@tool(phase=2)
async def update_time_estimates_by_user(
    task_id: str,
    estimates: list[dict],
    workspace_id: str | None = None,
    replace_all: bool = False,
) -> dict:
    """Set per-assignee time estimates on a Task.

    Distinct from `update_task(time_estimate_minutes=...)`, which sets one estimate
    for the whole Task. Use this when different people have separate estimates.

    Args:
        task_id: ClickUp Task id.
        estimates: One entry per user, e.g.
            [{"user_id": 123, "time_estimate": 3600000}] with the estimate in
            milliseconds.
        workspace_id: Omit if the user has only one Workspace.
        replace_all: True replaces every existing per-user estimate; False (the
            default) merges these into what is already there.
    """
    if not estimates:
        raise ValueError("estimates must contain at least one entry.")
    client, team_id = await client_and_workspace(workspace_id)
    path = (
        f"/v3/workspaces/{team_id}/tasks/{require_id(task_id, 'task_id')}"
        "/time_estimates_by_user"
    )
    body = {"time_estimates": estimates}
    if replace_all:
        return await client.put(path, body)
    return await client.patch(path, body)
