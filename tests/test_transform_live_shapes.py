"""Transform behaviour against shapes observed from the live ClickUp API.

Recorded 2026-07-28 from a real Workspace: 100 tasks via GetFilteredTeamTasks,
5 Spaces, and their Lists. The fixtures below reproduce the exact structures the
API returned, including the two things the OpenAPI spec does not tell you.
"""
from __future__ import annotations

from clickup_mcp.transform import collection, summarize_list, summarize_task

# A folderless List: ClickUp does not omit `folder`, it sends a placeholder whose
# name is the literal string "hidden". 54 of the first 100 real tasks looked like
# this — taken at face value the summary claimed a folder named "hidden".
FOLDERLESS = {
    "id": "86bb4x7ff",
    "name": "Sweep mutating RPCs",
    "status": {"status": "to do", "color": "#87909e", "orderindex": 0, "type": "open"},
    "list": {"id": "901417246732", "name": "Backlog", "access": True},
    "folder": {"id": "90163123456", "name": "hidden", "hidden": True, "access": True},
    "space": {"id": "90141365402"},
    "url": "https://app.clickup.com/t/86bb4x7ff",
}

FOLDERED = {
    **FOLDERLESS,
    "id": "86bb4p9m2",
    "folder": {"id": "90163987654", "name": "Sprint Folder", "hidden": False, "access": True},
    "assignees": [{"id": 96030373, "username": "Nikko Pabion", "email": "n@example.com"}],
    "priority": {"priority": "normal", "color": "#6fddff", "id": "3", "orderindex": "3"},
    "due_date": "1785456000000",
    "time_spent": 3600000,  # int, though the v2 spec declares string|null
    "time_estimate": None,
    "points": None,
    "custom_item_id": 0,
}


def test_folderless_task_reports_no_folder():
    summary = summarize_task(FOLDERLESS)
    assert "folder" not in summary, (
        'a folderless task must not claim a folder named "hidden"'
    )
    assert summary["list"] == "Backlog"


def test_foldered_task_still_reports_its_folder():
    assert summarize_task(FOLDERED)["folder"] == "Sprint Folder"


def test_folderless_list_reports_no_folder():
    summary = summarize_list(
        {
            "id": "901417246732",
            "name": "Backlog",
            "task_count": 107,
            "folder": {"id": "1", "name": "hidden", "hidden": True},
            "space": {"id": "90141365402", "name": "Enverge"},
        }
    )
    assert "folder" not in summary
    assert summary["space"] == "Enverge" and summary["task_count"] == 107


def test_space_is_labelled_as_an_id_not_a_name():
    """ClickUp only sends an id for `space`; the key should not imply a name."""
    summary = summarize_task(FOLDERED)
    assert summary["space_id"] == "90141365402"
    assert "space" not in summary


def test_int_time_spent_survives_the_specs_wrong_type():
    """The v2 spec declares time_spent as string|null; the API returns an int."""
    assert summarize_task(FOLDERED)["time_spent_ms"] == 3600000


def test_null_heavy_task_summarizes_without_empty_keys():
    """95 of 100 real tasks had no due_date, 100 had no time_estimate or points."""
    summary = summarize_task(FOLDERLESS)
    for absent in ("due_date", "time_estimate_ms", "points", "priority", "assignees"):
        assert absent not in summary
    assert summary["id"] and summary["name"] and summary["status"]


def test_summary_is_drastically_smaller_than_the_raw_page():
    """The real 100-task page was 1,122,129 bytes raw and 41,155 summarized.

    Returning raw would exhaust a context window on a single search.
    """
    import json

    page = {"tasks": [FOLDERED] * 100, "last_page": False}
    raw = len(json.dumps(page))
    slim = len(json.dumps(collection(page, "tasks", summarize_task)))
    assert slim < raw / 2
    assert collection(page, "tasks", summarize_task)["last_page"] is False
