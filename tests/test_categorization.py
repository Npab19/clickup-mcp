"""Tool categorization invariants.

The phase/destructive/admin flags are what `policy.py` gates on, so a wrong flag
either hides a tool people need or exposes one they shouldn't have. These pin the
rules that are easy to get wrong by hand across 150 tools.
"""
from __future__ import annotations

import pytest

from clickup_mcp import server  # noqa: F401  (registers every tool)
from clickup_mcp.app import mcp
from clickup_mcp.constants import PROFILE_PHASES
from clickup_mcp.policy import TOOL_META


def _module(name: str) -> str:
    return mcp._tool_manager._tools[name].fn.__module__.split(".")[-1]


def _signature(name: str):
    import inspect

    return inspect.signature(mcp._tool_manager._tools[name].fn)


CORE_PHASES = PROFILE_PHASES["core"]

# A tool is destructive when it irreversibly loses user-authored content or
# cascades a delete. Re-addable relationships are NOT destructive — see policy.py.
EXPECTED_DESTRUCTIVE = {
    "delete_space", "delete_folder", "delete_list", "delete_task", "delete_comment",
    "delete_checklist", "delete_checklist_item", "delete_space_tag",
    "delete_time_entry", "delete_task_tracked_time",
    "delete_chat_channel", "delete_chat_message",
    "delete_view", "delete_goal", "delete_key_result", "delete_webhook",
    "delete_user_group", "remove_user_from_workspace", "remove_guest_from_workspace",
    "merge_tasks",
}

# Reversible removals that must stay available without CLICKUP_ENABLE_DESTRUCTIVE.
REVERSIBLE_REMOVALS = {
    "remove_task_dependency", "remove_task_link", "remove_time_entry_tags",
    "remove_guest_from_item", "remove_task_from_list", "remove_tag_from_task",
    "remove_chat_reaction", "remove_custom_field_value",
}


def test_destructive_set_matches_the_stated_rule():
    actual = {n for n, m in TOOL_META.items() if m.destructive}
    assert actual == EXPECTED_DESTRUCTIVE, (
        f"unexpectedly destructive: {sorted(actual - EXPECTED_DESTRUCTIVE)}; "
        f"no longer destructive: {sorted(EXPECTED_DESTRUCTIVE - actual)}"
    )


@pytest.mark.parametrize("name", sorted(REVERSIBLE_REMOVALS))
def test_reversible_removals_are_not_gated_as_destructive(name):
    assert name in TOOL_META, f"{name} is not registered"
    assert not TOOL_META[name].destructive, (
        f"{name} is reversible; gating it behind CLICKUP_ENABLE_DESTRUCTIVE hides "
        "an ordinary workflow tool for no safety gain"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_DESTRUCTIVE))
def test_every_destructive_tool_demands_confirmation(name):
    """The flag controls visibility; `confirm` stops an accidental call once visible."""
    params = _signature(name).parameters
    assert "confirm" in params, f"{name} is destructive but takes no confirm parameter"
    assert params["confirm"].default is False, f"{name} must default confirm to False"


def test_no_non_destructive_tool_asks_for_confirmation():
    """A confirm flag on a harmless tool trains the model to pass confirm=True."""
    offenders = [
        name
        for name, meta in TOOL_META.items()
        if not meta.destructive and "confirm" in _signature(name).parameters
    ]
    assert not offenders, f"non-destructive tools with a confirm parameter: {offenders}"


def test_template_listing_is_reachable_wherever_template_creation_is():
    """`create_*_from_template` needs an id that only `list_*_templates` provides."""
    pairs = [
        ("create_task_from_template", "list_task_templates"),
        ("create_list_from_template", "list_list_templates"),
        ("create_folder_from_template", "list_folder_templates"),
    ]
    for creator, lister in pairs:
        assert TOOL_META[lister].phase <= TOOL_META[creator].phase, (
            f"{creator} is phase {TOOL_META[creator].phase} but {lister} is phase "
            f"{TOOL_META[lister].phase} — you could create from a template without "
            "being able to discover one"
        )


@pytest.mark.parametrize(
    "name",
    [
        "search_tasks", "get_task", "create_task", "update_task", "move_task_to_list",
        "list_workspaces", "whoami", "list_lists", "list_spaces",
        "start_timer", "stop_timer", "get_running_timer", "list_time_entries",
        "get_view_tasks",
    ],
)
def test_everyday_tools_are_in_the_default_profile(name):
    assert TOOL_META[name].phase in CORE_PHASES, (
        f"{name} is phase {TOOL_META[name].phase}, invisible under the default "
        "'core' profile despite being everyday work"
    )


def test_admin_tools_live_only_in_the_admin_phase():
    misplaced = {
        n: m.phase for n, m in TOOL_META.items() if m.admin and m.phase in CORE_PHASES
    }
    assert not misplaced, f"admin tools inside the default profile: {misplaced}"


def test_v3_task_tools_are_filed_by_domain_not_api_version():
    """Phase is about what a tool is for, not which API version happens to serve it."""
    assert TOOL_META["move_task_to_list"].phase == 1, "moving a task is core task work"
    assert TOOL_META["update_time_estimates_by_user"].phase == 2, "that is time tracking"


def test_every_tool_declares_metadata():
    undeclared = sorted(set(mcp._tool_manager._tools) - set(TOOL_META))
    assert not undeclared, (
        f"tools registered without @tool(phase=...) metadata: {undeclared}"
    )


def test_phases_are_within_the_known_range():
    bad = {n: m.phase for n, m in TOOL_META.items() if m.phase not in {1, 2, 3, 4}}
    assert not bad, f"tools with an unknown phase: {bad}"
