"""Tool-call audit trail, ported from CW Manage 3's `audit-capture.ts`.

With destructive tools available on a shared server, this is the only way to
answer "who deleted that Space". Every `tools/call` is recorded with the acting
ClickUp user, the arguments, the outcome, and the duration.

Auditing must never break a tool call, so every failure here is swallowed and
logged.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from clickup_mcp.runtime import store
from clickup_mcp.store import ClickUpGrant

logger = logging.getLogger(__name__)

# Argument keys whose values must never reach the audit table.
_REDACT_KEYS = frozenset({"token", "access_token", "password", "secret", "api_key"})


def _redact(arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        return arguments
    return {
        k: ("<redacted>" if k.lower() in _REDACT_KEYS else v) for k, v in arguments.items()
    }


# Keys worth keeping from a tool's result to identify what it touched.
_REF_KEYS = ("id", "url", "task_id", "list_id", "folder_id", "space_id", "deleted")


def result_ref(result: Any, _depth: int = 0) -> str | None:
    """Pull identifiers out of a tool result.

    `create_task` recording only its arguments tells you a task was made but not
    which one, which is the first thing anyone asks when reconciling the trail
    against ClickUp. Best-effort: the shape varies by tool and by how FastMCP
    converted it, so this walks a little and gives up quietly.
    """
    if _depth > 3:
        return None
    try:
        if isinstance(result, dict):
            found = {k: result[k] for k in _REF_KEYS if k in result}
            if found:
                return json.dumps(found, default=str)[:500]
            for value in result.values():
                nested = result_ref(value, _depth + 1)
                if nested:
                    return nested
        elif isinstance(result, (list, tuple)):
            for item in result[:3]:
                nested = result_ref(item, _depth + 1)
                if nested:
                    return nested
        elif hasattr(result, "text"):  # an MCP TextContent block
            import json as _json

            try:
                return result_ref(_json.loads(result.text), _depth + 1)
            except (ValueError, TypeError):
                return None
    except Exception:  # pragma: no cover - never break a call for the audit
        return None
    return None


async def record(
    grant: ClickUpGrant | None,
    tool: str,
    arguments: Any,
    status: str,
    error: str | None,
    duration_ms: int,
    result: Any = None,
) -> None:
    try:
        await store.record_audit(
            grant_id=grant.id if grant else None,
            clickup_user_id=grant.clickup_user_id if grant else None,
            tool=tool,
            arguments=_redact(arguments),
            status=status,
            error=error,
            duration_ms=duration_ms,
            result_ref=result_ref(result) if result is not None else None,
        )
    except Exception:
        logger.warning("Could not write audit record", extra={"tool": tool}, exc_info=True)
