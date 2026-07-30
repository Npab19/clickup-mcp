"""Task attachments — the only multipart endpoint in the API.

An MCP tool call carries JSON, not binary, so content arrives either as text or as
base64 and is decoded here before being posted as a file part.
"""
from __future__ import annotations

import base64
import binascii

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.validation import require_id

# Guard against a model pasting something enormous into an argument.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@tool(phase=2)
async def upload_task_attachment(
    task_id: str,
    filename: str,
    text_content: str | None = None,
    content_base64: str | None = None,
    content_type: str = "application/octet-stream",
) -> dict:
    """Attach a file to a Task.

    Pass exactly one of text_content (for notes, logs, CSV, markdown) or
    content_base64 (for binary). Maximum 10 MB.

    ClickUp has no API to delete an attachment — this cannot be undone from here.

    Args:
        task_id: ClickUp Task id.
        filename: Name to store the file under, including its extension.
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

    client = await current_client()
    result = await client.post_multipart(
        f"/v2/task/{require_id(task_id, 'task_id')}/attachment",
        files={"attachment": (filename, payload, content_type)},
        data={"filename": filename},
    )
    return {
        "uploaded": True,
        "task_id": task_id,
        "filename": filename,
        "bytes": len(payload),
        "attachment": result,
    }
