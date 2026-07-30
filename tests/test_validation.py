"""Input coercion and the destructive-operation guard."""
from __future__ import annotations

import pytest

from clickup_mcp.transform import ms_to_iso, summarize_task
from clickup_mcp.validation import (
    array_params,
    as_list,
    priority_to_int,
    require_confirm,
    require_id,
    to_unix_ms,
    validate_page,
)


def test_confirm_guard_blocks_unconfirmed_deletes():
    with pytest.raises(ValueError, match="PERMANENT"):
        require_confirm(False, "delete this Space")
    # Truthy-but-not-True must not slip through.
    with pytest.raises(ValueError):
        require_confirm("yes", "delete this Space")  # type: ignore[arg-type]
    require_confirm(True, "delete this Space")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-07-31", 1785456000000),
        ("2026-07-31T00:00:00Z", 1785456000000),
        (1785456000000, 1785456000000),
        ("1785456000000", 1785456000000),
        (1785456000, 1785456000000),  # seconds get promoted to ms
        (None, None),
        ("", None),
    ],
)
def test_date_coercion(value, expected):
    assert to_unix_ms(value, "due_date") == expected


def test_bad_date_names_the_field_and_shows_the_format():
    with pytest.raises(ValueError, match="due_date must be an ISO-8601 date"):
        to_unix_ms("next tuesday", "due_date")


@pytest.mark.parametrize(
    "value,expected",
    [("urgent", 1), ("HIGH", 2), ("normal", 3), ("low", 4), (2, 2), ("3", 3), (None, None)],
)
def test_priority_accepts_names_and_numbers(value, expected):
    assert priority_to_int(value) == expected


@pytest.mark.parametrize("value", ["critical", 0, 5, "9"])
def test_bad_priority_is_rejected(value):
    with pytest.raises(ValueError):
        priority_to_int(value)


def test_ids_must_be_present():
    assert require_id(" abc ", "task_id") == "abc"
    assert require_id(123, "task_id") == "123"
    for bad in (None, "", "   "):
        with pytest.raises(ValueError, match="task_id is required"):
            require_id(bad, "task_id")


def test_page_bounds():
    assert validate_page(0) == 0
    assert validate_page(None) is None
    for bad in (-1, 1001, "2"):
        with pytest.raises(ValueError):
            validate_page(bad)


def test_as_list_normalizes_scalars_and_csv():
    assert as_list("a,b , c") == ["a", "b", "c"]
    assert as_list(["a"]) == ["a"]
    assert as_list(None) is None
    assert as_list("") is None
    assert as_list(7) == [7]


def test_array_params_adds_the_bracket_suffix():
    """The team-scoped task endpoints require `assignees[]`, not `assignees`."""
    out = array_params({"assignees": "1,2", "page": 0, "tags": None}, ("assignees", "tags"))
    assert out == {"assignees[]": ["1", "2"], "page": 0}


def test_ms_to_iso_survives_garbage():
    assert ms_to_iso("1621915186877") == "2021-05-25T03:59:46.877000+00:00"
    for bad in (None, "", 0, "0", "abc", []):
        assert ms_to_iso(bad) is None


def test_task_summary_drops_the_bulk_but_keeps_what_matters():
    task = {
        "id": "abc",
        "name": "Fix login",
        "status": {"status": "in progress", "color": "#fff", "orderindex": 1, "type": "custom"},
        "assignees": [{"id": 1, "username": "Ada", "color": "#000", "profilePicture": "http://x"}],
        "priority": {"priority": "high", "color": "#f00", "id": "2", "orderindex": "2"},
        "due_date": "1785456000000",
        "tags": [{"name": "backend", "tag_bg": "#000", "tag_fg": "#fff"}],
        "list": {"id": "1", "name": "Sprint", "access": True},
        "url": "https://app.clickup.com/t/abc",
        "custom_fields": [
            {"id": "f1", "name": "Points", "value": 5, "type_config": {"huge": "blob"}},
            {"id": "f2", "name": "Empty", "value": None, "type_config": {}},
        ],
        "watchers": [{"id": 9}] * 50,
        "description": "x" * 5000,
    }
    summary = summarize_task(task)

    assert summary["status"] == "in progress"
    assert summary["assignees"] == ["Ada"]
    assert summary["priority"] == "high"
    assert summary["tags"] == ["backend"]
    assert summary["list"] == "Sprint"
    assert summary["due_date"].startswith("2026-07-31")
    assert summary["custom_fields"] == {"Points": 5}  # valueless field dropped
    assert "watchers" not in summary
    assert "description" not in summary
