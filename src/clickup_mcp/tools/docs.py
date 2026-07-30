"""ClickUp Docs (API v3).

Docs live only in v3, on a different path prefix from everything in Phases 1-2.
A Doc is a container of pages; the content lives on the pages.

## Markdown/plain text is LOSSY — read this before editing a page

Per ClickUp's own import/export limitations, reading a page as `text/md` or
`text/plain` and writing it back **silently destroys** everything the format
cannot carry. Nothing errors; the content is just gone.

Not representable at all — dropped on write:
  toggle lists, checklists, banners, alignment, underline, indent, text and
  background colours, badges, inline elements, views, synced content, columns,
  tables of contents, slides, covers, icons, page comments, wiki settings, and
  every embed (YouTube, Vimeo, Loom, Miro, Giphy, Google Drive/Docs/Sheets/Slides,
  task embeds, Doc embeds, Whiteboard embeds, org charts).

Survives but degraded:
  code blocks lose their formatting, tables lose formatting, buttons become plain
  links, attachments lose their sizing.

The practical rule: prefer `content_edit_mode="append"` or `"prepend"`, which do
not rewrite existing content. Use `"replace"` only when the user has asked to
rewrite the page and you have told them what may be lost.
"""
from __future__ import annotations

from clickup_mcp.app import tool
from clickup_mcp.transform import collection, summarize_doc
from clickup_mcp.tools._common import clean, client_and_workspace
from clickup_mcp.validation import require_id

_CONTENT_FORMATS = {"text/md", "text/plain", "text/html"}


def _check_format(content_format: str) -> str:
    if content_format not in _CONTENT_FORMATS:
        raise ValueError(
            f"content_format must be one of {', '.join(sorted(_CONTENT_FORMATS))}, "
            f"got {content_format!r}"
        )
    return content_format


@tool(phase=3)
async def search_docs(
    workspace_id: str | None = None,
    doc_id: str | None = None,
    creator_id: int | None = None,
    parent_id: str | None = None,
    parent_type: str | None = None,
    include_archived: bool = False,
    include_deleted: bool = False,
    limit: int = 25,
    next_cursor: str | None = None,
    raw: bool = False,
) -> dict:
    """Search the Docs in a Workspace.

    Returns Doc metadata only — call `get_doc_pages` for the actual content.

    Args:
        workspace_id: Omit if the user has only one Workspace.
        doc_id: Look up one specific Doc id.
        creator_id: Only Docs created by this ClickUp user id.
        parent_id: Only Docs under this parent object.
        parent_type: Parent object type, e.g. SPACE, FOLDER, LIST, TASK.
        include_archived: Include archived Docs.
        include_deleted: Include deleted Docs.
        limit: Results per page (default 25).
        next_cursor: Cursor from a previous response, to get the next page.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    params = clean(
        {
            "id": doc_id,
            "creator": creator_id,
            "parent_id": parent_id,
            "parent_type": parent_type,
            "archived": include_archived or None,
            "deleted": include_deleted or None,
            "limit": limit,
            "next_cursor": next_cursor,
        }
    )
    payload = await client.get(f"/v3/workspaces/{team_id}/docs", params=params)
    return payload if raw else collection(payload, "docs", summarize_doc)


@tool(phase=3)
async def get_doc(doc_id: str, workspace_id: str | None = None, raw: bool = False) -> dict:
    """Get a Doc's metadata.

    This does not include page content — use `get_doc_pages`.

    Args:
        doc_id: ClickUp Doc id.
        workspace_id: Omit if the user has only one Workspace.
        raw: Return ClickUp's full response.
    """
    client, team_id = await client_and_workspace(workspace_id)
    payload = await client.get(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}"
    )
    return payload if raw else summarize_doc(payload)


@tool(phase=3)
async def create_doc(
    name: str,
    workspace_id: str | None = None,
    parent_id: str | None = None,
    parent_type: str | None = None,
    visibility: str = "PRIVATE",
    create_page: bool = True,
) -> dict:
    """Create a Doc.

    Args:
        name: Doc name.
        workspace_id: Omit if the user has only one Workspace.
        parent_id: Id of the object to create the Doc under.
        parent_type: Parent type — SPACE, FOLDER, LIST, EVERYTHING, or WORKSPACE.
            Required when parent_id is given.
        visibility: PRIVATE or PUBLIC. Defaults to PRIVATE — say so if the user
            expected the Doc to be shared.
        create_page: Also create an empty first page.
    """
    if bool(parent_id) != bool(parent_type):
        raise ValueError("Pass parent_id and parent_type together, or neither.")

    client, team_id = await client_and_workspace(workspace_id)
    body: dict = {"name": name, "visibility": visibility, "create_page": create_page}
    if parent_id:
        body["parent"] = {"id": parent_id, "type": parent_type}
    return await client.post(f"/v3/workspaces/{team_id}/docs", body)


@tool(phase=3)
async def get_doc_page_listing(
    doc_id: str, workspace_id: str | None = None, max_page_depth: int = -1
) -> dict:
    """Get a Doc's page tree, without any page content.

    Cheap way to see a Doc's structure before pulling content for one page.

    Args:
        doc_id: ClickUp Doc id.
        workspace_id: Omit if the user has only one Workspace.
        max_page_depth: How deep to descend. -1 means unlimited.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}/page_listing",
        params={"max_page_depth": max_page_depth},
    )


