"""ClickUp Chat (API v3) — channels, messages, replies, and reactions.

Posting here is visible to other people immediately. Confirm wording with the user
before sending anything on their behalf.
"""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.transform import collection, summarize_chat_message
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import as_list, require_confirm, require_id


@tool(phase=3)
async def list_chat_channels(
    workspace_id: str | None = None,
    is_follower: bool | None = None,
    include_closed: bool = False,
    channel_types: list[str] | str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict:
    """List Chat channels in a Workspace.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        is_follower: Only channels the authenticated user follows.
        include_closed: Include closed channels.
        channel_types: Filter by type, e.g. CHANNEL, DM, GROUP_DM.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    params = clean(
        {
            "is_follower": is_follower,
            "include_closed": include_closed or None,
            "channel_types": as_list(channel_types),
            "limit": limit,
            "cursor": cursor,
        }
    )
    return await client.get(f"/v3/workspaces/{team_id}/chat/channels", params=params)


@tool(phase=3)
async def get_chat_channel(channel_id: str, workspace_id: str | None = None) -> dict:
    """Get one Chat channel.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}"
    )


@tool(phase=3)
async def create_chat_channel(
    name: str,
    workspace_id: str | None = None,
    description: str | None = None,
    topic: str | None = None,
    user_ids: list[str] | str | None = None,
    visibility: str = "PRIVATE",
) -> dict:
    """Create a Chat channel.

    Args:
        name: Channel name.
        workspace_id: Omit if the user has only one Workspace.
        description: Channel description.
        topic: Channel topic.
        user_ids: ClickUp user ids to add as members.
        visibility: PUBLIC or PRIVATE. Defaults to PRIVATE.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "name": name,
            "description": description,
            "topic": topic,
            "user_ids": as_list(user_ids),
            "visibility": visibility,
        }
    )
    return await client.post(f"/v3/workspaces/{team_id}/chat/channels", body)


@tool(phase=3)
async def create_location_chat_channel(
    location_id: str,
    location_type: str,
    workspace_id: str | None = None,
    description: str | None = None,
    topic: str | None = None,
    user_ids: list[str] | str | None = None,
    visibility: str = "PRIVATE",
) -> dict:
    """Create a Chat channel attached to a Space, Folder, or List.

    Args:
        location_id: Id of the Space, Folder, or List.
        location_type: space, folder, or list.
        workspace_id: Omit if the user has only one Workspace.
        description: Channel description.
        topic: Channel topic.
        user_ids: ClickUp user ids to add as members.
        visibility: PUBLIC or PRIVATE.
    """
    if location_type not in {"space", "folder", "list"}:
        raise ValueError(
            f"location_type must be space, folder, or list, got {location_type!r}"
        )
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "location": {"id": require_id(location_id, "location_id"), "type": location_type},
            "description": description,
            "topic": topic,
            "user_ids": as_list(user_ids),
            "visibility": visibility,
        }
    )
    return await client.post(f"/v3/workspaces/{team_id}/chat/channels/location", body)


@tool(phase=3)
async def create_direct_message_channel(
    user_ids: list[str] | str, workspace_id: str | None = None
) -> dict:
    """Open a direct-message channel with one or more people.

    Args:
        user_ids: ClickUp user ids to include.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.post(
        f"/v3/workspaces/{team_id}/chat/channels/direct_message",
        {"user_ids": as_list(user_ids)},
    )


@tool(phase=3)
async def update_chat_channel(
    channel_id: str,
    workspace_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
) -> dict:
    """Update a Chat channel. Only the fields you pass are changed.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
        name: New name.
        description: New description.
        topic: New topic.
        visibility: PUBLIC or PRIVATE.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {"name": name, "description": description, "topic": topic, "visibility": visibility}
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.patch(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}", body
    )


@tool(phase=3, destructive=True)
async def delete_chat_channel(
    channel_id: str, workspace_id: str | None = None, confirm: bool = False
) -> dict:
    """PERMANENTLY delete a Chat channel and its message history.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
        confirm: Must be True. Guard against accidental deletion.
    """
    require_confirm(confirm, "delete this Chat channel and its entire message history")
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}"
    )
    return {"deleted": True, "channel_id": channel_id}


@tool(phase=3)
async def get_chat_channel_members(
    channel_id: str, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
) -> dict:
    """List the members of a Chat channel.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}/members",
        params=clean({"limit": limit, "cursor": cursor}),
    )


@tool(phase=3)
async def get_chat_channel_followers(
    channel_id: str, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
) -> dict:
    """List the followers of a Chat channel.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}/followers",
        params=clean({"limit": limit, "cursor": cursor}),
    )


@tool(phase=3)
async def get_chat_messages(
    channel_id: str,
    workspace_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    content_format: str = "text/md",
    raw: bool = False,
) -> dict:
    """List messages in a Chat channel, newest first.

    Args:
        channel_id: Chat channel id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
        content_format: text/md or text/plain.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}/messages",
        params=clean({"limit": limit, "cursor": cursor, "content_format": content_format}),
    )
    return payload if raw else collection(payload, "data", summarize_chat_message)


