"""Comments on Tasks, Lists, and Chat views, plus threaded replies."""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.context import current_client
from clickup_mcp.transform import collection, summarize_comment
from clickup_mcp.tools._common import clean
from clickup_mcp.validation import optional_id, require_confirm, require_id

_PARENTS = {
    "task": "/v2/task/{id}/comment",
    "list": "/v2/list/{id}/comment",
    "view": "/v2/view/{id}/comment",
}


def _comment_path(parent_type: str, parent_id: str) -> str:
    if parent_type not in _PARENTS:
        raise ValueError(
            f"parent_type must be one of {', '.join(_PARENTS)}, got {parent_type!r}"
        )
    return _PARENTS[parent_type].format(id=require_id(parent_id, "parent_id"))


@tool(phase=1)
async def list_comments(
    parent_type: str,
    parent_id: str,
    start: str | None = None,
    start_id: str | None = None,
    raw: bool = False,
) -> dict:
    """List comments on a Task, List, or Chat view.

    Returns the 25 most recent. To page further back, pass the `date` and `id` of
    the oldest comment you received as start and start_id.

    Args:
        parent_type: task, list, or view.
        parent_id: Id of the Task, List, or view.
        start: Unix ms timestamp of the oldest comment already seen.
        start_id: Comment id matching `start`.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(
        _comment_path(parent_type, parent_id),
        params={"start": start, "start_id": start_id},
    )
    return payload if raw else collection(payload, "comments", summarize_comment)


@tool(phase=1)
async def create_comment(
    parent_type: str,
    parent_id: str,
    comment_text: str,
    assignee_id: str | None = None,
    notify_all: bool = False,
) -> dict:
    """Add a comment to a Task, List, or Chat view.

    This notifies people in ClickUp. Confirm the wording with the user before
    posting on their behalf.

    Args:
        parent_type: task, list, or view.
        parent_id: Id of the Task, List, or view.
        comment_text: The comment body.
        assignee_id: ClickUp user id to assign the comment to.
        notify_all: Notify everyone watching the parent.
    """
    client = await current_client()
    body = clean(
        {
            "comment_text": comment_text,
            "assignee": optional_id(assignee_id, "assignee_id"),
            "notify_all": notify_all or None,
        }
    )
    return await client.post(_comment_path(parent_type, parent_id), body)


@tool(phase=1)
async def list_comment_replies(comment_id: str, raw: bool = False) -> dict:
    """List the threaded replies under a comment.

    Args:
        comment_id: ClickUp comment id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/comment/{require_id(comment_id, 'comment_id')}/reply")
    return payload if raw else collection(payload, "comments", summarize_comment)


@tool(phase=1)
async def create_comment_reply(
    comment_id: str,
    comment_text: str,
    assignee_id: str | None = None,
    notify_all: bool = False,
) -> dict:
    """Reply in a comment thread.

    Args:
        comment_id: Comment to reply to.
        comment_text: The reply body.
        assignee_id: ClickUp user id to assign the reply to.
        notify_all: Notify everyone watching the parent.
    """
    client = await current_client()
    body = clean(
        {
            "comment_text": comment_text,
            "assignee": optional_id(assignee_id, "assignee_id"),
            "notify_all": notify_all or None,
        }
    )
    return await client.post(f"/v2/comment/{require_id(comment_id, 'comment_id')}/reply", body)


@tool(phase=1)
async def update_comment(
    comment_id: str,
    comment_text: str | None = None,
    assignee_id: str | None = None,
    resolved: bool | None = None,
) -> dict:
    """Update a comment's text, assignee, or resolved state.

    Args:
        comment_id: ClickUp comment id.
        comment_text: New comment body.
        assignee_id: ClickUp user id to assign the comment to.
        resolved: Mark resolved or unresolved.
    """
    client = await current_client()
    body = clean(
        {
            "comment_text": comment_text,
            "assignee": optional_id(assignee_id, "assignee_id"),
            "resolved": resolved,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(f"/v2/comment/{require_id(comment_id, 'comment_id')}", body)


@tool(phase=1, destructive=True)
async def delete_comment(comment_id: str, confirm: bool = False) -> dict:
    """PERMANENTLY delete a comment and its replies.

    Args:
        comment_id: ClickUp comment id.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this comment")
    client = await current_client()
    await client.delete(f"/v2/comment/{require_id(comment_id, 'comment_id')}")
    return {"deleted": True, "comment_id": comment_id}
