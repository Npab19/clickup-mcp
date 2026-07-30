"""Spaces — the top level of the Workspace hierarchy."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_space
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import require_confirm, require_id


@tool(phase=1)
async def list_spaces(
    workspace_id: str | None = None, archived: bool = False, raw: bool = False
) -> dict:
    """List the Spaces in a Workspace.

    Only needed when the user asks about structure. To find tasks, use
    `search_tasks` instead of walking Spaces → Folders → Lists.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        archived: Include archived Spaces.
        raw: Return ClickUp's full response, including every status definition and
            feature flag per Space.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(f"/v2/team/{team_id}/space", params={"archived": archived})
    return payload if raw else collection(payload, "spaces", summarize_space)


@tool(phase=1)
async def get_space(space_id: str, raw: bool = False) -> dict:
    """Get one Space, including its configured statuses.

    Args:
        space_id: ClickUp Space id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/space/{require_id(space_id, 'space_id')}")
    return payload if raw else summarize_space(payload)


@tool(phase=1)
async def create_space(
    name: str,
    workspace_id: str | None = None,
    multiple_assignees: bool = True,
) -> dict:
    """Create a Space in a Workspace.

    Args:
        name: Space name.
        workspace_id: Omit if the user has only one Workspace.
        multiple_assignees: Allow more than one assignee per task.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.post(
        f"/v2/team/{team_id}/space",
        {"name": name, "multiple_assignees": multiple_assignees},
    )


@tool(phase=1)
async def update_space(
    space_id: str,
    name: str | None = None,
    private: bool | None = None,
    archived: bool | None = None,
    multiple_assignees: bool | None = None,
) -> dict:
    """Update a Space. Only the fields you pass are changed.

    Args:
        space_id: ClickUp Space id.
        name: New name.
        private: Make the Space private or public.
        archived: Archive or unarchive.
        multiple_assignees: Allow more than one assignee per task.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "private": private,
            "archived": archived,
            "multiple_assignees": multiple_assignees,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/space/{require_id(space_id, 'space_id')}", body)


@tool(phase=1, destructive=True)
async def delete_space(space_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a Space and everything inside it.

    This cascades: every Folder, List and Task in the Space is destroyed. There is
    no undo through the API. Name the Space and its contents to the user and get
    explicit agreement before calling with confirm=True.

    Args:
        space_id: ClickUp Space id.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this Space")
    client = await current_client()
    await client.delete(f"/v2/space/{require_id(space_id, 'space_id')}")
    return {"deleted": True, "space_id": space_id}
