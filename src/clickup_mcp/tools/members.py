"""Members — who can see a Task or List.

Useful for mapping a person's name to the ClickUp user id that assignee filters
and assignment fields require.
"""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.validation import require_id


def _summarize_members(payload: Any) -> dict:
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list):
        return payload
    return {
        "members": [
            {
                "id": m.get("id"),
                "username": m.get("username"),
                "email": m.get("email"),
                "role": m.get("role"),
            }
            for m in members
            if isinstance(m, dict)
        ],
        "count": len(members),
    }


@tool(phase=2)
async def get_task_members(task_id: str, raw: bool = False) -> dict:
    """List the people with access to a Task.

    Use this to turn a name into the user id that `search_tasks(assignees=...)`
    and `update_task(add_assignees=...)` need.

    Args:
        task_id: ClickUp Task id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/task/{require_id(task_id, 'task_id')}/member")
    return payload if raw else _summarize_members(payload)


@tool(phase=2)
async def get_list_members(list_id: str, raw: bool = False) -> dict:
    """List the people with access to a List.

    The usual way to find candidate assignees for Tasks in that List.

    Args:
        list_id: ClickUp List id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/list/{require_id(list_id, 'list_id')}/member")
    return payload if raw else _summarize_members(payload)
