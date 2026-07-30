"""Folders — the optional layer between a Space and its Lists."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_folder
from clickup_mcp.tools._common import client_and_workspace
from clickup_mcp.validation import require_confirm, require_id


@tool(phase=1)
async def list_folders(space_id: str, archived: bool = False, raw: bool = False) -> dict:
    """List the Folders in a Space.

    Lists can also sit directly in a Space with no Folder — `list_lists` covers
    both, so use that if you are looking for Lists rather than Folders.

    Args:
        space_id: ClickUp Space id.
        archived: Include archived Folders.
        raw: Return ClickUp's full response, which nests every List in every Folder.
    """
    client = await current_client()
    payload = await client.get(
        f"/v2/space/{require_id(space_id, 'space_id')}/folder", params={"archived": archived}
    )
    return payload if raw else collection(payload, "folders", summarize_folder)


@tool(phase=1)
async def get_folder(folder_id: str, raw: bool = False) -> dict:
    """Get one Folder.

    Args:
        folder_id: ClickUp Folder id.
        raw: Return ClickUp's full response, including its nested Lists.
    """
    client = await current_client()
    payload = await client.get(f"/v2/folder/{require_id(folder_id, 'folder_id')}")
    return payload if raw else summarize_folder(payload)


@tool(phase=1)
async def create_folder(space_id: str, name: str) -> dict:
    """Create a Folder in a Space.

    Args:
        space_id: ClickUp Space id.
        name: Folder name.
    """
    client = await current_client()
    return await client.post(
        f"/v2/space/{require_id(space_id, 'space_id')}/folder", {"name": name}
    )


@tool(phase=1)
async def create_folder_from_template(
    space_id: str,
    template_id: str,
    name: str,
    workspace_id: str | None = None,
    options: dict | None = None,
) -> dict:
    """Create a Folder in a Space from a Folder template.

    Call `list_folder_templates` for available template ids.

    Args:
        space_id: ClickUp Space id.
        template_id: Folder template id.
        name: Name for the new Folder.
        workspace_id: Omit if the user has only one Workspace.
        options: ClickUp template options, e.g. which content to import and how to
            remap dates. Omit for ClickUp's defaults.
    """
    client, _team_id = await client_and_workspace(workspace_id)
    body: dict = {"name": name}
    if options:
        body["options"] = options
    return await client.post(
        f"/v2/space/{require_id(space_id, 'space_id')}"
        f"/folder_template/{require_id(template_id, 'template_id')}",
        body,
    )


@tool(phase=1)
async def update_folder(folder_id: str, name: str) -> dict:
    """Rename a Folder.

    Args:
        folder_id: ClickUp Folder id.
        name: New name.
    """
    client = await current_client()
    return await client.put(f"/v2/folder/{require_id(folder_id, 'folder_id')}", {"name": name})


@tool(phase=1, destructive=True)
async def delete_folder(folder_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a Folder and everything inside it.

    This cascades: every List and Task in the Folder is destroyed. There is no undo
    through the API. Tell the user what will be lost and get explicit agreement
    before calling with confirm=True.

    Args:
        folder_id: ClickUp Folder id.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this Folder")
    client = await current_client()
    await client.delete(f"/v2/folder/{require_id(folder_id, 'folder_id')}")
    return {"deleted": True, "folder_id": folder_id}
