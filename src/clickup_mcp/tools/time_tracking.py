"""Time tracking — the modern Workspace-scoped API, plus the legacy per-Task one."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_time_entry
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import (
    as_list,
    optional_id,
    require_confirm,
    require_id,
    to_unix_ms,
)


@tool(phase=2)
async def list_time_entries(
    workspace_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    assignee: str | None = None,
    task_id: str | None = None,
    list_id: str | None = None,
    folder_id: str | None = None,
    space_id: str | None = None,
    include_task_tags: bool = False,
    raw: bool = False,
) -> dict:
    """List time entries in a date range.

    Defaults to the last 30 days when no dates are given. Without `assignee` you
    only see your own entries; viewing other people's requires the right
    Workspace permissions.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        start_date: ISO-8601 date/datetime, or Unix ms.
        end_date: ISO-8601 date/datetime, or Unix ms.
        assignee: ClickUp user id, or comma-separated ids for several people.
        task_id: Only entries against this Task.
        list_id: Only entries against Tasks in this List.
        folder_id: Only entries against Tasks in this Folder.
        space_id: Only entries against Tasks in this Space.
        include_task_tags: Include each Task's tags.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    params = clean(
        {
            "start_date": to_unix_ms(start_date, "start_date"),
            "end_date": to_unix_ms(end_date, "end_date"),
            "assignee": assignee,
            "task_id": optional_id(task_id, "task_id"),
            "list_id": optional_id(list_id, "list_id"),
            "folder_id": optional_id(folder_id, "folder_id"),
            "space_id": optional_id(space_id, "space_id"),
            "include_task_tags": include_task_tags or None,
        }
    )
    payload = await client.get(f"/v2/team/{team_id}/time_entries", params=params)
    return payload if raw else collection(payload, "data", summarize_time_entry)


@tool(phase=2)
async def get_running_timer(
    workspace_id: str | None = None, assignee: str | None = None
) -> dict:
    """Get the currently running timer, if any.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        assignee: ClickUp user id. Defaults to the authenticated user.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/time_entries/current",
        params=clean({"assignee": assignee}),
    )
    data = payload.get("data")
    if not data:
        return {"running": False}
    return {"running": True, "entry": summarize_time_entry(data)}


@tool(phase=2)
async def start_timer(
    task_id: str,
    workspace_id: str | None = None,
    description: str | None = None,
    billable: bool | None = None,
    tags: list[str] | str | None = None,
) -> dict:
    """Start a timer on a Task for the authenticated user.

    Only one timer runs at a time — starting this one stops any timer already
    running. Check `get_running_timer` first if that matters.

    Args:
        task_id: Task to track time against.
        workspace_id: Omit if the user has only one Workspace.
        description: What is being worked on.
        billable: Mark the entry billable.
        tags: Time-entry tag names (not Task tags).
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "tid": require_id(task_id, "task_id"),
            "description": description,
            "billable": billable,
            "tags": as_list(tags),
        }
    )
    payload = await client.post(f"/v2/team/{team_id}/time_entries/start", body)
    return summarize_time_entry(payload.get("data") or payload)


