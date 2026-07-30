"""Tool modules. Importing this package registers every tool with the server.

Whether a registered tool is actually advertised to a given caller is decided at
request time by `clickup_mcp.policy` — see the phase/destructive/admin metadata on
each `@tool(...)` declaration.
"""

from clickup_mcp.tools import (  # noqa: F401
    # Phase 1 — core hierarchy and tasks
    auth,
    comments,
    custom_fields,
    folders,
    lists,
    spaces,
    tasks,
    workspaces,
    # Phase 2 — time tracking and collaboration
    attachments,
    checklists,
    members,
    tags,
    task_relationships,
    time_tracking,
    # Phase 3 — API v3: Docs, Chat, and v3-only task operations
    chat,
    docs,
    v3_attachments,
    v3_tasks,
    # Phase 4 — admin surface
    audit_logs,
    goals,
    groups,
    guests,
    templates,
    users,
    views,
    webhooks,
)
