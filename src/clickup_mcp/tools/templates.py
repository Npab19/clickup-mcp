"""Template discovery. Creating from a template lives with the thing being created —
see `create_task_from_template`, `create_list_from_template`, `create_folder_from_template`.
"""
from __future__ import annotations

from typing import Any

from clickup_mcp.app import tool
from clickup_mcp.tools._common import client_and_workspace
from clickup_mcp.validation import validate_page


def _summarize(payload: Any, key: str) -> dict:
    items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return payload
    return {
        key: [{"id": t.get("id"), "name": t.get("name")} for t in items if isinstance(t, dict)],
        "count": len(items),
    }


@tool(phase=1)
async def list_task_templates(
    workspace_id: str | None = None, page: int = 0, raw: bool = False
) -> dict:
    """List Task templates in the Workspace.

    Pass a returned id to `create_task_from_template`.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        page: 0-indexed page number.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/taskTemplate", params={"page": validate_page(page)}
    )
    return payload if raw else _summarize(payload, "templates")


@tool(phase=1)
async def list_list_templates(
    workspace_id: str | None = None, page: int = 0, raw: bool = False
) -> dict:
    """List List templates in the Workspace.

    Pass a returned id to `create_list_from_template`.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        page: 0-indexed page number.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/list_template", params={"page": validate_page(page)}
    )
    return payload if raw else _summarize(payload, "templates")


@tool(phase=1)
async def list_folder_templates(
    workspace_id: str | None = None, page: int = 0, raw: bool = False
) -> dict:
    """List Folder templates in the Workspace.

    Pass a returned id to `create_folder_from_template`.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        page: 0-indexed page number.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v2/team/{team_id}/folder_template", params={"page": validate_page(page)}
    )
    return payload if raw else _summarize(payload, "templates")
