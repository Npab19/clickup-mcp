"""Shared application instances — imported by server.py and every tool module."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from clickup_mcp.constants import MCP_SCOPE
from clickup_mcp.oauth_provider import ClickUpOAuthProvider
from clickup_mcp.policy import GovernedFastMCP, ToolMeta, register_meta
from clickup_mcp.runtime import (
    CLICKUP_CLIENT_ID,
    CLICKUP_CLIENT_SECRET,
    SERVER_URL,
    store,
)

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = """\
You are connected to ClickUp on behalf of one specific user. Every call uses that
user's own OAuth token, so you can only see and change what they can.

## Hierarchy (top → bottom)
Workspace (called "team" in the API) → Space → Folder → List → Task

Folders are optional: a List can hang directly off a Space ("folderless list").
All IDs are opaque strings — never construct, guess, or increment one.

## Finding things — read this before walking the tree
1. `search_tasks` is almost always the right first call. It queries a whole
   Workspace with filters (assignee, status, list, tag, due date, text) and beats
   listing Spaces → Folders → Lists → Tasks, which costs many calls and huge output.
2. `list_workspaces` first if you do not know the workspace_id. Users with exactly
   one Workspace get it filled in automatically — omit the argument.
3. Only walk the hierarchy when the user asks about structure itself
   ("what Lists are in this Space?").

## Output size
List results are summarized by default: id, name, status, assignees, dates, URL.
Pass `raw=True` on any tool to get ClickUp's full response — do this only when you
genuinely need a field the summary omits, because full task objects are enormous.

## Pagination
Task queries are paged (`page`, 0-indexed). A response with `last_page: false` has
more. Do not fetch every page by reflex — ask the user if the first page is enough.

## Custom fields
Custom field values are set through `set_custom_field_value` with the field's id,
not through `update_task`. Call `list_accessible_custom_fields` to discover ids and
their expected value shapes.

## Time tracking
`start_timer` / `stop_timer` act on the authenticated user's own timer. Only one
timer runs at a time; starting a new one stops the previous.

## Destructive operations
Deletes CASCADE in ClickUp — deleting a Space removes its Folders, Lists and Tasks;
deleting a Folder removes its Lists and Tasks. These tools may be disabled on this
server. When they are available they require `confirm=True`, and you should state
plainly what will be destroyed and get the user's agreement before calling.

## Errors
- "not connected" / "reconnect" → the user's ClickUp authorization is missing or was
  revoked. Tell them to reconnect this server. Do NOT retry.
- 429 → rate limited (ClickUp allows 100 requests/minute per user on most plans).
  Stop and report; do not loop.
- "Do not retry" in any error message means exactly that — report it to the user.
"""


provider = ClickUpOAuthProvider(
    server_url=SERVER_URL,
    store=store,
    client_id=CLICKUP_CLIENT_ID,
    client_secret=CLICKUP_CLIENT_SECRET,
)

auth_settings = AuthSettings(
    issuer_url=AnyHttpUrl(SERVER_URL),
    client_registration_options=ClientRegistrationOptions(
        enabled=True,
        valid_scopes=[MCP_SCOPE],
        default_scopes=[MCP_SCOPE],
    ),
    required_scopes=[MCP_SCOPE],
    resource_server_url=None,
)

# Startup/shutdown is handled by GovernedFastMCP.streamable_http_app(), not by a
# `lifespan=` argument here — see the docstring there for why that argument does
# not do what its name suggests.
mcp = GovernedFastMCP(
    name="ClickUp MCP Server",
    instructions=SERVER_INSTRUCTIONS,
    auth_server_provider=provider,
    auth=auth_settings,
    host="0.0.0.0",
    port=8000,
)


_MUTATING_VERBS = {"post", "put", "patch", "delete", "post_multipart"}
_IDEMPOTENT_VERBS = {"get", "put", "patch", "delete"}

# Phase 3 covers two unrelated domains; everything else maps one-to-one.
_DOMAIN_BY_MODULE = {
    "auth": "Account",
    "workspaces": "Workspace",
    "spaces": "Hierarchy",
    "folders": "Hierarchy",
    "lists": "Hierarchy",
    "tasks": "Tasks",
    "v3_tasks": "Tasks",
    "comments": "Comments",
    "custom_fields": "Custom Fields",
    "time_tracking": "Time Tracking",
    "tags": "Tags",
    "checklists": "Checklists",
    "task_relationships": "Task Relationships",
    "members": "Members",
    "attachments": "Attachments",
    "v3_attachments": "Attachments",
    "docs": "Docs",
    "chat": "Chat",
    "views": "Views",
    "goals": "Goals",
    "templates": "Templates",
    "webhooks": "Admin",
    "users": "Admin",
    "guests": "Admin",
    "groups": "Admin",
    "audit_logs": "Admin",
}


def _client_verbs(fn: Callable[..., Any]) -> set[str]:
    """Which HTTP verbs a tool actually issues, read from its own source.

    Declaring read-only by hand across 150 tools would drift; deriving it from the
    calls the function makes cannot.
    """
    import ast
    import inspect
    import textwrap

    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, SyntaxError):  # pragma: no cover - source always available here
        return set()
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATING_VERBS | {"get"}
    }


def _title_from(name: str) -> str:
    """search_tasks -> Search Tasks. Gives clients something human to show."""
    small = {"to", "from", "by", "in", "a", "the", "of"}
    words = [w if w in small else w.capitalize() for w in name.split("_")]
    words[0] = words[0].capitalize()
    return " ".join(words)


def tool(
    *,
    phase: int,
    destructive: bool = False,
    admin: bool = False,
    read_only: bool | None = None,
    idempotent: bool | None = None,
    title: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool with both the internal gating metadata and MCP annotations.

    Two audiences, and both matter:

    * `policy` gates on phase/destructive/admin — server-side, never leaves here.
    * MCP clients group and present tools using the standard `ToolAnnotations`
      (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`,
      `title`). Without them a client has nothing to categorize on and files every
      tool under "Other", which is exactly what happened before this existed.

    `read_only` and `idempotent` are derived from the HTTP verbs the function
    actually issues, so they cannot drift from the implementation. Pass them
    explicitly only where the verb misleads — `query_audit_logs` is a POST that
    modifies nothing.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        verbs = _client_verbs(fn)
        # An empty verb set means the tool issues no request at all (`whoami` answers
        # from the cached grant). That is maximally read-only and idempotent, so the
        # subset tests below must be written to hold for the empty set.
        resolved_read_only = (
            read_only if read_only is not None else not (verbs & _MUTATING_VERBS)
        )
        resolved_idempotent = (
            idempotent if idempotent is not None else verbs <= _IDEMPOTENT_VERBS
        )

        register_meta(fn.__name__, ToolMeta(phase=phase, destructive=destructive, admin=admin))

        annotations = ToolAnnotations(
            title=title or _title_from(fn.__name__),
            readOnlyHint=resolved_read_only,
            destructiveHint=destructive,
            idempotentHint=resolved_idempotent,
            # Every tool reaches out to ClickUp, an external system.
            openWorldHint=True,
        )
        domain = _DOMAIN_BY_MODULE.get(fn.__module__.split(".")[-1], "Other")

        return mcp.tool(
            title=annotations.title,
            annotations=annotations,
            meta={
                "clickup/domain": domain,
                "clickup/phase": phase,
                "clickup/admin_only": admin,
            },
        )(fn)

    return decorator
