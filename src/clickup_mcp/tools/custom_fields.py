"""Custom fields.

Custom field values are not editable through `update_task` — they go through
`set_custom_field_value` with the field's id, which is why discovering ids and
their type configs matters.
"""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import client_and_workspace
from clickup_mcp.validation import require_id

_SCOPES = {
    "list": "/v2/list/{id}/field",
    "folder": "/v2/folder/{id}/field",
    "space": "/v2/space/{id}/field",
}


@tool(phase=1)
async def list_accessible_custom_fields(
    scope: str = "list",
    scope_id: str | None = None,
    workspace_id: str | None = None,
    raw: bool = False,
) -> dict:
    """List custom fields available in a List, Folder, Space, or Workspace.

    Call this before `set_custom_field_value` — you need the field id, and the
    field's `type` tells you what shape its value must be (e.g. a drop_down takes
    the id of one of its options, not the option's label).

    Args:
        scope: list, folder, space, or workspace.
        scope_id: Id of the List, Folder, or Space. Omit when scope=workspace.
        workspace_id: Used when scope=workspace. Omit if the user has one Workspace.
        raw: Return every field's full type_config. Needed to see drop-down option
            ids, but verbose.
    """
    if scope == "workspace":
        client, team_id = await client_and_workspace(workspace_id)
        path = f"/v2/team/{team_id}/field"
    elif scope in _SCOPES:
        if not scope_id:
            raise ValueError(f"scope_id is required when scope={scope!r}.")
        client = await current_client()
        path = _SCOPES[scope].format(id=require_id(scope_id, "scope_id"))
    else:
        raise ValueError(
            f"scope must be list, folder, space, or workspace, got {scope!r}"
        )

    payload = await client.get(path)
    if raw:
        return payload

    fields = payload.get("fields") or []
    summarized = []
    for f in fields:
        entry: dict[str, Any] = {
            "id": f.get("id"),
            "name": f.get("name"),
            "type": f.get("type"),
        }
        # Drop-down and label values are set by option id, so surface the mapping.
        config = f.get("type_config") or {}
        options = config.get("options")
        if isinstance(options, list) and options:
            entry["options"] = [
                {"id": o.get("id"), "name": o.get("name") or o.get("label")}
                for o in options
                if isinstance(o, dict)
            ]
        summarized.append(entry)
    return {"fields": summarized, "count": len(summarized)}


@tool(phase=1)
async def set_custom_field_value(
    task_id: str,
    field_id: str,
    value: Any,
    value_options: dict | None = None,
) -> dict:
    """Set a custom field's value on a Task.

    The shape of `value` depends on the field type — check
    `list_accessible_custom_fields` first:
      - text / short_text / url / email / phone: a string
      - number / currency: a number
      - date: Unix ms (pass value_options={"time": true} to include a time)
      - drop_down: the option's **id**, not its label
      - labels: a list of option ids
      - checkbox: true or false
      - users: {"add": [user_id], "rem": [user_id]}
      - tasks: {"add": [task_id], "rem": [task_id]}

    Args:
        task_id: ClickUp Task id.
        field_id: Custom field id (a UUID) from `list_accessible_custom_fields`.
        value: The new value, shaped per the field type.
        value_options: Extra options, e.g. {"time": true} for date fields.
    """
    client = await current_client()
    body: dict[str, Any] = {"value": value}
    if value_options:
        body["value_options"] = value_options
    return await client.post(
        f"/v2/task/{require_id(task_id, 'task_id')}/field/{require_id(field_id, 'field_id')}",
        body,
    )


@tool(phase=1)
async def remove_custom_field_value(task_id: str, field_id: str) -> dict:
    """Clear a custom field's value on a Task.

    This removes the value only — the field itself stays on the List.

    Args:
        task_id: ClickUp Task id.
        field_id: Custom field id (a UUID).
    """
    client = await current_client()
    await client.delete(
        f"/v2/task/{require_id(task_id, 'task_id')}/field/{require_id(field_id, 'field_id')}"
    )
    return {"cleared": True, "task_id": task_id, "field_id": field_id}
