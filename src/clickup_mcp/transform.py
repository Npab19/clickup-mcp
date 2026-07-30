"""Response slimming.

ClickUp task objects are enormous — full status arrays, every custom field with its
type config, all watchers, and nested list/folder/space objects, on every task in a
100-task page. Returned raw, one query can consume most of a context window.

Every tool therefore summarizes by default and takes `raw=True` as an escape hatch.
Dates come back as Unix-millisecond strings and are rendered as ISO-8601 UTC, since
a model reading `1621915186877` cannot reason about it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable


def ms_to_iso(value: Any) -> str | None:
    """ClickUp timestamps are Unix milliseconds, usually as strings."""
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _name(obj: Any) -> str | None:
    return obj.get("name") if isinstance(obj, dict) else None


def _folder_name(obj: Any) -> str | None:
    """Folder name, or None when the task/list is folderless.

    ClickUp does not omit the folder for a folderless List — it returns a
    placeholder `{"id": ..., "name": "hidden", "hidden": true}`. Taken at face
    value that reports a folder literally named "hidden", which was true for 54
    of the first 100 real tasks tested.
    """
    if not isinstance(obj, dict) or obj.get("hidden") is True:
        return None
    return obj.get("name")


def _people(items: Any) -> list[str]:
    """Assignees/watchers as plain names — the id/color/avatar noise is dropped."""
    if not isinstance(items, list):
        return []
    return [
        p.get("username") or p.get("email") or str(p.get("id"))
        for p in items
        if isinstance(p, dict)
    ]


def summarize_task(task: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(task, dict):
        return task

    status = task.get("status") or {}
    priority = task.get("priority") or {}
    summary: dict[str, Any] = {
        "id": task.get("id"),
        "name": task.get("name"),
        "status": status.get("status") if isinstance(status, dict) else status,
        "assignees": _people(task.get("assignees")),
        "due_date": ms_to_iso(task.get("due_date")),
        "start_date": ms_to_iso(task.get("start_date")),
        "date_updated": ms_to_iso(task.get("date_updated")),
        "list": _name(task.get("list")),
        "folder": _folder_name(task.get("folder")),
        # ClickUp only ever returns an id here, no name — say so in the key rather
        # than sitting an opaque id next to the human-readable list/folder names.
        "space_id": (task.get("space") or {}).get("id")
        if isinstance(task.get("space"), dict)
        else None,
        "url": task.get("url"),
    }
    if task.get("custom_id"):
        summary["custom_id"] = task["custom_id"]
    if isinstance(priority, dict) and priority.get("priority"):
        summary["priority"] = priority["priority"]
    if task.get("parent"):
        summary["parent"] = task["parent"]
    if task.get("archived"):
        summary["archived"] = True
    if task.get("time_estimate"):
        summary["time_estimate_ms"] = task["time_estimate"]
    if task.get("time_spent"):
        summary["time_spent_ms"] = task["time_spent"]

    tags = task.get("tags")
    if isinstance(tags, list) and tags:
        summary["tags"] = [t.get("name") for t in tags if isinstance(t, dict)]

    # Only custom fields that actually carry a value — the full definitions are
    # available from list_accessible_custom_fields when they are needed.
    fields = task.get("custom_fields")
    if isinstance(fields, list):
        valued = {
            f.get("name"): f.get("value")
            for f in fields
            if isinstance(f, dict) and f.get("value") not in (None, "", [])
        }
        if valued:
            summary["custom_fields"] = valued

    return {k: v for k, v in summary.items() if v not in (None, [], {})}


def summarize_list(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    status = item.get("status") or {}
    summary = {
        "id": item.get("id"),
        "name": item.get("name"),
        "task_count": item.get("task_count"),
        "due_date": ms_to_iso(item.get("due_date")),
        "folder": _folder_name(item.get("folder")),
        "space": _name(item.get("space")),
        "archived": item.get("archived") or None,
        "status": status.get("status") if isinstance(status, dict) else None,
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_folder(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    summary = {
        "id": item.get("id"),
        "name": item.get("name"),
        "task_count": item.get("task_count"),
        "list_count": len(item.get("lists") or []) or None,
        "space": _name(item.get("space")),
        "hidden": item.get("hidden") or None,
        "archived": item.get("archived") or None,
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_space(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    statuses = item.get("statuses")
    summary = {
        "id": item.get("id"),
        "name": item.get("name"),
        "private": item.get("private"),
        "archived": item.get("archived") or None,
        "statuses": [s.get("status") for s in statuses if isinstance(s, dict)]
        if isinstance(statuses, list)
        else None,
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_comment(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    user = item.get("user") or {}
    summary = {
        "id": item.get("id"),
        "text": item.get("comment_text"),
        "user": user.get("username") if isinstance(user, dict) else None,
        "date": ms_to_iso(item.get("date")),
        "resolved": item.get("resolved") or None,
        "reply_count": item.get("reply_count") or None,
        "assignee": _name(item.get("assignee")),
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_time_entry(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    task = item.get("task") or {}
    user = item.get("user") or {}
    duration = item.get("duration")
    summary = {
        "id": item.get("id"),
        "task_id": task.get("id") if isinstance(task, dict) else None,
        "task_name": _name(task),
        "user": user.get("username") if isinstance(user, dict) else None,
        "start": ms_to_iso(item.get("start")),
        "end": ms_to_iso(item.get("end")),
        "duration_ms": duration,
        "duration_hours": round(int(duration) / 3_600_000, 2)
        if str(duration or "").lstrip("-").isdigit()
        else None,
        "billable": item.get("billable"),
        "description": item.get("description") or None,
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_doc(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    summary = {
        "id": item.get("id"),
        "name": item.get("name"),
        "date_created": ms_to_iso(item.get("date_created")),
        "date_updated": ms_to_iso(item.get("date_updated")),
        "creator": item.get("creator"),
        "public": item.get("public"),
        "url": item.get("url"),
    }
    return {k: v for k, v in summary.items() if v is not None}


def summarize_chat_message(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    user = item.get("user") or {}
    summary = {
        "id": item.get("id"),
        "text": item.get("content") or item.get("text_content"),
        "user": user.get("username") if isinstance(user, dict) else None,
        "date": ms_to_iso(item.get("date")),
        "reply_count": item.get("reply_count") or None,
        "resolved": item.get("resolved") or None,
    }
    return {k: v for k, v in summary.items() if v is not None}


def collection(
    payload: Any,
    key: str,
    summarizer: Callable[[dict[str, Any]], dict[str, Any]],
    extra_keys: tuple[str, ...] = ("last_page", "next_cursor", "next_page"),
) -> dict[str, Any]:
    """Summarize `payload[key]`, preserving pagination signals.

    Also normalizes the bare-array responses ClickUp returns from some endpoints
    into the same `{key: [...]}` shape, so tools have one contract.
    """
    if isinstance(payload, list):
        return {key: [summarizer(i) for i in payload], "count": len(payload)}
    if not isinstance(payload, dict):
        return {key: payload}

    items = payload.get(key)
    if not isinstance(items, list):
        return payload

    result: dict[str, Any] = {key: [summarizer(i) for i in items], "count": len(items)}
    for extra in extra_keys:
        if extra in payload:
            result[extra] = payload[extra]
    return result
