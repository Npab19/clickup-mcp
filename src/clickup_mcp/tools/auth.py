"""Identity and Workspace discovery — the natural first calls in any session."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client


@tool(phase=1, title="Who Am I")
async def whoami() -> dict:
    """Show which ClickUp account this session is authenticated as.

    Use this to confirm identity before acting on someone's behalf, or when a call
    fails with a permissions error and you need to know who "you" are.
    """
    client = await current_client()
    grant = client.grant
    return {
        "clickup_user_id": grant.clickup_user_id,
        "username": grant.username,
        "email": grant.email,
        "authorized_workspaces": grant.workspaces,
        "default_workspace_id": client.default_team_id(),
    }


@tool(phase=1)
async def list_workspaces(raw: bool = False) -> dict:
    """List the Workspaces (called "teams" in the ClickUp API) this user authorized.

    Nearly every other tool needs a workspace_id. If the user authorized exactly one
    Workspace it is applied automatically and you can omit the argument everywhere.

    Args:
        raw: Return ClickUp's full response, including every member of every
            Workspace. Large — leave False unless you need member details.
    """
    client = await current_client()
    payload = await client.get("/v2/team")
    if raw:
        return payload
    teams = payload.get("teams") or []
    return {
        "workspaces": [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "member_count": len(t.get("members") or []),
            }
            for t in teams
        ],
        "count": len(teams),
    }
