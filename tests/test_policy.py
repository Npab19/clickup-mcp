"""Gating rules. With destructive tools in the build, these are safety tests.

`policy` binds its config at import time, so the tests patch the module globals
rather than the environment.
"""
from __future__ import annotations

import pytest

from clickup_mcp import policy
from clickup_mcp.policy import ToolMeta
from clickup_mcp.store import ClickUpGrant


def grant(email: str | None = "ada@example.com") -> ClickUpGrant:
    return ClickUpGrant(
        id=1,
        clickup_user_id="111",
        email=email,
        username="Ada",
        access_token="tok",
        workspaces=[],
    )


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_META", {}, raising=True)
    monkeypatch.setattr(policy, "TOOL_PROFILE", "core", raising=True)
    monkeypatch.setattr(policy, "ENABLE_DESTRUCTIVE", False, raising=True)
    monkeypatch.setattr(policy, "ADMIN_EMAILS", frozenset(), raising=True)


def test_destructive_tools_are_hidden_by_default():
    policy.TOOL_META["delete_space"] = ToolMeta(phase=1, destructive=True)
    assert policy.allows("delete_space", grant()) is False
    assert "CLICKUP_ENABLE_DESTRUCTIVE" in policy.denial_reason("delete_space", grant())


def test_destructive_tools_appear_once_enabled(monkeypatch):
    monkeypatch.setattr(policy, "ENABLE_DESTRUCTIVE", True)
    policy.TOOL_META["delete_space"] = ToolMeta(phase=1, destructive=True)
    assert policy.allows("delete_space", grant()) is True


def test_core_profile_hides_later_phases():
    policy.TOOL_META["search_tasks"] = ToolMeta(phase=1)
    policy.TOOL_META["start_timer"] = ToolMeta(phase=2)
    policy.TOOL_META["search_docs"] = ToolMeta(phase=3)
    policy.TOOL_META["list_views"] = ToolMeta(phase=4)

    assert policy.allows("search_tasks", grant())
    assert policy.allows("start_timer", grant())
    assert not policy.allows("search_docs", grant())
    assert not policy.allows("list_views", grant())


def test_full_profile_shows_every_phase(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_PROFILE", "full")
    policy.TOOL_META["search_docs"] = ToolMeta(phase=3)
    policy.TOOL_META["list_views"] = ToolMeta(phase=4)
    assert policy.allows("search_docs", grant())
    assert policy.allows("list_views", grant())


def test_unknown_profile_falls_back_to_core(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_PROFILE", "banana")
    policy.TOOL_META["search_tasks"] = ToolMeta(phase=1)
    policy.TOOL_META["search_docs"] = ToolMeta(phase=3)
    assert policy.allows("search_tasks", grant())
    assert not policy.allows("search_docs", grant())


def test_admin_tools_are_hidden_when_no_admins_configured(monkeypatch):
    """Fail closed. An unset CLICKUP_ADMIN_EMAILS must not mean "everyone"."""
    monkeypatch.setattr(policy, "TOOL_PROFILE", "full")
    policy.TOOL_META["remove_user_from_workspace"] = ToolMeta(phase=4, admin=True)
    assert not policy.allows("remove_user_from_workspace", grant())


def test_admin_tools_are_visible_only_to_listed_admins(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_PROFILE", "full")
    monkeypatch.setattr(policy, "ADMIN_EMAILS", frozenset({"ada@example.com"}))
    policy.TOOL_META["remove_user_from_workspace"] = ToolMeta(phase=4, admin=True)

    assert policy.allows("remove_user_from_workspace", grant("ada@example.com"))
    assert policy.allows("remove_user_from_workspace", grant("ADA@Example.com"))
    assert not policy.allows("remove_user_from_workspace", grant("bob@example.com"))
    assert not policy.allows("remove_user_from_workspace", grant(None))
    assert not policy.allows("remove_user_from_workspace", None)


def test_anonymous_caller_never_reaches_admin_or_destructive(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_PROFILE", "full")
    monkeypatch.setattr(policy, "ADMIN_EMAILS", frozenset({"ada@example.com"}))
    policy.TOOL_META["delete_task"] = ToolMeta(phase=1, destructive=True)
    policy.TOOL_META["invite_user_to_workspace"] = ToolMeta(phase=4, admin=True)

    assert not policy.allows("delete_task", None)
    assert not policy.allows("invite_user_to_workspace", None)


def test_admin_gate_applies_even_with_destructive_enabled(monkeypatch):
    """The gates are independent — enabling one must not open another."""
    monkeypatch.setattr(policy, "TOOL_PROFILE", "full")
    monkeypatch.setattr(policy, "ENABLE_DESTRUCTIVE", True)
    policy.TOOL_META["remove_user_from_workspace"] = ToolMeta(
        phase=4, admin=True, destructive=True
    )
    assert not policy.allows("remove_user_from_workspace", grant("bob@example.com"))


def test_unregistered_tools_are_allowed():
    """Anything outside the phase system is visible rather than silently hidden."""
    assert policy.allows("some_helper_tool", grant())


def test_denial_reasons_tell_the_model_not_to_retry(monkeypatch):
    monkeypatch.setattr(policy, "ADMIN_EMAILS", frozenset({"ada@example.com"}))
    policy.TOOL_META["a"] = ToolMeta(phase=3)
    policy.TOOL_META["b"] = ToolMeta(phase=1, destructive=True)
    policy.TOOL_META["c"] = ToolMeta(phase=1, admin=True)

    for name in ("a", "b", "c"):
        assert "Do not retry" in policy.denial_reason(name, grant("bob@example.com"))
