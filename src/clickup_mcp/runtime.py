"""Process-wide singletons and startup configuration checks.

Kept separate from `app` so that `context` and `policy` can reach the store and
HTTP client without importing the FastMCP instance that depends on them.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlparse

from clickup_mcp.client import ClickUpClient
from clickup_mcp.store import Store, StoreError

logger = logging.getLogger(__name__)


def _fatal(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")
    sys.exit(1)


def _resolve_server_url() -> str:
    server_url = os.environ.get("SERVER_URL", "http://localhost:8000")
    parsed = urlparse(server_url)
    is_localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_localhost:
        _fatal(
            f"SERVER_URL must be HTTPS for OAuth security, got: {server_url}\n"
            "  Set SERVER_URL to your public https:// URL "
            "(http://localhost is allowed for local dev only)."
        )
    return server_url


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        _fatal(
            f"{name} is required. Create a ClickUp OAuth app at "
            "https://app.clickup.com/settings/apps and copy its credentials into .env."
        )
    return value


SERVER_URL = _resolve_server_url()
CLICKUP_CLIENT_ID = _require("CLICKUP_CLIENT_ID")
CLICKUP_CLIENT_SECRET = _require("CLICKUP_CLIENT_SECRET")

try:
    store = Store()
except StoreError as exc:  # missing / malformed TOKEN_ENCRYPTION_KEY
    _fatal(str(exc))
    raise  # unreachable; satisfies type checkers

clickup = ClickUpClient()
