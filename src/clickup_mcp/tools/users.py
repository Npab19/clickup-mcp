"""Workspace member administration. Admin-gated and billing-affecting.

Inviting consumes a seat; removing reassigns or orphans that person's work. Every
tool here is restricted to CLICKUP_ADMIN_EMAILS and requires an Enterprise plan on
the ClickUp side.
"""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import require_confirm, require_id


@tool(phase=4, admin=True)
async def get_workspace_user(
    user_id: str, workspace_id: str | None = None, include_shared: bool = False
) -> dict:
    """Get a member of the Workspace.

    Args:
        user_id: ClickUp user id.
        workspace_id: Omit if the user has only one Workspace.
        include_shared: Include objects shared with this user.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v2/team/{team_id}/user/{require_id(user_id, 'user_id')}",
        params={"include_shared": include_shared},
    )


@tool(phase=4, admin=True)
async def invite_user_to_workspace(
    email: str,
    workspace_id: str | None = None,
    admin: bool = False,
    custom_role_id: int | None = None,
) -> dict:
    """Invite someone to the Workspace as a member. Enterprise plans only.

    This consumes a paid seat and changes billing, and it sends the person an
    email. Check `get_workspace_seats` and confirm with the user first.

    Args:
        email: Email address to invite.
        workspace_id: Omit if the user has only one Workspace.
        admin: Grant Workspace admin rights.
        custom_role_id: Custom role id from `list_custom_roles`.
    """
    if "@" not in email:
        raise ValueError(f"email does not look like an address: {email!r}")
    client, team_id = await client_and_workspace(workspace_id)
    body = clean({"email": email, "admin": admin, "custom_role_id": custom_role_id})
    return await client.post(f"/v2/team/{team_id}/user", body)


@tool(phase=4, admin=True)
async def edit_workspace_user(
    user_id: str,
    workspace_id: str | None = None,
    username: str | None = None,
    admin: bool | None = None,
    custom_role_id: int | None = None,
) -> dict:
    """Change a Workspace member's name or role. Enterprise plans only.

    Granting admin gives that person full control of the Workspace.

    Args:
        user_id: ClickUp user id.
        workspace_id: Omit if the user has only one Workspace.
        username: New display name.
        admin: Grant or revoke Workspace admin rights.
        custom_role_id: Custom role id from `list_custom_roles`.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean({"username": username, "admin": admin, "custom_role_id": custom_role_id})
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(
        f"/v2/team/{team_id}/user/{require_id(user_id, 'user_id')}", body
    )


@tool(phase=4, admin=True, destructive=True)
async def remove_user_from_workspace(
    user_id: str, workspace_id: str | None = None, confirm: bool = False
) -> dict:
    """Remove a member from the Workspace. Enterprise plans only.

    They lose access immediately and their assignments are affected. Name the
    person to the user and get explicit agreement before calling with confirm=True.

    Args:
        user_id: ClickUp user id.
        workspace_id: Omit if the user has only one Workspace.
        confirm: Must be True.
    """
    require_confirm(confirm, "remove this person from the Workspace")
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(f"/v2/team/{team_id}/user/{require_id(user_id, 'user_id')}")
    return {"removed": True, "user_id": user_id}
