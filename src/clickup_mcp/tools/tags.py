"""Task tags. Tags are defined on a Space, then attached to Tasks by name."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.tools._common import clean
from clickup_mcp.validation import require_confirm, require_id


@tool(phase=2)
async def list_space_tags(space_id: str) -> dict:
    """List the tags defined in a Space.

    A tag must exist in the Space before it can be added to a Task in it.

    Args:
        space_id: ClickUp Space id.
    """
    client = await current_client()
    return await client.get(f"/v2/space/{require_id(space_id, 'space_id')}/tag")


@tool(phase=2)
async def create_space_tag(
    space_id: str, name: str, tag_bg: str | None = None, tag_fg: str | None = None
) -> dict:
    """Create a tag in a Space.

    Args:
        space_id: ClickUp Space id.
        name: Tag name.
        tag_bg: Background colour, e.g. "#FF0000".
        tag_fg: Foreground colour, e.g. "#FFFFFF".
    """
    client = await current_client()
    tag = clean({"name": name, "tag_bg": tag_bg, "tag_fg": tag_fg})
    return await client.post(f"/v2/space/{require_id(space_id, 'space_id')}/tag", {"tag": tag})


@tool(phase=2)
async def update_space_tag(
    space_id: str,
    tag_name: str,
    new_name: str | None = None,
    tag_bg: str | None = None,
    tag_fg: str | None = None,
) -> dict:
    """Rename or recolour a Space tag.

    Renaming updates the tag on every Task using it.

    Args:
        space_id: ClickUp Space id.
        tag_name: Current tag name.
        new_name: New tag name.
        tag_bg: Background colour.
        tag_fg: Foreground colour.
    """
    client = await current_client()
    tag = clean({"name": new_name or tag_name, "tag_bg": tag_bg, "tag_fg": tag_fg})
    return await client.put(
        f"/v2/space/{require_id(space_id, 'space_id')}/tag/{tag_name}", {"tag": tag}
    )


@tool(phase=2, destructive=True)
async def delete_space_tag(space_id: str, tag_name: str, confirm: bool = False) -> dict:
    """Delete a tag from a Space, removing it from every Task that has it.

    Args:
        space_id: ClickUp Space id.
        tag_name: Tag name to delete.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, f"delete the tag {tag_name!r} from this Space")
    client = await current_client()
    await client.delete(
        f"/v2/space/{require_id(space_id, 'space_id')}/tag/{tag_name}",
        params={"name": tag_name},
    )
    return {"deleted": True, "space_id": space_id, "tag": tag_name}


@tool(phase=2)
async def add_tag_to_task(task_id: str, tag_name: str) -> dict:
    """Add an existing Space tag to a Task.

    The tag must already exist in the Task's Space — create it with
    `create_space_tag` first.

    Args:
        task_id: ClickUp Task id.
        tag_name: Tag name.
    """
    client = await current_client()
    await client.post(f"/v2/task/{require_id(task_id, 'task_id')}/tag/{tag_name}")
    return {"added": True, "task_id": task_id, "tag": tag_name}


@tool(phase=2)
async def remove_tag_from_task(task_id: str, tag_name: str) -> dict:
    """Remove a tag from a Task.

    The tag itself stays defined on the Space.

    Args:
        task_id: ClickUp Task id.
        tag_name: Tag name.
    """
    client = await current_client()
    await client.delete(f"/v2/task/{require_id(task_id, 'task_id')}/tag/{tag_name}")
    return {"removed": True, "task_id": task_id, "tag": tag_name}