@tool(phase=3)
async def get_doc_pages(
    doc_id: str,
    workspace_id: str | None = None,
    max_page_depth: int = -1,
    content_format: str = "text/md",
) -> dict:
    """Get every page of a Doc, with content.

    This can be very large. If you only need the structure, or one page, use
    `get_doc_page_listing` then `get_doc_page`.

    What you get back is not the whole page: markdown and plain text cannot carry
    embeds, toggle lists, checklists, banners, colours, columns, or synced content,
    so do not conclude a page lacks something merely because it is absent here.

    Args:
        doc_id: ClickUp Doc id.
        workspace_id: Omit if the user has only one Workspace.
        max_page_depth: How deep to descend. -1 means unlimited.
        content_format: text/md, text/plain, or text/html.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}/pages",
        params={
            "max_page_depth": max_page_depth,
            "content_format": _check_format(content_format),
        },
    )


@tool(phase=3)
async def get_doc_page(
    doc_id: str,
    page_id: str,
    workspace_id: str | None = None,
    content_format: str = "text/md",
) -> dict:
    """Get one page of a Doc, with content.

    Args:
        doc_id: ClickUp Doc id.
        page_id: Page id from `get_doc_page_listing`.
        workspace_id: Omit if the user has only one Workspace.
        content_format: text/md, text/plain, or text/html.
    """
    client, team_id = await client_and_workspace(workspace_id)
    return await client.get(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}"
        f"/pages/{require_id(page_id, 'page_id')}",
        params={"content_format": _check_format(content_format)},
    )


@tool(phase=3)
async def create_doc_page(
    doc_id: str,
    name: str,
    workspace_id: str | None = None,
    content: str | None = None,
    sub_title: str | None = None,
    parent_page_id: str | None = None,
    content_format: str = "text/md",
) -> dict:
    """Add a page to a Doc.

    Args:
        doc_id: ClickUp Doc id.
        name: Page title.
        workspace_id: Omit if the user has only one Workspace.
        content: Page body.
        sub_title: Page subtitle.
        parent_page_id: Nest this page under another page. Omit for a root page.
        content_format: text/md, text/plain, or text/html.
    """
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "name": name,
            "content": content,
            "sub_title": sub_title,
            "parent_page_id": parent_page_id,
            "content_format": _check_format(content_format),
        }
    )
    return await client.post(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}/pages", body
    )


@tool(phase=3)
async def edit_doc_page(
    doc_id: str,
    page_id: str,
    workspace_id: str | None = None,
    name: str | None = None,
    content: str | None = None,
    sub_title: str | None = None,
    content_edit_mode: str = "replace",
    content_format: str = "text/md",
) -> dict:
    """Edit a Doc page.

    WARNING — "replace" is lossy and silent. It overwrites the whole page with
    markdown/plain text, and ClickUp cannot round-trip rich content through those
    formats: toggle lists, checklists, banners, colours, embeds, columns, synced
    blocks and more are DISCARDED, while code blocks and tables lose their
    formatting. Nothing errors.

    Prefer "append" or "prepend", which leave existing content alone. Before using
    "replace" on a page you did not create, read it with `get_doc_page` and tell
    the user what formatting is at risk.

    Args:
        doc_id: ClickUp Doc id.
        page_id: Page id.
        workspace_id: Omit if the user has only one Workspace.
        name: New page title.
        content: New or additional content.
        sub_title: New subtitle.
        content_edit_mode: append or prepend (safe), or replace (overwrites the
            page and drops rich formatting).
        content_format: text/md, text/plain, or text/html. text/html preserves
            more structure than markdown if you must replace.
    """
    if content_edit_mode not in {"replace", "append", "prepend"}:
        raise ValueError(
            f"content_edit_mode must be replace, append, or prepend, got {content_edit_mode!r}"
        )
    client, team_id = await client_and_workspace(workspace_id)
    body = clean(
        {
            "name": name,
            "content": content,
            "sub_title": sub_title,
            "content_edit_mode": content_edit_mode if content is not None else None,
            "content_format": _check_format(content_format) if content is not None else None,
        }
    )
    if not body:
        raise ValueError("Pass at least one field to change.")
    return await client.put(
        f"/v3/workspaces/{team_id}/docs/{require_id(doc_id, 'doc_id')}"
        f"/pages/{require_id(page_id, 'page_id')}",
        body,
    )
