"""Tasks. `search_tasks` is the workhorse of this whole server."""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_task
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import (
    array_params,
    as_list,
    optional_id,
    priority_to_int,
    require_confirm,
    require_id,
    to_unix_ms,
    validate_page,
)

_TEAM_ARRAY_PARAMS = (
    "space_ids",
    "project_ids",
    "list_ids",
    "statuses",
    "assignees",
    "tags",
    "custom_items",
)


@tool(phase=1)
async def search_tasks(
    workspace_id: str | None = None,
    list_ids: list[str] | str | None = None,
    space_ids: list[str] | str | None = None,
    folder_ids: list[str] | str | None = None,
    assignees: list[str] | str | None = None,
    statuses: list[str] | str | None = None,
    tags: list[str] | str | None = None,
    include_closed: bool = False,
    subtasks: bool = False,
    due_date_after: str | None = None,
    due_date_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    order_by: str | None = None,
    reverse: bool = False,
    page: int = 0,
    raw: bool = False,
) -> dict:
    """Search Tasks across a whole Workspace with filters. START HERE.

    This is almost always the right way to find Tasks. Walking Spaces → Folders →
    Lists → Tasks costs many calls and returns far more data; this is one call.

    Results are paginated at 100 per page. `last_page: false` means there is more —
    ask the user before fetching further pages rather than looping.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        list_ids: Restrict to these List ids.
        space_ids: Restrict to these Space ids.
        folder_ids: Restrict to these Folder ids.
        assignees: ClickUp user ids (not usernames). Use `list_workspaces(raw=True)`
            or `get_list_members` to map a name to an id.
        statuses: Status names, e.g. ["in progress", "review"].
        tags: Tag names.
        include_closed: Include Tasks in a closed status.
        subtasks: Include subtasks in the results.
        due_date_after: ISO-8601 date/datetime, or Unix ms.
        due_date_before: ISO-8601 date/datetime, or Unix ms.
        updated_after: ISO-8601 date/datetime, or Unix ms.
        updated_before: ISO-8601 date/datetime, or Unix ms.
        order_by: id, created, updated, or due_date.
        reverse: Reverse the sort order.
        page: 0-indexed page number.
        raw: Return ClickUp's full response. Very large — leave False unless you
            need a field the summary omits.
    """
    client, team_id = await client_and_workspace(workspace_id)
    params = array_params(
        {
            "space_ids": space_ids,
            # ClickUp still calls Folders "projects" in this endpoint's parameters.
            "project_ids": folder_ids,
            "list_ids": list_ids,
            "statuses": statuses,
            "assignees": assignees,
            "tags": tags,
            "include_closed": include_closed or None,
            "subtasks": subtasks or None,
            "due_date_gt": to_unix_ms(due_date_after, "due_date_after"),
            "due_date_lt": to_unix_ms(due_date_before, "due_date_before"),
            "date_updated_gt": to_unix_ms(updated_after, "updated_after"),
            "date_updated_lt": to_unix_ms(updated_before, "updated_before"),
            "order_by": order_by,
            "reverse": reverse or None,
            "page": validate_page(page),
        },
        _TEAM_ARRAY_PARAMS,
    )
    payload = await client.get(f"/v2/team/{team_id}/task", params=params)
    return payload if raw else collection(payload, "tasks", summarize_task)


