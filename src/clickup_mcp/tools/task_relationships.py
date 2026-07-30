"""Task dependencies (blocking relationships) and links (loose associations)."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.validation import require_confirm, require_id


@tool(phase=2)
async def add_task_dependency(
    task_id: str,
    depends_on_task_id: str | None = None,
    blocks_task_id: str | None = None,
) -> dict:
    """Make one Task depend on, or block, another.

    Pass exactly one of depends_on_task_id or blocks_task_id:
      - depends_on: `task_id` cannot start until that Task is done.
      - blocks: that Task cannot start until `task_id` is done.

    Args:
        task_id: The Task the dependency is being added to.
        depends_on_task_id: Task that must finish first.
        blocks_task_id: Task that is waiting on this one.
    """
    if bool(depends_on_task_id) == bool(blocks_task_id):
        raise ValueError("Pass exactly one of depends_on_task_id or blocks_task_id.")

    client = await current_client()
    body = (
        {"depends_on": require_id(depends_on_task_id, "depends_on_task_id")}
        if depends_on_task_id
        else {"dependency_of": require_id(blocks_task_id, "blocks_task_id")}
    )
    await client.post(f"/v2/task/{require_id(task_id, 'task_id')}/dependency", body)
    return {"linked": True, "task_id": task_id, **body}


@tool(phase=2)
async def remove_task_dependency(
    task_id: str,
    depends_on_task_id: str | None = None,
    blocks_task_id: str | None = None,
) -> dict:
    """Remove a dependency between two Tasks.

    Reversible — `add_task_dependency` puts it back.

    Args:
        task_id: The Task the dependency is being removed from.
        depends_on_task_id: The Task it currently depends on.
        blocks_task_id: The Task it currently blocks.
    """
    if bool(depends_on_task_id) == bool(blocks_task_id):
        raise ValueError("Pass exactly one of depends_on_task_id or blocks_task_id.")

    client = await current_client()
    params = (
        {"depends_on": require_id(depends_on_task_id, "depends_on_task_id")}
        if depends_on_task_id
        else {"dependency_of": require_id(blocks_task_id, "blocks_task_id")}
    )
    await client.delete(f"/v2/task/{require_id(task_id, 'task_id')}/dependency", params=params)
    return {"removed": True, "task_id": task_id, **params}


@tool(phase=2)
async def add_task_link(task_id: str, links_to_task_id: str) -> dict:
    """Link two Tasks together.

    A link is a loose association with no scheduling effect — use
    `add_task_dependency` when one Task genuinely blocks another.

    Args:
        task_id: First Task id.
        links_to_task_id: Task to link it to.
    """
    client = await current_client()
    return await client.post(
        f"/v2/task/{require_id(task_id, 'task_id')}"
        f"/link/{require_id(links_to_task_id, 'links_to_task_id')}"
    )


@tool(phase=2)
async def remove_task_link(task_id: str, links_to_task_id: str) -> dict:
    """Remove a link between two Tasks.

    Reversible — `add_task_link` puts it back.

    Args:
        task_id: First Task id.
        links_to_task_id: The linked Task.
    """
    client = await current_client()
    return await client.delete(
        f"/v2/task/{require_id(task_id, 'task_id')}"
        f"/link/{require_id(links_to_task_id, 'links_to_task_id')}"
    )
