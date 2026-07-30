"""Guest administration. Admin-gated.

Guests are external people given access to specific Tasks, Lists, or Folders.
Adding one shares real content outside the Workspace. Enterprise plans only.
"""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import require_confirm, require_id

_SCOPES = {
    "task": "/v2/task/{id}/guest/{guest_id}",
    "list": "/v2/list/{id}/guest/{guest_id}",
    "folder": "/v2/folder/{id}/guest/{guest_id}",
}

_PERMISSIONS = {"read", "comment", "edit", "create"}


def _scope_path(scope: str, scope_id: str, guest_id: str) -> str:
    if scope not in _SCOPES:
        raise ValueError(f"scope must be task, list, or folder, got {scope!r}")
    return _SCOPES[scope].format(
        id=require_id(scope_id, "scope_id"), guest_id=require_id(guest_id, "guest_id")
    )


@tool(phase=4, admin=True)
async def get_guest(guest_id: str, workspace_id: str | None = None) -> dict:
    """Get a guest and what they can see. Enterprise plans only.

    Args:
        guest_id: ClickUp guest id.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v2/team/{team_id}/guest/{require_id(guest_id, 'guest_id')}"
    )


@tool(phase=4, admin=True)
async def invite_guest_to_workspace(
    email: str,
    workspace_id: str | None = None,
    can_edit_tags: bool = False,
    can_see_time_spent: bool = False,
    can_see_time_estimated: bool = False,
    can_create_views: bool = False,
) -> dict:
    """Invite an external person to the Workspace as a guest. Enterprise plans only.

    This sends them an email and consumes a guest seat. Grant access to specific
    content afterwards with `add_guest_to_item`.

    Args:
        email: Email address to invite.
        workspace_id: Omit if the user has only one Workspace.
        can_edit_tags: Allow editing Task tags.
        can_see_time_spent: Allow seeing time tracked.
        can_see_time_estimated: Allow seeing time estimates.
        can_create_views: Allow creating views.
    """
    if "@" not in email:
        raise ValueError(f"email does not look like an address: {email!r}")
    client, team_id = await client_and_workspace(workspace_id)
    body = {
        "email": email,
        "can_edit_tags": can_edit_tags,
        "can_see_time_spent": can_see_time_spent,
        "can_see_time_estimated": can_see_time_estimated,
        "can_create_views": can_create_views,
    }
    return await client.post(f"/v2/team/{team_id}/guest", body)


@tool(phase=4, admin=True)
async def edit_guest(
    guest_id: str,
    workspace_id: str | None = None,
    can_edit_tags: bool | None = None,
    can_see_time_spent: bool | None = None,
    can_see_time_estimated: bool | None = None,
    can_create_views: bool | None = None,
) -> dict:
    """Change a guest's Workspace-level permissions. Enterprise plans only.

    Args:
        guest_id: ClickUp guest id.
        workspace_id: Omit if the user has only one Workspace.
        can_edit_tags: Allow editing Task tags.
        can_see_time_spent: Allow seeing time tracked.
        can_see_time_estimated: Allow seeing time estimates.
        can_create_views: Allow creating views.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "can_edit_tags": can_edit_tags,
            "can_see_time_spent": can_see_time_spent,
            "can_see_time_estimated": can_see_time_estimated,
            "can_create_views": can_create_views,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(
        f"/v2/team/{team_id}/guest/{require_id(guest_id, 'guest_id')}", body
    )


@tool(phase=4, admin=True, destructive=True)
async def remove_guest_from_workspace(
    guest_id: str, workspace_id: str | None = None, confirm: bool = False
) -> dict:
    """Remove a guest from the Workspace entirely. Enterprise plans only.

    They lose access to everything they were shared on.

    Args:
        guest_id: ClickUp guest id.
        workspace_id: Omit if the user has only one Workspace.
        confirm: Must be True.
    """
    require_confirm(confirm, "remove this guest from the Workspace")
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(f"/v2/team/{team_id}/guest/{require_id(guest_id, 'guest_id')}")
    return {"removed": True, "guest_id": guest_id}


@tool(phase=4, admin=True)
async def add_guest_to_item(
    scope: str, scope_id: str, guest_id: str, permission_level: str = "read"
) -> dict:
    """Share a Task, List, or Folder with a guest. Enterprise plans only.

    This exposes real content to someone outside the Workspace. Confirm what is
    being shared and with whom before calling.

    Args:
        scope: task, list, or folder.
        scope_id: Id of the Task, List, or Folder.
        guest_id: ClickUp guest id.
        permission_level: read, comment, edit, or create.
    """
    if permission_level not in _PERMISSIONS:
        raise ValueError(
            f"permission_level must be one of {', '.join(sorted(_PERMISSIONS))}, "
            f"got {permission_level!r}"
        )
    client = await current_client()
    return await client.post(
        _scope_path(scope, scope_id, guest_id), {"permission_level": permission_level}
    )


@tool(phase=4, admin=True)
async def remove_guest_from_item(scope: str, scope_id: str, guest_id: str) -> dict:
    """Stop sharing a Task, List, or Folder with a guest. Enterprise plans only.

    Reversible — `add_guest_to_item` re-shares it. Nothing is deleted; the guest
    simply loses access to that one item.

    Args:
        scope: task, list, or folder.
        scope_id: Id of the Task, List, or Folder.
        guest_id: ClickUp guest id.
    """
    client = await current_client()
    await client.delete(_scope_path(scope, scope_id, guest_id))
    return {"removed": True, "scope": scope, "scope_id": scope_id, "guest_id": guest_id}