@tool(phase=1)
async def list_tasks_in_list(
    list_id: str,
    assignees: list[str] | str | None = None,
    statuses: list[str] | str | None = None,
    tags: list[str] | str | None = None,
    include_closed: bool = False,
    subtasks: bool = False,
    archived: bool = False,
    order_by: str | None = None,
    reverse: bool = False,
    page: int = 0,
    raw: bool = False,
) -> dict:
    """List the Tasks in one List.

    Use `search_tasks` instead when filtering across Lists, Spaces, or the whole
    Workspace.

    Args:
        list_id: ClickUp List id.
        assignees: ClickUp user ids.
        statuses: Status names.
        tags: Tag names.
        include_closed: Include Tasks in a closed status.
        subtasks: Include subtasks.
        archived: Include archived Tasks.
        order_by: id, created, updated, or due_date.
        reverse: Reverse the sort order.
        page: 0-indexed page number.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    params = array_params(
        {
            "assignees": assignees,
            "statuses": statuses,
            "tags": tags,
            "include_closed": include_closed or None,
            "subtasks": subtasks or None,
            "archived": archived or None,
            "order_by": order_by,
            "reverse": reverse or None,
            "page": validate_page(page),
        },
        # This endpoint takes bare names, without the [] suffix.
        (),
    )
    for key in ("assignees", "statuses", "tags"):
        if key in params:
            params[key] = as_list(params[key])
    payload = await client.get(f"/v2/list/{require_id(list_id, 'list_id')}/task", params=params)
    return payload if raw else collection(payload, "tasks", summarize_task)


@tool(phase=1)
async def get_task(
    task_id: str,
    custom_task_ids: bool = False,
    workspace_id: str | None = None,
    include_subtasks: bool = False,
    include_markdown_description: bool = False,
    raw: bool = False,
) -> dict:
    """Get one Task in full.

    Args:
        task_id: ClickUp Task id, or a custom id like "ABC-123" with
            custom_task_ids=True.
        custom_task_ids: Set True when task_id is a custom id rather than a
            ClickUp id. Requires workspace_id.
        workspace_id: Required when custom_task_ids is True.
        include_subtasks: Include this Task's subtasks.
        include_markdown_description: Return the description as markdown.
        raw: Return ClickUp's full response.
    """
    if custom_task_ids:
        client, team_id = await client_and_workspace(workspace_id)
    else:
        client, team_id = await current_client(), None

    params: dict[str, Any] = {
        "include_subtasks": include_subtasks or None,
        "include_markdown_description": include_markdown_description or None,
    }
    if custom_task_ids:
        params["custom_task_ids"] = True
        params["team_id"] = team_id

    payload = await client.get(f"/v2/task/{require_id(task_id, 'task_id')}", params=params)
    return payload if raw else summarize_task(payload)


@tool(phase=1)
async def create_task(
    list_id: str,
    name: str,
    description: str | None = None,
    markdown_description: str | None = None,
    assignees: list[str] | str | None = None,
    tags: list[str] | str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    time_estimate_minutes: int | None = None,
    parent_task_id: str | None = None,
    custom_item_id: int | None = None,
    notify_all: bool = False,
) -> dict:
    """Create a Task in a List.

    To create a subtask, pass parent_task_id. Custom field values cannot be set
    here — create the Task, then call `set_custom_field_value`.

    Args:
        list_id: ClickUp List id the Task belongs to.
        name: Task name.
        description: Plain-text description.
        markdown_description: Markdown description. Takes precedence over
            description if both are given.
        assignees: ClickUp user ids (not usernames).
        tags: Tag names. The tag must already exist in the Space.
        status: Status name. Must be one the List actually has — check `get_list`.
        priority: urgent, high, normal, or low.
        due_date: ISO-8601 date/datetime, or Unix ms.
        start_date: ISO-8601 date/datetime, or Unix ms.
        time_estimate_minutes: Estimate in minutes.
        parent_task_id: Make this a subtask of that Task.
        custom_item_id: Custom task type id from `list_custom_task_types`.
        notify_all: Notify every assignee and watcher about the creation.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "description": description,
            "markdown_description": markdown_description,
            "assignees": as_list(assignees),
            "tags": as_list(tags),
            "status": status,
            "priority": priority_to_int(priority),
            "due_date": to_unix_ms(due_date, "due_date"),
            "due_date_time": True if due_date and ":" in str(due_date) else None,
            "start_date": to_unix_ms(start_date, "start_date"),
            "start_date_time": True if start_date and ":" in str(start_date) else None,
            "time_estimate": time_estimate_minutes * 60_000
            if time_estimate_minutes is not None
            else None,
            "parent": optional_id(parent_task_id, "parent_task_id"),
            "custom_item_id": custom_item_id,
            "notify_all": notify_all or None,
        }
    )
    payload = await client.post(f"/v2/list/{require_id(list_id, 'list_id')}/task", body)
    return summarize_task(payload)


@tool(phase=1)
async def create_task_from_template(list_id: str, template_id: str, name: str) -> dict:
    """Create a Task in a List from a Task template.

    Call `list_task_templates` for available template ids.

    Args:
        list_id: ClickUp List id.
        template_id: Task template id.
        name: Name for the new Task.
    """
    client = await current_client()
    return await client.post(
        f"/v2/list/{require_id(list_id, 'list_id')}"
        f"/taskTemplate/{require_id(template_id, 'template_id')}",
        {"name": name},
    )


