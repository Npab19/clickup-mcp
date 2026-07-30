"""Goals and their key results (targets)."""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, require_confirm, require_id, to_unix_ms

_KEY_RESULT_TYPES = {"number", "currency", "boolean", "percentage", "automatic"}


def _summarize_goal(goal: Any) -> dict:
    if not isinstance(goal, dict):
        return goal
    summary = {
        "id": goal.get("id"),
        "name": goal.get("name"),
        "owner": (goal.get("owner") or {}).get("username")
        if isinstance(goal.get("owner"), dict)
        else None,
        "due_date": None,
        "percent_completed": goal.get("percent_completed"),
        "completed": goal.get("completed"),
        "key_result_count": goal.get("key_result_count"),
    }
    from clickup_mcp.transform import ms_to_iso

    summary["due_date"] = ms_to_iso(goal.get("due_date"))
    return {k: v for k, v in summary.items() if v is not None}


@tool(phase=4)
async def list_goals(
    workspace_id: str | None = None, include_completed: bool = False, raw: bool = False
) -> dict:
    """List the Goals in a Workspace.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        include_completed: Include completed Goals.
        raw: Return ClickUp's full response, including every key result.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/goal", params={"include_completed": include_completed}
    )
    if raw:
        return payload
    goals = payload.get("goals") or []
    return {
        "goals": [_summarize_goal(g) for g in goals],
        "count": len(goals),
        "goal_folders": payload.get("folders"),
    }


@tool(phase=4)
async def get_goal(goal_id: str, raw: bool = False) -> dict:
    """Get one Goal with its key results.

    Args:
        goal_id: ClickUp Goal id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/goal/{require_id(goal_id, 'goal_id')}")
    return payload if raw else _summarize_goal(payload.get("goal") or payload)


@tool(phase=4)
async def create_goal(
    name: str,
    due_date: str,
    workspace_id: str | None = None,
    description: str | None = None,
    owner_ids: list[str] | str | None = None,
    color: str | None = None,
    multiple_owners: bool = True,
) -> dict:
    """Create a Goal in a Workspace.

    A Goal on its own tracks nothing — add key results with `create_key_result`.

    Args:
        name: Goal name.
        due_date: ISO-8601 date/datetime, or Unix ms.
        workspace_id: Omit if the user has only one Workspace.
        description: Goal description.
        owner_ids: ClickUp user ids who own the Goal.
        color: Hex colour, e.g. "#32a852".
        multiple_owners: Allow more than one owner.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "name": name,
            "due_date": to_unix_ms(due_date, "due_date"),
            "description": description,
            "owners": as_list(owner_ids),
            "color": color,
            "multiple_owners": multiple_owners,
        }
    )
    return await client.post(f"/v2/team/{team_id}/goal", body)


@tool(phase=4)
async def update_goal(
    goal_id: str,
    name: str | None = None,
    due_date: str | None = None,
    description: str | None = None,
    add_owner_ids: list[str] | str | None = None,
    remove_owner_ids: list[str] | str | None = None,
    color: str | None = None,
) -> dict:
    """Update a Goal. Owners are edited additively.

    Args:
        goal_id: ClickUp Goal id.
        name: New name.
        due_date: ISO-8601 date/datetime, or Unix ms.
        description: New description.
        add_owner_ids: ClickUp user ids to add as owners.
        remove_owner_ids: ClickUp user ids to remove as owners.
        color: Hex colour.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "due_date": to_unix_ms(due_date, "due_date"),
            "description": description,
            "add_owners": as_list(add_owner_ids),
            "rem_owners": as_list(remove_owner_ids),
            "color": color,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/goal/{require_id(goal_id, 'goal_id')}", body)


@tool(phase=4, destructive=True)
async def delete_goal(goal_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a Goal and all of its key results.

    Args:
        goal_id: ClickUp Goal id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this Goal and its key results")
    client = await current_client()
    await client.delete(f"/v2/goal/{require_id(goal_id, 'goal_id')}")
    return {"deleted": True, "goal_id": goal_id}


@tool(phase=4)
async def create_key_result(
    goal_id: str,
    name: str,
    key_result_type: str,
    owner_ids: list[str] | str | None = None,
    steps_start: int | None = None,
    steps_end: int | None = None,
    unit: str | None = None,
    task_ids: list[str] | str | None = None,
    list_ids: list[str] | str | None = None,
) -> dict:
    """Add a key result (target) to a Goal.

    Args:
        goal_id: ClickUp Goal id.
        name: Key result name.
        key_result_type: number, currency, boolean, percentage, or automatic.
        owner_ids: ClickUp user ids responsible for it.
        steps_start: Starting value.
        steps_end: Target value.
        unit: Unit label, e.g. "customers" or "USD".
        task_ids: Task ids to roll up into an automatic key result.
        list_ids: List ids to roll up into an automatic key result.
    """
    if key_result_type not in _KEY_RESULT_TYPES:
        raise ValueError(
            f"key_result_type must be one of {', '.join(sorted(_KEY_RESULT_TYPES))}, "
            f"got {key_result_type!r}"
        )
    client = await current_client()
    body = clean(
        {
            "name": name,
            "type": key_result_type,
            "owners": as_list(owner_ids),
            "steps_start": steps_start,
            "steps_end": steps_end,
            "unit": unit,
            "task_ids": as_list(task_ids),
            "list_ids": as_list(list_ids),
        }
    )
    return await client.post(f"/v2/goal/{require_id(goal_id, 'goal_id')}/key_result", body)


@tool(phase=4)
async def update_key_result(
    key_result_id: str,
    steps_current: int | None = None,
    note: str | None = None,
    name: str | None = None,
) -> dict:
    """Update a key result's progress.

    Args:
        key_result_id: ClickUp key result id.
        steps_current: Current value — this is what moves the Goal's progress.
        note: Note to attach to the update.
        name: New key result name.
    """
    client = await current_client()
    body = clean({"steps_current": steps_current, "note": note, "name": name})
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(
        f"/v2/key_result/{require_id(key_result_id, 'key_result_id')}", body
    )


@tool(phase=4, destructive=True)
async def delete_key_result(key_result_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a key result from its Goal.

    Args:
        key_result_id: ClickUp key result id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this key result")
    client = await current_client()
    await client.delete(f"/v2/key_result/{require_id(key_result_id, 'key_result_id')}")
    return {"deleted": True, "key_result_id": key_result_id}
