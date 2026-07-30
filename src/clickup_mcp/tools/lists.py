"""Lists — the container Tasks actually live in."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_list
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import (
    optional_id,
    priority_to_int,
    require_confirm,
    require_id,
    to_unix_ms,
)


@tool(phase=1)
async def list_lists(
    folder_id: str | None = None,
    space_id: str | None = None,
    archived: bool = False,
    raw: bool = False,
) -> dict:
    """List the Lists in a Folder, or the folderless Lists in a Space.

    Pass exactly one of folder_id or space_id. A Space can hold Lists both ways, so
    to see everything in a Space, call this with space_id and then once per Folder
    from `list_folders`.

    Args:
        folder_id: List the Lists inside this Folder.
        space_id: List the Lists sitting directly in this Space (no Folder).
        archived: Include archived Lists.
        raw: Return ClickUp's full response.
    """
    if bool(folder_id) == bool(space_id):
        raise ValueError("Pass exactly one of folder_id or space_id.")

    client = await current_client()
    if folder_id:
        path = f"/v2/folder/{require_id(folder_id, 'folder_id')}/list"
    else:
        path = f"/v2/space/{require_id(space_id, 'space_id')}/list"
    payload = await client.get(path, params={"archived": archived})
    return payload if raw else collection(payload, "lists", summarize_list)


@tool(phase=1)
async def get_list(list_id: str, raw: bool = False) -> dict:
    """Get one List, including its task count and configured statuses.

    Args:
        list_id: ClickUp List id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/list/{require_id(list_id, 'list_id')}")
    return payload if raw else summarize_list(payload)


@tool(phase=1)
async def create_list(
    name: str,
    folder_id: str | None = None,
    space_id: str | None = None,
    content: str | None = None,
    due_date: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    status: str | None = None,
) -> dict:
    """Create a List, either inside a Folder or directly in a Space.

    Pass exactly one of folder_id or space_id.

    Args:
        name: List name.
        folder_id: Create the List inside this Folder.
        space_id: Create the List directly in this Space (folderless).
        content: Description for the List.
        due_date: ISO-8601 date/datetime, or Unix ms.
        priority: urgent, high, normal, or low.
        assignee_id: ClickUp user id to own the List.
        status: Initial status for the List.
    """
    if bool(folder_id) == bool(space_id):
        raise ValueError("Pass exactly one of folder_id or space_id.")

    client = await current_client()
    body = clean(
        {
            "name": name,
            "content": content,
            "due_date": to_unix_ms(due_date, "due_date"),
            "priority": priority_to_int(priority),
            "assignee": optional_id(assignee_id, "assignee_id"),
            "status": status,
        }
    )
    if folder_id:
        path = f"/v2/folder/{require_id(folder_id, 'folder_id')}/list"
    else:
        path = f"/v2/space/{require_id(space_id, 'space_id')}/list"
    return await client.post(path, body)


@tool(phase=1)
async def create_list_from_template(
    name: str,
    template_id: str,
    folder_id: str | None = None,
    space_id: str | None = None,
    options: dict | None = None,
) -> dict:
    """Create a List from a List template, in a Folder or directly in a Space.

    Call `list_list_templates` for available template ids.

    Args:
        name: Name for the new List.
        template_id: List template id.
        folder_id: Create inside this Folder.
        space_id: Create directly in this Space (folderless).
        options: ClickUp template options. Omit for ClickUp's defaults.
    """
    if bool(folder_id) == bool(space_id):
        raise ValueError("Pass exactly one of folder_id or space_id.")

    client = await current_client()
    body: dict = {"name": name}
    if options:
        body["options"] = options
    template = require_id(template_id, "template_id")
    if folder_id:
        path = f"/v2/folder/{require_id(folder_id, 'folder_id')}/list_template/{template}"
    else:
        path = f"/v2/space/{require_id(space_id, 'space_id')}/list_template/{template}"
    return await client.post(path, body)


@tool(phase=1)
async def update_list(
    list_id: str,
    name: str | None = None,
    content: str | None = None,
    due_date: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    status: str | None = None,
    unset_status: bool | None = None,
    archived: bool | None = None,
) -> dict:
    """Update a List. Only the fields you pass are changed.

    Args:
        list_id: ClickUp List id.
        name: New name.
        content: New description.
        due_date: ISO-8601 date/datetime, or Unix ms.
        priority: urgent, high, normal, or low.
        assignee_id: ClickUp user id to own the List.
        status: New status for the List itself.
        unset_status: Clear the List's status.
        archived: Archive or unarchive.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "content": content,
            "due_date": to_unix_ms(due_date, "due_date"),
            "priority": priority_to_int(priority),
            "assignee": optional_id(assignee_id, "assignee_id"),
            "status": status,
            "unset_status": unset_status,
            "archived": archived,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/list/{require_id(list_id, 'list_id')}", body)


@tool(phase=1, destructive=True)
async def delete_list(list_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a List and every Task in it.

    There is no undo through the API. Say how many Tasks will be destroyed and get
    the user's explicit agreement before calling with confirm=True. If they only
    want it out of the way, `update_list(archived=True)` is reversible.

    Args:
        list_id: ClickUp List id.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this List")
    client = await current_client()
    await client.delete(f"/v2/list/{require_id(list_id, 'list_id')}")
    return {"deleted": True, "list_id": list_id}


@tool(phase=1)
async def add_task_to_list(task_id: str, list_id: str, workspace_id: str | None = None) -> dict:
    """Add an existing Task to an additional List (Tasks in Multiple Lists).

    The Task keeps its home List. This requires the Tasks in Multiple Lists
    ClickUp App to be enabled on the Workspace.

    Args:
        task_id: ClickUp Task id.
        list_id: List to add the Task to.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, _team_id = await client_and_workspace(workspace_id)
    await client.post(
        f"/v2/list/{require_id(list_id, 'list_id')}/task/{require_id(task_id, 'task_id')}"
    )
    return {"added": True, "task_id": task_id, "list_id": list_id}


@tool(phase=1)
async def remove_task_from_list(task_id: str, list_id: str) -> dict:
    """Remove a Task from an additional List.

    This only detaches it from that extra List — it does not delete the Task and
    cannot remove it from its home List.

    Args:
        task_id: ClickUp Task id.
        list_id: List to remove the Task from.
    """
    client = await current_client()
    await client.delete(
        f"/v2/list/{require_id(list_id, 'list_id')}/task/{require_id(task_id, 'task_id')}"
    )
    return {"removed": True, "task_id": task_id, "list_id": list_id}
