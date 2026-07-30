"""Task checklists and their items."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean
from clickup_mcp.validation import optional_id, require_confirm, require_id


@tool(phase=2)
async def create_checklist(task_id: str, name: str) -> dict:
    """Add a checklist to a Task.

    Checklists appear on `get_task(raw=True)`; there is no endpoint to list them
    on their own.

    Args:
        task_id: ClickUp Task id.
        name: Checklist name.
    """
    client = await current_client()
    return await client.post(
        f"/v2/task/{require_id(task_id, 'task_id')}/checklist", {"name": name}
    )


@tool(phase=2)
async def update_checklist(
    checklist_id: str, name: str | None = None, position: int | None = None
) -> dict:
    """Rename or reorder a checklist.

    Args:
        checklist_id: ClickUp checklist id.
        name: New name.
        position: 0-indexed position among the Task's checklists.
    """
    client = await current_client()
    body = clean({"name": name, "position": position})
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/checklist/{require_id(checklist_id, 'checklist_id')}", body)


@tool(phase=2, destructive=True)
async def delete_checklist(checklist_id: str, confirm: bool = False) -> dict:
    """Delete a checklist and all of its items.

    Args:
        checklist_id: ClickUp checklist id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this checklist")
    client = await current_client()
    await client.delete(f"/v2/checklist/{require_id(checklist_id, 'checklist_id')}")
    return {"deleted": True, "checklist_id": checklist_id}


@tool(phase=2)
async def create_checklist_item(
    checklist_id: str, name: str, assignee_id: str | None = None
) -> dict:
    """Add an item to a checklist.

    Args:
        checklist_id: ClickUp checklist id.
        name: Item text.
        assignee_id: ClickUp user id to assign the item to.
    """
    client = await current_client()
    body = clean({"name": name, "assignee": optional_id(assignee_id, "assignee_id")})
    return await client.post(
        f"/v2/checklist/{require_id(checklist_id, 'checklist_id')}/checklist_item", body
    )


@tool(phase=2)
async def update_checklist_item(
    checklist_id: str,
    checklist_item_id: str,
    name: str | None = None,
    resolved: bool | None = None,
    assignee_id: str | None = None,
    parent_item_id: str | None = None,
) -> dict:
    """Update a checklist item — rename it, tick it off, assign it, or nest it.

    Args:
        checklist_id: ClickUp checklist id.
        checklist_item_id: Item id.
        name: New item text.
        resolved: True to tick the item off.
        assignee_id: ClickUp user id to assign the item to.
        parent_item_id: Nest this item under another item in the same checklist.
    """
    client = await current_client()
    body = clean(
        {
            "name": name,
            "resolved": resolved,
            "assignee": optional_id(assignee_id, "assignee_id"),
            "parent": optional_id(parent_item_id, "parent_item_id"),
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(
        f"/v2/checklist/{require_id(checklist_id, 'checklist_id')}"
        f"/checklist_item/{require_id(checklist_item_id, 'checklist_item_id')}",
        body,
    )


@tool(phase=2, destructive=True)
async def delete_checklist_item(
    checklist_id: str, checklist_item_id: str, confirm: bool = False
) -> dict:
    """Delete an item from a checklist.

    Args:
        checklist_id: ClickUp checklist id.
        checklist_item_id: Item id.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this checklist item")
    client = await current_client()
    await client.delete(
        f"/v2/checklist/{require_id(checklist_id, 'checklist_id')}"
        f"/checklist_item/{require_id(checklist_item_id, 'checklist_item_id')}"
    )
    return {"deleted": True, "checklist_item_id": checklist_item_id}
