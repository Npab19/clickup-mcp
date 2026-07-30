from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from clickup_mcp.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("clickup_mcp")

from clickup_mcp.app import mcp, provider  # noqa: E402
from clickup_mcp.runtime import store  # noqa: E402

import clickup_mcp.tools  # noqa: E402, F401

_ERROR_PAGE = """<!doctype html>
<title>ClickUp authorization failed</title>
<body style="font-family:system-ui;max-width:34rem;margin:4rem auto;line-height:1.5">
<h1>Authorization failed</h1>
<p>{message}</p>
<p>Close this tab and start the connection again from your MCP client.</p>
</body>"""


@mcp.custom_route("/clickup-callback", methods=["GET"])
async def clickup_callback_handler(request: Request) -> Response:
    """Handle the redirect back from ClickUp after the user authorizes."""
    error = request.query_params.get("error")
    if error:
        logger.warning("ClickUp returned an error to the callback", extra={"error": error})
        return HTMLResponse(_ERROR_PAGE.format(message="ClickUp denied the request."), status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return HTMLResponse(
            _ERROR_PAGE.format(message="The callback was missing its code or state parameter."),
            status_code=400,
        )

    try:
        redirect_uri = await provider.handle_clickup_callback(code, state)
    except ValueError as exc:
        logger.error("ClickUp callback error: %s", exc)
        return HTMLResponse(_ERROR_PAGE.format(message=str(exc)), status_code=400)

    return RedirectResponse(url=redirect_uri, status_code=302)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    """Liveness probe. Counts only — no user identifiers, no tokens."""
    from clickup_mcp.policy import startup_report

    try:
        grants = await store.count_grants()
        status = "ok"
    except Exception:
        logger.warning("Health check could not read the store", exc_info=True)
        grants, status = -1, "degraded"

    return JSONResponse({"status": status, "authorized_users": grants, **startup_report()})


def main() -> None:
    """Run the ClickUp MCP server."""
    logger.info("Starting ClickUp MCP server")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
