"""MCP tool annotations — how a client categorizes and presents these tools.

The phase/destructive/admin metadata in `policy` never leaves the server. Clients
group by the standard `ToolAnnotations` instead, and with none set they file every
tool under "Other" — which is what happened until these were added.

`readOnlyHint` and `idempotentHint` are derived from the HTTP verbs each function
actually issues, so they cannot drift from the implementation. These tests check
the derivation itself, since a wrong `readOnlyHint` on a mutating tool is a safety
claim a client may act on.
"""
from __future__ import annotations

import pytest

from clickup_mcp import server  # noqa: F401  (registers every tool)
from clickup_mcp.app import _client_verbs, _title_from
from clickup_mcp.app import mcp
from clickup_mcp.policy import TOOL_META

TOOLS = {t.name: t for t in mcp._tool_manager.list_tools()}


def test_every_tool_is_annotated_and_titled():
    missing = [n for n, t in TOOLS.items() if not t.annotations]
    assert not missing, f"tools without annotations: {missing}"
    untitled = [n for n, t in TOOLS.items() if not t.title]
    assert not untitled, f"tools without a title: {untitled}"


def test_every_tool_carries_a_grouping_domain():
    ungrouped = [
        n for n, t in TOOLS.items() if (t.meta or {}).get("clickup/domain") in (None, "Other")
    ]
    assert not ungrouped, f"tools with no domain (they land in 'Other'): {ungrouped}"


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_read_only_hint_matches_what_the_tool_actually_does(name):
    """A tool claiming readOnlyHint must issue no mutating request."""
    tool = TOOLS[name]
    fn = mcp._tool_manager._tools[name].fn
    verbs = _client_verbs(fn)
    mutating = verbs & {"post", "put", "patch", "delete", "post_multipart"}

    if tool.annotations.readOnlyHint and mutating:
        # Only an explicit override may claim this, and only when the endpoint
        # genuinely does not modify anything (a POST used as a query).
        assert name == "query_audit_logs", (
            f"{name} claims readOnlyHint but issues {sorted(mutating)}"
        )


def test_destructive_hint_mirrors_the_gating_flag():
    """The client-facing warning and the server-side gate must agree."""
    for name, meta in TOOL_META.items():
        assert TOOLS[name].annotations.destructiveHint == meta.destructive, (
            f"{name}: destructiveHint={TOOLS[name].annotations.destructiveHint} "
            f"but policy has destructive={meta.destructive}"
        )


def test_creates_are_not_advertised_as_idempotent():
    """Calling create_task twice makes two tasks; a client must not assume retry-safe."""
    for name in ("create_task", "create_list", "create_folder", "create_space",
                 "create_comment", "upload_task_attachment", "create_time_entry"):
        assert TOOLS[name].annotations.idempotentHint is False, (
            f"{name} is not idempotent — repeating it creates another object"
        )


def test_updates_and_deletes_are_idempotent():
    for name in ("update_task", "update_list", "delete_task", "delete_space"):
        assert TOOLS[name].annotations.idempotentHint is True


def test_a_tool_that_makes_no_request_is_read_only_and_idempotent():
    """`whoami` answers from the cached grant. An empty verb set must not be
    mistaken for 'unknown, assume unsafe'."""
    assert _client_verbs(mcp._tool_manager._tools["whoami"].fn) == set()
    a = TOOLS["whoami"].annotations
    assert a.readOnlyHint is True and a.idempotentHint is True


def test_everything_is_open_world():
    """Every tool talks to ClickUp, an external system."""
    assert all(t.annotations.openWorldHint is True for t in TOOLS.values())


def test_read_only_tools_are_a_meaningful_share():
    ro = [n for n, t in TOOLS.items() if t.annotations.readOnlyHint]
    assert 40 <= len(ro) <= 90, f"suspicious read-only count: {len(ro)}"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("search_tasks", "Search Tasks"),
        ("get_doc_page_listing", "Get Doc Page Listing"),
        ("add_task_to_list", "Add Task to List"),
        ("remove_guest_from_workspace", "Remove Guest from Workspace"),
    ],
)
def test_titles_read_naturally(name, expected):
    assert _title_from(name) == expected


def test_verb_extraction_sees_through_multiline_calls():
    """Several tools build paths across concatenated f-strings; the AST walk must
    still find the verb."""
    fn = mcp._tool_manager._tools["delete_checklist_item"].fn
    assert "delete" in _client_verbs(fn)