@tool(phase=2)
async def stop_timer(workspace_id: str | None = None) -> dict:
    """Stop the authenticated user's running timer.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.post(f"/v2/team/{team_id}/time_entries/stop")
    return summarize_time_entry(payload.get("data") or payload)


@tool(phase=2)
async def create_time_entry(
    task_id: str,
    start: str,
    duration_minutes: int,
    workspace_id: str | None = None,
    description: str | None = None,
    billable: bool | None = None,
    assignee: str | None = None,
    tags: list[str] | str | None = None,
) -> dict:
    """Log time that was already worked, as a completed entry.

    Use this for "I worked 2 hours on X yesterday". For tracking live, use
    `start_timer` / `stop_timer`.

    Args:
        task_id: Task to log time against.
        start: When the work started. ISO-8601 date/datetime, or Unix ms.
        duration_minutes: How long, in minutes.
        workspace_id: Omit if the user has only one Workspace.
        description: What was worked on.
        billable: Mark the entry billable.
        assignee: ClickUp user id to log for. Defaults to the authenticated user;
            logging for someone else needs Workspace permissions.
        tags: Time-entry tag names.
    """
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive.")
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "tid": require_id(task_id, "task_id"),
            "start": to_unix_ms(start, "start"),
            "duration": duration_minutes * 60_000,
            "description": description,
            "billable": billable,
            "assignee": assignee,
            "tags": as_list(tags),
        }
    )
    payload = await client.post(f"/v2/team/{team_id}/time_entries", body)
    return summarize_time_entry(payload.get("data") or payload)


@tool(phase=2)
async def get_time_entry(
    timer_id: str, workspace_id: str | None = None, raw: bool = False
) -> dict:
    """Get one time entry.

    Args:
        timer_id: Time entry id.
        workspace_id: Omit if the user has only one Workspace.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/time_entries/{require_id(timer_id, 'timer_id')}"
    )
    if raw:
        return payload
    return summarize_time_entry(payload.get("data") or payload)


@tool(phase=2)
async def get_time_entry_history(timer_id: str, workspace_id: str | None = None) -> dict:
    """Get the edit history of a time entry.

    Args:
        timer_id: Time entry id.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v2/team/{team_id}/time_entries/{require_id(timer_id, 'timer_id')}/history"
    )


@tool(phase=2)
async def update_time_entry(
    timer_id: str,
    workspace_id: str | None = None,
    description: str | None = None,
    start: str | None = None,
    end: str | None = None,
    duration_minutes: int | None = None,
    billable: bool | None = None,
    task_id: str | None = None,
    tags: list[str] | str | None = None,
) -> dict:
    """Update a time entry. Only the fields you pass are changed.

    Args:
        timer_id: Time entry id.
        workspace_id: Omit if the user has only one Workspace.
        description: New description.
        start: ISO-8601 date/datetime, or Unix ms.
        end: ISO-8601 date/datetime, or Unix ms.
        duration_minutes: New duration in minutes.
        billable: Mark billable or not.
        task_id: Move the entry to a different Task.
        tags: Replace the entry's tags.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "description": description,
            "start": to_unix_ms(start, "start"),
            "end": to_unix_ms(end, "end"),
            "duration": duration_minutes * 60_000 if duration_minutes is not None else None,
            "billable": billable,
            "tid": optional_id(task_id, "task_id"),
            "tags": as_list(tags),
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    payload = await client.put(
        f"/v2/team/{team_id}/time_entries/{require_id(timer_id, 'timer_id')}", body
    )
    return summarize_time_entry(payload.get("data") or payload)


@tool(phase=2, destructive=True)
async def delete_time_entry(
    timer_id: str, workspace_id: str | None = None, confirm: bool = False
) -> dict:
    """PERMANENTLY delete a time entry.

    Logged time is often billing data — confirm with the user before removing it.

    Args:
        timer_id: Time entry id.
        workspace_id: Omit if the user has only one Workspace.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this time entry")
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(
        f"/v2/team/{team_id}/time_entries/{require_id(timer_id, 'timer_id')}"
    )
    return {"deleted": True, "timer_id": timer_id}


@tool(phase=2)
async def list_time_entry_tags(workspace_id: str | None = None) -> dict:
    """List all tags used on time entries in the Workspace.

    These are separate from Task tags.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/time_entries/tags")


@tool(phase=2)
async def add_time_entry_tags(
    timer_ids: list[str] | str, tags: list[dict] | list[str] | str, workspace_id: str | None = None
) -> dict:
    """Add tags to one or more time entries.

    Args:
        timer_ids: Time entry ids.
        tags: Tag names, or full tag objects with name/tag_bg/tag_fg.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    normalized = [{"name": t} if isinstance(t, str) else t for t in (as_list(tags) or [])]
    return await client.post(
        f"/v2/team/{team_id}/time_entries/tags",
        {"time_entry_ids": as_list(timer_ids), "tags": normalized},
    )


@tool(phase=2)
async def remove_time_entry_tags(
    timer_ids: list[str] | str,
    tags: list[str] | str,
    workspace_id: str | None = None,
) -> dict:
    """Remove tags from one or more time entries.

    Reversible — the entries themselves are untouched and `add_time_entry_tags`
    puts the tags back.

    Args:
        timer_ids: Time entry ids.
        tags: Tag names to remove.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    normalized = [{"name": t} if isinstance(t, str) else t for t in (as_list(tags) or [])]
    return await client.delete(
        f"/v2/team/{team_id}/time_entries/tags",
        params={"time_entry_ids": as_list(timer_ids), "tags": normalized},
    )