@tool(phase=1)
async def update_task(
    task_id: str,
    name: str | None = None,
    description: str | None = None,
    markdown_description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    time_estimate_minutes: int | None = None,
    add_assignees: list[str] | str | None = None,
    remove_assignees: list[str] | str | None = None,
    parent_task_id: str | None = None,
    archived: bool | None = None,
) -> dict:
    """Update a Task. Only the fields you pass are changed.

    Assignees are edited additively — pass add_assignees / remove_assignees rather
    than a replacement list. Custom fields are not editable here; use
    `set_custom_field_value`.

    Args:
        task_id: ClickUp Task id.
        name: New name.
        description: New plain-text description.
        markdown_description: New markdown description.
        status: New status. Must exist on the Task's List.
        priority: urgent, high, normal, or low.
        due_date: ISO-8601 date/datetime, or Unix ms.
        start_date: ISO-8601 date/datetime, or Unix ms.
        time_estimate_minutes: Estimate in minutes.
        add_assignees: ClickUp user ids to assign.
        remove_assignees: ClickUp user ids to unassign.
        parent_task_id: Re-parent this Task under another Task.
        archived: Archive or unarchive.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "description": description,
            "markdown_description": markdown_description,
            "status": status,
            "priority": priority_to_int(priority),
            "due_date": to_unix_ms(due_date, "due_date"),
            "due_date_time": True if due_date and ":" in str(due_date) else None,
            "start_date": to_unix_ms(start_date, "start_date"),
            "start_date_time": True if start_date and ":" in str(start_date) else None,
            "time_estimate": time_estimate_minutes * 60_000
            if time_estimate_minutes is not None
            else None,
            "parent": optional_id(parent_task_id, "parent_task_id"),
            "archived": archived,
        }
    )

    assignee_patch = clean({"add": as_list(add_assignees), "rem": as_list(remove_assignees)})
    if assignee_patch:
        body["assignees"] = assignee_patch

    if not body:
        raise ValueError("Pass at least one field to change.")

    payload = await client.put(f"/v2/task/{require_id(task_id, 'task_id')}", body)
    return summarize_task(payload)


@tool(phase=1, destructive=True)
async def delete_task(task_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a Task and its subtasks.

    There is no undo through the API. If the user just wants it off the board,
    `update_task(status="closed")` or `update_task(archived=True)` is reversible —
    offer that first.

    Args:
        task_id: ClickUp Task id.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this Task")
    client = await current_client()
    await client.delete(f"/v2/task/{require_id(task_id, 'task_id')}")
    return {"deleted": True, "task_id": task_id}


@tool(phase=1, destructive=True)
async def merge_tasks(
    target_task_id: str, source_task_ids: list[str] | str, confirm: bool = False
) -> dict:
    """Merge one or more source Tasks into a target Task.

    The source Tasks are absorbed into the target and cease to exist as separate
    Tasks. There is no unmerge. Name which Task survives and which are consumed,
    and get the user's agreement, before calling with confirm=True.

    Args:
        target_task_id: The Task that survives and receives the others' content.
        source_task_ids: Task ids to merge into the target. These are consumed.
        confirm: Must be True. Guard against an accidental irreversible merge.
    """
    sources = as_list(source_task_ids)
    if not sources:
        raise ValueError("source_task_ids must contain at least one Task id.")
    require_confirm(confirm, f"merge {len(sources)} Task(s) into {target_task_id}")
    client = await current_client()
    return await client.post(
        f"/v2/task/{require_id(target_task_id, 'target_task_id')}/merge",
        {"source_task_ids": sources},
    )


@tool(phase=1)
async def get_task_time_in_status(task_id: str) -> dict:
    """Get how long a Task has spent in each status.

    Args:
        task_id: ClickUp Task id.
    """
    client = await current_client()
    return await client.get(f"/v2/task/{require_id(task_id, 'task_id')}/time_in_status")


@tool(phase=1)
async def get_bulk_time_in_status(task_ids: list[str] | str) -> dict:
    """Get time-in-status for up to 100 Tasks at once.

    Args:
        task_ids: ClickUp Task ids (2 to 100).
    """
    ids = as_list(task_ids) or []
    if not 2 <= len(ids) <= 100:
        raise ValueError(f"task_ids must contain between 2 and 100 ids, got {len(ids)}.")
    client = await current_client()
    return await client.get(
        "/v2/task/bulk_time_in_status/task_ids", params={"task_ids": ids}
    )
