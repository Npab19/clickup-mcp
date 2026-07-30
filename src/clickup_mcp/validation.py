"""Input validation and coercion for tool parameters.

Errors raise ValueError with messages a model can relay verbatim, and say what to
do instead rather than just what was wrong.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# ClickUp's own cap on team-wide task queries.
MAX_PAGE = 1000


def require_id(value: Any, field: str) -> str:
    """ClickUp ids are opaque strings — never constructed or incremented."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{field} is required.")
    text = str(value).strip()
    if len(text) > 128:
        raise ValueError(f"{field} does not look like a ClickUp id: {text[:32]}...")
    return text


def optional_id(value: Any, field: str) -> str | None:
    return None if value is None else require_id(value, field)


def validate_page(page: int | None) -> int | None:
    if page is None:
        return None
    if not isinstance(page, int) or page < 0 or page > MAX_PAGE:
        raise ValueError(f"page must be an integer between 0 and {MAX_PAGE}, got {page!r}")
    return page


def to_unix_ms(value: Any, field: str) -> int | None:
    """Accept an ISO-8601 date/datetime or a raw epoch value; emit epoch ms.

    ClickUp speaks only in Unix milliseconds, but a model given a user's
    "due next Friday" will naturally produce an ISO date.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = int(value)
        # Anything this small is seconds, not milliseconds (1e11 ms ~= 1973).
        return number * 1000 if number < 100_000_000_000 else number
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{field} must be an ISO-8601 date (2026-07-31 or "
                f"2026-07-31T17:00:00Z) or a Unix timestamp, got {value!r}"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    raise ValueError(f"{field} must be a date string or Unix timestamp, got {value!r}")


def require_confirm(confirm: bool, what: str) -> None:
    """Guard on every destructive tool.

    ClickUp deletes cascade, so the model must state what will be destroyed and
    get the user's agreement before passing confirm=True.
    """
    if confirm is not True:
        raise ValueError(
            f"Refusing to {what} without confirmation. This is PERMANENT and cascades "
            "to everything contained in it. Tell the user exactly what will be deleted, "
            "get their explicit agreement, then call again with confirm=True."
        )


def as_list(value: Any) -> list[Any] | None:
    """Normalize a scalar / comma-separated string / list into a list."""
    if value is None:
        return None
    if isinstance(value, list):
        return value or None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return parts or None
    return [value]


def array_params(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Expand list values into ClickUp's repeated `key[]=v` query form.

    httpx renders a list value as repeated keys, so the work here is only adding
    the `[]` suffix that the team-scoped endpoints require.
    """
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key in keys:
            items = as_list(value)
            if items:
                out[f"{key}[]"] = items
        elif value is not None:
            out[key] = value
    return out


def priority_to_int(priority: Any, field: str = "priority") -> int | None:
    """ClickUp takes priority as 1-4; users say "urgent"/"high"/"normal"/"low"."""
    if priority in (None, ""):
        return None
    names = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
    if isinstance(priority, str) and not priority.isdigit():
        key = priority.strip().lower()
        if key not in names:
            raise ValueError(
                f"{field} must be one of urgent, high, normal, low (or 1-4), got {priority!r}"
            )
        return names[key]
    number = int(priority)
    if number not in (1, 2, 3, 4):
        raise ValueError(f"{field} must be 1 (urgent) to 4 (low), got {priority!r}")
    return number
