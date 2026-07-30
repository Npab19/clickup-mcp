"""Workspace audit logs (API v3). Enterprise plans only, and admin-gated.

This is ClickUp's own audit trail of Workspace activity — separate from this
server's `audit_log` table, which records MCP tool calls.
"""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, to_unix_ms


# A POST that only queries — the verb would otherwise infer a mutation.
@tool(phase=4, admin=True, read_only=True, idempotent=True)
async def query_audit_logs(
    workspace_id: str | None = None,
    user_ids: list[str] | str | None = None,
    user_emails: list[str] | str | None = None,
    event_types: list[str] | str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = 50,
    page_number: int = 0,
) -> dict:
    """Query the Workspace's audit log. Enterprise plans only.

    Returns ClickUp's record of who did what in the Workspace. If you want to know
    what this MCP server did, that is a different trail — see the server's own
    audit_log table.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        user_ids: Only actions by these ClickUp user ids.
        user_emails: Only actions by these email addresses.
        event_types: Only these event types.
        start_date: ISO-8601 date/datetime, or Unix ms.
        end_date: ISO-8601 date/datetime, or Unix ms.
        page_size: Rows per page.
        page_number: 0-indexed page number.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "filter": clean(
                {
                    "userId": as_list(user_ids),
                    "userEmail": as_list(user_emails),
                    "eventType": as_list(event_types),
                    "startTime": to_unix_ms(start_date, "start_date"),
                    "endTime": to_unix_ms(end_date, "end_date"),
                }
            )
            or None,
            "pagination": {"pageSize": page_size, "pageNumber": page_number},
        }
    )
    return await client.post(f"/v3/workspaces/{team_id}/auditlogs", body)
