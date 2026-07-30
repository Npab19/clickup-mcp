"""Per-identity tool gating, ported from CW Manage 3's `policy-gate.ts`.

That server proxies `McpServer.tool()` so disallowed tools never enter the
registry and `tools/list` omits them entirely. FastMCP has no equivalent hook, so
the same guarantee is achieved by overriding `list_tools`/`call_tool`: a hidden
tool is neither advertised nor callable, and a caller who guesses its name gets
the same "unknown tool" answer as if it did not exist.

Three independent gates, all fail-closed:

* **profile** — `CLICKUP_TOOL_PROFILE` selects which phases are advertised at all.
  Everything is *built*; this only controls what a caller is shown, because ~140
  flat tools measurably degrades tool selection.
* **destructive** — off unless `CLICKUP_ENABLE_DESTRUCTIVE` is set. The rule is
  **irreversible loss of user-authored content, or a cascading delete** — ClickUp
  deletes cascade, so a Space takes its Folders, Lists and Tasks with it. Removing
  something that can simply be re-added (a dependency, a task link, a tag on a time
  entry, a guest's access to one item) is *not* destructive; marking it so would
  hide ordinary workflow tools behind an admin flag for no safety gain.
* **admin** — the Phase 4 surface is limited to `CLICKUP_ADMIN_EMAILS`.

Worth stating plainly: none of this is the real security boundary. Every call runs
against the caller's own ClickUp OAuth token, so ClickUp enforces that user's
actual permissions upstream and the server cannot exceed what the user could do in
the UI. This layer is defence in depth and a blast-radius limiter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool as MCPTool

from clickup_mcp.constants import (
    ADMIN_EMAILS,
    ENABLE_DESTRUCTIVE,
    PROFILE_PHASES,
    TOOL_PROFILE,
)
from clickup_mcp.store import ClickUpGrant

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolMeta:
    phase: int
    destructive: bool = False
    admin: bool = False


TOOL_META: dict[str, ToolMeta] = {}


def register_meta(name: str, meta: ToolMeta) -> None:
    TOOL_META[name] = meta


def active_phases() -> frozenset[int]:
    phases = PROFILE_PHASES.get(TOOL_PROFILE)
    if phases is None:
        logger.warning(
            "Unknown CLICKUP_TOOL_PROFILE; falling back to 'core'",
            extra={"profile": TOOL_PROFILE},
        )
        return PROFILE_PHASES["core"]
    return phases


def is_admin(grant: ClickUpGrant | None) -> bool:
    if not ADMIN_EMAILS or grant is None or not grant.email:
        return False
    return grant.email.lower() in ADMIN_EMAILS


def allows(name: str, grant: ClickUpGrant | None) -> bool:
    """Whether this caller may see and call `name`.

    Tools with no registered metadata are allowed — that covers anything defined
    outside the phase system rather than silently hiding it.
    """
    meta = TOOL_META.get(name)
    if meta is None:
        return True
    if meta.phase not in active_phases():
        return False
    if meta.destructive and not ENABLE_DESTRUCTIVE:
        return False
    if meta.admin and not is_admin(grant):
        return False
    return True


def denial_reason(name: str, grant: ClickUpGrant | None) -> str:
    """A message that tells the operator how to enable the tool, without
    implying to the model that retrying might work."""
    meta = TOOL_META.get(name)
    if meta is None:
        return f"Unknown tool: {name}"
    if meta.phase not in active_phases():
        return (
            f"Tool '{name}' is not enabled on this server "
            f"(phase {meta.phase}; profile is '{TOOL_PROFILE}'). "
            "An administrator can set CLICKUP_TOOL_PROFILE=full. Do not retry."
        )
    if meta.destructive and not ENABLE_DESTRUCTIVE:
        return (
            f"Tool '{name}' performs a destructive ClickUp operation and is disabled "
            "on this server. An administrator can set CLICKUP_ENABLE_DESTRUCTIVE=true. "
            "Do not retry."
        )
    if meta.admin and not is_admin(grant):
        return (
            f"Tool '{name}' is restricted to administrators of this MCP server. "
            "Do not retry."
        )
    return f"Tool '{name}' is not available."


def startup_report() -> dict[str, object]:
    """Summary for the startup log — no PII, counts only."""
    phases = active_phases()
    visible = sum(
        1
        for name, meta in TOOL_META.items()
        if meta.phase in phases
        and (ENABLE_DESTRUCTIVE or not meta.destructive)
        and not meta.admin
    )
    return {
        "profile": TOOL_PROFILE,
        "phases": sorted(phases),
        "destructive_enabled": ENABLE_DESTRUCTIVE,
        "admin_emails_configured": len(ADMIN_EMAILS),
        "tools_registered": len(TOOL_META),
        "tools_visible_to_non_admin": visible,
    }


class GovernedFastMCP(FastMCP):
    """FastMCP with per-identity tool gating, rate limiting, and audit."""

    def streamable_http_app(self):
        """Attach a real process-level lifespan.

        FastMCP builds its Starlette app with `lifespan=lambda app:
        self.session_manager.run()`, hardcoded — the `lifespan=` argument its
        constructor accepts is wired into the low-level MCP *session*, so it does
        not run at process startup and never covers custom routes. Left alone,
        `/clickup-callback` and `/health` would hit an unconnected store.

        So wrap the app's lifespan rather than replacing it: ours opens the store
        first, then defers to FastMCP's session manager.
        """
        from contextlib import asynccontextmanager

        app = super().streamable_http_app()
        inner = app.router.lifespan_context

        @asynccontextmanager
        async def combined(scope_app):
            from clickup_mcp.runtime import clickup, store

            await store.connect()
            logger.info("ClickUp MCP ready", extra=startup_report())
            try:
                async with inner(scope_app):
                    yield
            finally:
                await clickup.close()
                await store.close()

        app.router.lifespan_context = combined
        return app

    async def list_tools(self) -> list[MCPTool]:
        # Imported lazily: context/audit reach into runtime, which must finish
        # its startup checks before this module is imported by app.
        from clickup_mcp.context import current_grant_or_none

        grant = await current_grant_or_none()
        tools = await super().list_tools()
        return [t for t in tools if allows(t.name, grant)]

    async def call_tool(self, name: str, arguments: dict):  # type: ignore[override]
        import time

        from clickup_mcp import audit, rate_limit
        from clickup_mcp.context import current_grant_or_none

        grant = await current_grant_or_none()

        if not allows(name, grant):
            logger.warning(
                "Blocked tool call",
                extra={"tool": name, "grant_id": grant.id if grant else None},
            )
            await audit.record(grant, name, arguments, "denied", denial_reason(name, grant), 0)
            raise ToolError(denial_reason(name, grant))

        if grant is not None:
            rate_limit.check(grant.id)

        started = time.monotonic()
        try:
            result = await super().call_tool(name, arguments)
        except Exception as exc:
            await audit.record(
                grant,
                name,
                arguments,
                "error",
                f"{type(exc).__name__}: {exc}",
                int((time.monotonic() - started) * 1000),
            )
            raise
        await audit.record(
            grant,
            name,
            arguments,
            "success",
            None,
            int((time.monotonic() - started) * 1000),
            result=result,
        )
        return result
