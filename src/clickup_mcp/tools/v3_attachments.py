"""Attachments and access control on arbitrary entities (API v3).

The v2 attachment endpoint only handles Tasks; these work against any entity type,
and the ACL endpoint is the only way to change sharing through the API.
"""
from __future__ import annotations

import base64
import binascii

from clickup_mcp.app import tool
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.tools.attachments import MAX_ATTACHMENT_BYTES
from clickup_mcp.validation import require_id

_ENTITY_TYPES = {"task", "doc", "chat_message", "list", "folder", "space"}


def _check_entity(entity_type: str) -> str:
    if entity_type not in _ENTITY_TYPES:
        raise ValueError(
            f"entity_type must be one of {', '.join(sorted(_ENTITY_TYPES))}, "
            f"got {entity_type!r}"
        )
    return entity_type


@tool(phase=3)
async def list_entity_attachments(
    entity_type: str,
    entity_id: str,
    workspace_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict:
    """List the attachments on an entity.

    Args:
        entity_type: task, doc, chat_message, list, folder, or space.
        entity_id: Id of that entity.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/{_check_entity(entity_type)}"
        f"/{require_id(entity_id, 'entity_id')}/attachments",
        params=clean({"limit": limit, "cursor": cursor}),
    )


@tool(phase=3)
async def upload_entity_attachment(
    entity_type: str,
    entity_id: str,
    filename: str,
    workspace_id: str | None = None,
    text_content: str | None = None,
    content_base64: str | None = None,
    content_type: str = "application/octet-stream",
) -> dict:
    """Attach a file to any entity, not just a Task.

    Pass exactly one of text_content or content_base64. Maximum 10 MB.

    Args:
        entity_type: task, doc, chat_message, list, folder, or space.
        entity_id: Id of that entity.
        filename: Name to store the file under, including its extension.
        workspace_id: Omit if the user has only one Workspace.
        text_content: File contents as plain text.
        content_base64: File contents as base64.
        content_type: MIME type, e.g. "text/csv" or "image/png".
    """
    if bool(text_content) == bool(content_base64):
        raise ValueError("Pass exactly one of text_content or content_base64.")

    if text_content is not None:
        payload = text_content.encode("utf-8")
    else:
        try:
            payload = base64.b64decode(content_base64 or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 is not valid base64.") from exc

    if not payload:
        raise ValueError("The attachment is empty.")
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment is {len(payload) // 1024} KB; the limit here is "
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024} MB."
        )

    client, team_id = await client_and_workspace(workspace_id)
    result = await client.post_multipart(
        f"/v3/workspaces/{team_id}/{_check_entity(entity_type)}"
        f"/{require_id(entity_id, 'entity_id')}/attachments",
        files={"attachment": (filename, payload, content_type)},
        data={"filename": filename},
    )
    return {
        "uploaded": True,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "filename": filename,
        "bytes": len(payload),
        "attachment": result,
    }


@tool(phase=3, admin=True)
async def update_object_acl(
    object_type: str,
    object_id: str,
    acls: list[dict],
    workspace_id: str | None = None,
) -> dict:
    """Change who can access an object.

    This alters permissions on real content — it can expose a private Doc or Space,
    or cut someone off from their own work. State exactly what will change and get
    the user's agreement first.

    Args:
        object_type: Object type, e.g. doc, list, folder, space.
        object_id: Id of that object.
        acls: Access control entries to apply, in ClickUp's ACL shape.
        workspace_id: Omit if the user has only one Workspace.
    """
    if not acls:
        raise ValueError("acls must contain at least one entry.")
    client, team_id = await client_and_workspace(workspace_id)
    return await client.patch(
        f"/v3/workspaces/{team_id}/{object_type}/{require_id(object_id, 'object_id')}/acls",
        {"acls": acls},
    )