@tool(phase=2)
async def rename_time_entry_tag(
    old_name: str,
    new_name: str,
    workspace_id: str | None = None,
    tag_bg: str | None = None,
    tag_fg: str | None = None,
) -> dict:
    """Rename a time-entry tag everywhere it is used.

    Args:
        old_name: Current tag name.
        new_name: New tag name.
        workspace_id: Omit if the user has only one Workspace.
        tag_bg: Background colour, e.g. "#FF0000".
        tag_fg: Foreground colour.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean({"name": old_name, "new_name": new_name, "tag_bg": tag_bg, "tag_fg": tag_fg})
    return await client.put(f"/v2/team/{team_id}/time_entries/tags", body)


# --- Legacy per-Task time tracking -------------------------------------------
# Superseded by the Workspace-scoped endpoints above. Kept because older
# integrations and some reporting still read these intervals.


@tool(phase=2)
async def get_task_tracked_time(task_id: str) -> dict:
    """Get time tracked against a Task (legacy endpoint).

    Prefer `list_time_entries(task_id=...)`, which returns richer data.

    Args:
        task_id: ClickUp Task id.
    """
    client = await current_client()
    return await client.get(f"/v2/task/{require_id(task_id, 'task_id')}/time")


@tool(phase=2)
async def track_task_time(
    task_id: str, start: str, end: str, duration_minutes: int, assignee: str | None = None
) -> dict:
    """Record tracked time on a Task (legacy endpoint).

    Prefer `create_time_entry`.

    Args:
        task_id: ClickUp Task id.
        start: ISO-8601 date/datetime, or Unix ms.
        end: ISO-8601 date/datetime, or Unix ms.
        duration_minutes: Duration in minutes.
        assignee: ClickUp user id. Defaults to the authenticated user.
    """
    client = await current_client()
    body = clean(
        {
            "start": to_unix_ms(start, "start"),
            "end": to_unix_ms(end, "end"),
            "time": duration_minutes * 60_000,
            "assignee": assignee,
        }
    )
    return await client.post(f"/v2/task/{require_id(task_id, 'task_id')}/time", body)


@tool(phase=2)
async def edit_task_tracked_time(
    task_id: str, interval_id: str, start: str, end: str, duration_minutes: int
) -> dict:
    """Edit a tracked-time interval on a Task (legacy endpoint).

    Args:
        task_id: ClickUp Task id.
        interval_id: Interval id from `get_task_tracked_time`.
        start: ISO-8601 date/datetime, or Unix ms.
        end: ISO-8601 date/datetime, or Unix ms.
        duration_minutes: Duration in minutes.
    """
    client = await current_client()
    body = {
        "start": to_unix_ms(start, "start"),
        "end": to_unix_ms(end, "end"),
        "time": duration_minutes * 60_000,
    }
    return await client.put(
        f"/v2/task/{require_id(task_id, 'task_id')}/time/{require_id(interval_id, 'interval_id')}",
        body,
    )


@tool(phase=2, destructive=True)
async def delete_task_tracked_time(
    task_id: str, interval_id: str, confirm: bool = False
) -> dict:
    """Delete a tracked-time interval on a Task (legacy endpoint).

    Args:
        task_id: ClickUp Task id.
        interval_id: Interval id from `get_task_tracked_time`.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this tracked time interval")
    client = await current_client()
    await client.delete(
        f"/v2/task/{require_id(task_id, 'task_id')}/time/{require_id(interval_id, 'interval_id')}"
    )
    return {"deleted": True, "task_id": task_id, "interval_id": interval_id}
