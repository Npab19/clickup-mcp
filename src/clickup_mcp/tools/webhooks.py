"""Webhooks. Admin-gated: these push Workspace data to an external URL."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, optional_id, require_confirm, require_id


@tool(phase=4, admin=True)
async def list_webhooks(workspace_id: str | None = None) -> dict:
    """List the webhooks registered on a Workspace by this OAuth app.

    You only see webhooks created by the same app — not ones other integrations
    registered.

    Args:
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(f"/v2/team/{team_id}/webhook")


@tool(phase=4, admin=True)
async def create_webhook(
    endpoint: str,
    events: list[str] | str,
    workspace_id: str | None = None,
    space_id: str | None = None,
    folder_id: str | None = None,
    list_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Register a webhook that POSTs Workspace events to an external URL.

    This sends ClickUp data off to a third-party endpoint continuously until it is
    deleted. Confirm the destination URL with the user — a typo leaks Workspace
    activity to whoever owns that host.

    Pass at most one of space_id / folder_id / list_id / task_id to narrow scope;
    with none, the webhook covers the whole Workspace.

    Args:
        endpoint: HTTPS URL to receive events.
        events: Event names, e.g. ["taskCreated", "taskUpdated"], or ["*"] for all.
        workspace_id: Omit if the user has only one Workspace.
        space_id: Only fire for this Space.
        folder_id: Only fire for this Folder.
        list_id: Only fire for this List.
        task_id: Only fire for this Task.
    """
    if not endpoint.lower().startswith("https://"):
        raise ValueError("endpoint must be an https:// URL.")

    scopes = [s for s in (space_id, folder_id, list_id, task_id) if s]
    if len(scopes) > 1:
        raise ValueError("Pass at most one of space_id, folder_id, list_id, or task_id.")

    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "endpoint": endpoint,
            "events": as_list(events),
            "space_id": optional_id(space_id, "space_id"),
            "folder_id": optional_id(folder_id, "folder_id"),
            "list_id": optional_id(list_id, "list_id"),
            "task_id": optional_id(task_id, "task_id"),
        }
    )
    return await client.post(f"/v2/team/{team_id}/webhook", body)


@tool(phase=4, admin=True)
async def update_webhook(
    webhook_id: str,
    endpoint: str | None = None,
    events: list[str] | str | None = None,
    status: str | None = None,
) -> dict:
    """Update a webhook's endpoint, events, or status.

    Args:
        webhook_id: ClickUp webhook id.
        endpoint: New HTTPS URL.
        events: New event list, or ["*"] for all.
        status: "active" or "suspended".
    """
    if endpoint and not endpoint.lower().startswith("https://"):
        raise ValueError("endpoint must be an https:// URL.")
    client = await current_client()
    body = clean({"endpoint": endpoint, "events": as_list(events), "status": status})
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/webhook/{require_id(webhook_id, 'webhook_id')}", body)


@tool(phase=4, admin=True, destructive=True)
async def delete_webhook(webhook_id: str, confirm: bool = False) -> dict:
    """Delete a webhook, stopping event delivery to its endpoint.

    Whatever integration depends on it will stop receiving events.

    Args:
        webhook_id: ClickUp webhook id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this webhook")
    client = await current_client()
    await client.delete(f"/v2/webhook/{require_id(webhook_id, 'webhook_id')}")
    return {"deleted": True, "webhook_id": webhook_id}