@tool(phase=3)
async def send_chat_message(
    channel_id: str,
    content: str,
    workspace_id: str | None = None,
    message_type: str = "message",
    assignee_id: str | None = None,
    content_format: str = "text/md",
    post_subtype_id: str | None = None,
    post_title: str | None = None,
) -> dict:
    """Post a message to a Chat channel.

    Other people see this immediately. Show the user the exact wording and get
    their agreement before sending.

    For message_type="post" you must also pass post_subtype_id — get it from
    `get_post_subtype_ids`, since the ids differ per Workspace.

    Args:
        channel_id: Chat channel id.
        content: Message body.
        workspace_id: Omit if the user has only one Workspace.
        message_type: "message" or "post".
        assignee_id: ClickUp user id to assign the message to.
        content_format: text/md or text/plain.
        post_subtype_id: Required when message_type="post".
        post_title: Title for a post.
    """
    if message_type == "post" and not post_subtype_id:
        raise ValueError(
            "message_type='post' requires post_subtype_id. Call get_post_subtype_ids "
            "first — the ids are unique to each Workspace."
        )

    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "type": message_type,
            "content": content,
            "content_format": content_format,
            "assignee": assignee_id,
            "post_data": clean({"subtype_id": post_subtype_id, "title": post_title})
            or None,
        }
    )
    return await client.post(
        f"/v3/workspaces/{team_id}/chat/channels/{require_id(channel_id, 'channel_id')}/messages",
        body,
    )


@tool(phase=3)
async def get_post_subtype_ids(comment_type: str = "post", workspace_id: str | None = None) -> dict:
    """Get the subtype ids for posts — Announcement, Discussion, Idea, Update.

    Call this before `send_chat_message(message_type="post")`: a post needs a
    subtype id, and the ids are unique to each Workspace.

    Args:
        comment_type: Comment type to list subtypes for. "post" is the usual one.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/comments/types/{comment_type}/subtypes"
    )


@tool(phase=3)
async def get_chat_message_replies(
    message_id: str,
    workspace_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    content_format: str = "text/md",
    raw: bool = False,
) -> dict:
    """List the replies to a Chat message.

    Args:
        message_id: Chat message id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
        content_format: text/md or text/plain.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}/replies",
        params=clean({"limit": limit, "cursor": cursor, "content_format": content_format}),
    )
    return payload if raw else collection(payload, "data", summarize_chat_message)


@tool(phase=3)
async def reply_to_chat_message(
    message_id: str,
    content: str,
    workspace_id: str | None = None,
    message_type: str = "message",
    content_format: str = "text/md",
) -> dict:
    """Reply to a Chat message in its thread.

    Visible to others immediately — confirm the wording with the user first.

    Args:
        message_id: Chat message to reply to.
        content: Reply body.
        workspace_id: Omit if the user has only one Workspace.
        message_type: "message" or "post".
        content_format: text/md or text/plain.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = {"type": message_type, "content": content, "content_format": content_format}
    return await client.post(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}/replies",
        body,
    )


@tool(phase=3)
async def update_chat_message(
    message_id: str,
    workspace_id: str | None = None,
    content: str | None = None,
    resolved: bool | None = None,
    assignee_id: str | None = None,
    content_format: str = "text/md",
) -> dict:
    """Edit a Chat message, or resolve it.

    Args:
        message_id: Chat message id.
        workspace_id: Omit if the user has only one Workspace.
        content: New message body.
        resolved: Mark resolved or unresolved.
        assignee_id: ClickUp user id to assign the message to.
        content_format: text/md or text/plain.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "content": content,
            "content_format": content_format if content is not None else None,
            "resolved": resolved,
            "assignee": assignee_id,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.patch(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}", body
    )


@tool(phase=3, destructive=True)
async def delete_chat_message(
    message_id: str, workspace_id: str | None = None, confirm: bool = False
) -> dict:
    """PERMANENTLY delete a Chat message.

    Args:
        message_id: Chat message id.
        workspace_id: Omit if the user has only one Workspace.
        confirm: Must be True.
    """
    require_confirm(confirm, "delete this Chat message")
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}"
    )
    return {"deleted": True, "message_id": message_id}


@tool(phase=3)
async def get_chat_message_reactions(
    message_id: str, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
) -> dict:
    """List the reactions on a Chat message.

    Args:
        message_id: Chat message id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}/reactions",
        params=clean({"limit": limit, "cursor": cursor}),
    )


@tool(phase=3)
async def add_chat_reaction(
    message_id: str, reaction: str, workspace_id: str | None = None
) -> dict:
    """React to a Chat message as the authenticated user.

    Args:
        message_id: Chat message id.
        reaction: Emoji name, e.g. "thumbsup".
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.post(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}/reactions",
        {"reaction": reaction},
    )


@tool(phase=3)
async def remove_chat_reaction(
    message_id: str, reaction: str, workspace_id: str | None = None
) -> dict:
    """Remove the authenticated user's reaction from a Chat message.

    Args:
        message_id: Chat message id.
        reaction: Emoji name to remove.
        workspace_id: Omit if the user has only one Workspace.
    """
    client, team_id = await client_and_workspace(workspace_id)
    await client.delete(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}"
        f"/reactions/{reaction}"
    )
    return {"removed": True, "message_id": message_id, "reaction": reaction}


@tool(phase=3)
async def get_chat_message_tagged_users(
    message_id: str, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
) -> dict:
    """List the users @-mentioned in a Chat message.

    Args:
        message_id: Chat message id.
        workspace_id: Omit if the user has only one Workspace.
        limit: Results per page.
        cursor: Cursor from a previous response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/chat/messages/{require_id(message_id, 'message_id')}"
        "/tagged_users",
        params=clean({"limit": limit, "cursor": cursor}),
    )
