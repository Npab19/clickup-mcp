"""Views — saved List/Board/Calendar/Gantt configurations at any hierarchy level."""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_task
from clickup_mcp.tools._common import client_and_workspace
from clickup_mcp.validation import require_confirm, require_id, validate_page

_PARENTS = {
    "space": "/v2/space/{id}/view",
    "folder": "/v2/folder/{id}/view",
    "list": "/v2/list/{id}/view",
}

_VIEW_TYPES = {
    "list", "board", "calendar", "table", "timeline", "gantt", "activity",
    "map", "workload", "conversation", "doc",
}


def _summarize_view(view: Any) -> dict:
    if not isinstance(view, dict):
        return view
    summary = {
        "id": view.get("id"),
        "name": view.get("name"),
        "type": view.get("type"),
        "parent": (view.get("parent") or {}).get("id")
        if isinstance(view.get("parent"), dict)
        else None,
        "visibility": view.get("visibility"),
    }
    return {k: v for k, v in summary.items() if v is not None}


@tool(phase=2)
async def list_views(
    parent_type: str,
    parent_id: str | None = None,
    workspace_id: str | None = None,
    raw: bool = False,
) -> dict:
    """List the views on a Workspace, Space, Folder, or List.

    Args:
        parent_type: workspace, space, folder, or list.
        parent_id: Id of the Space, Folder, or List. Omit when
            parent_type=workspace.
        workspace_id: Used when parent_type=workspace. Omit if the user has one
            Workspace.
        raw: Return ClickUp's full response, including every filter and grouping
            rule per view.
    """
    if parent_type == "workspace":
        client, team_id = await client_and_workspace(workspace_id)
        path = f"/v2/team/{team_id}/view"
    elif parent_type in _PARENTS:
        if not parent_id:
            raise ValueError(f"parent_id is required when parent_type={parent_type!r}.")
        client = await current_client()
        path = _PARENTS[parent_type].format(id=require_id(parent_id, "parent_id"))
    else:
        raise ValueError(
            f"parent_type must be workspace, space, folder, or list, got {parent_type!r}"
        )

    payload = await client.get(path)
    if raw:
        return payload
    views = payload.get("views") or []
    required = payload.get("required_views") or {}
    return {
        "views": [_summarize_view(v) for v in views],
        "count": len(views),
        "required_views": {k: _summarize_view(v) for k, v in required.items()}
        if isinstance(required, dict)
        else required,
    }


@tool(phase=2)
async def get_view(view_id: str, raw: bool = False) -> dict:
    """Get one view's configuration.

    Args:
        view_id: ClickUp view id.
        raw: Return ClickUp's full response, including filters and grouping.
    """
    client = await current_client()
    payload = await client.get(f"/v2/view/{require_id(view_id, 'view_id')}")
    if raw:
        return payload
    return _summarize_view(payload.get("view") or payload)


@tool(phase=2)
async def get_view_tasks(view_id: str, page: int = 0, raw: bool = False) -> dict:
    """Get the Tasks a view resolves to, with its filters and sorting applied.

    Handy when the user refers to "my sprint board" — the view already encodes
    which Tasks they mean.

    Args:
        view_id: ClickUp view id.
        page: 0-indexed page number.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(
        f"/v2/view/{require_id(view_id, 'view_id')}/task",
        params={"page": validate_page(page)},
    )
    return payload if raw else collection(payload, "tasks", summarize_task)


@tool(phase=4)
async def create_view(
    name: str,
    view_type: str,
    parent_type: str,
    parent_id: str | None = None,
    workspace_id: str | None = None,
    grouping: dict | None = None,
    filters: dict | None = None,
    settings: dict | None = None,
) -> dict:
    """Create a view on a Workspace, Space, Folder, or List.

    ClickUp requires a fairly complete configuration. Omit grouping/filters/settings
    to accept its defaults, then refine with `update_view`.

    Args:
        name: View name.
        view_type: list, board, calendar, table, timeline, gantt, activity, map,
            workload, conversation, or doc.
        parent_type: workspace, space, folder, or list.
        parent_id: Id of the Space, Folder, or List.
        workspace_id: Used when parent_type=workspace.
        grouping: ClickUp grouping config.
        filters: ClickUp filter config.
        settings: ClickUp view settings.
    """
    if view_type not in _VIEW_TYPES:
        raise ValueError(
            f"view_type must be one of {', '.join(sorted(_VIEW_TYPES))}, got {view_type!r}"
        )

    if parent_type == "workspace":
        client, team_id = await client_and_workspace(workspace_id)
        path = f"/v2/team/{team_id}/view"
    elif parent_type in _PARENTS:
        if not parent_id:
            raise ValueError(f"parent_id is required when parent_type={parent_type!r}.")
        client = await current_client()
        path = _PARENTS[parent_type].format(id=require_id(parent_id, "parent_id"))
    else:
        raise ValueError(
            f"parent_type must be workspace, space, folder, or list, got {parent_type!r}"
        )

    body: dict[str, Any] = {"name": name, "type": view_type}
    if grouping:
        body["grouping"] = grouping
    if filters:
        body["filters"] = filters
    if settings:
        body["settings"] = settings
    return await client.post(path, body)


@tool(phase=4)
async def update_view(
    view_id: str,
    name: str | None = None,
    grouping: dict | None = None,
    filters: dict | None = None,
    settings: dict | None = None,
) -> dict:
    """Update a view.

    ClickUp replaces the whole view definition on update, so read the current
    config with `get_view(raw=True)` first and send it back with your changes
    merged in — otherwise unsent sections are lost.

    Args:
        view_id: ClickUp view id.
        name: New name.
        grouping: Full grouping config.
        filters: Full filter config.
        settings: Full view settings.
    """
    client = await current_client()
    body = {
        k: v
        for k, v in {
            "name": name,
            "grouping": grouping,
            "filters": filters,
            "settings": settings,
        }.items()
        if v is not None
    }
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/view/{require_id(view_id, 'view_id')}", body)


@tool(phase=4, destructive=True)
async def delete_view(view_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a view.

    The Tasks are untouched — only the saved view configuration is destroyed. It
    may still be someone else's default board.

    Args:
        view_id: ClickUp view id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this view")
    client = await current_client()
    await client.delete(f"/v2/view/{require_id(view_id, 'view_id')}")
    return {"deleted": True, "view_id": view_id}
