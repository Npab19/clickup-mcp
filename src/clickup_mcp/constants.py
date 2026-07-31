"""ClickUp endpoints and server-wide constants — single source of truth."""
from __future__ import annotations

import os

# --- ClickUp API -------------------------------------------------------------
# One base URL serves both API versions: v2 paths are `/v2/...`, v3 are `/v3/...`.
# (The v3 spec declares its server as `https://api.clickup.com/` with `/api/v3/...`
# paths; the two are equivalent.)
CLICKUP_API_BASE = "https://api.clickup.com/api"

# ClickUp's authorize endpoint really is app.clickup.com/api — not an /oauth/ path.
CLICKUP_AUTHORIZE_URL = "https://app.clickup.com/api"
CLICKUP_TOKEN_URL = "https://api.clickup.com/api/v2/oauth/token"

# The path this server exposes for ClickUp's redirect. The ClickUp app's
# registered redirect URL must be exactly {SERVER_URL}{CALLBACK_PATH}.
CALLBACK_PATH = "/clickup-callback"

# ClickUp's OAuth has no scope parameter — the user picks Workspaces on the
# consent screen. This scope exists only between the MCP client and this server.
MCP_SCOPE = "clickup"

# --- Token lifetimes ---------------------------------------------------------
# ClickUp access tokens do not expire and no refresh token is issued, so there is
# nothing upstream to refresh. These govern only the MCP-level tokens we mint.
#
# The refresh token we issue never expires, and the ClickUp grant behind it never
# expires, so a user should never have to re-authorize once connected. The access
# token TTL only controls how often the client has to exercise the refresh path.
AUTH_CODE_TTL = 300
PENDING_AUTH_TTL = 900


def _env_seconds(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(60, value)


MCP_ACCESS_TOKEN_TTL = _env_seconds("CLICKUP_ACCESS_TOKEN_TTL", 86_400)

# How long a rotated refresh token keeps working after being exchanged.
#
# Strict single-use rotation is unforgiving: if the response is lost in flight,
# the client retries, or two editor windows refresh concurrently, the old token
# is already gone and the only recovery is a full re-authorization. A short
# replay window absorbs all three without meaningfully weakening rotation.
REFRESH_TOKEN_GRACE = _env_seconds("CLICKUP_REFRESH_GRACE", 300)

# --- Rate limiting -----------------------------------------------------------
# ClickUp's own ceiling is per token, i.e. per user: 100/min on Free, Unlimited,
# and Business; 1,000 on Business Plus; 10,000 on Enterprise. We cannot detect the
# plan cheaply, so we assume the lowest and let the 429 handler correct us.
CLICKUP_ASSUMED_RPM = 100

# --- Response handling -------------------------------------------------------
HTTP_TIMEOUT = 30.0
MAX_CACHE_ENTRIES_PER_GRANT = 128
RETRYABLE_STATUS = (502, 503, 504)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# --- Deployment configuration ------------------------------------------------
TOOL_PROFILE = (os.environ.get("CLICKUP_TOOL_PROFILE") or "core").strip().lower()
ENABLE_DESTRUCTIVE = _env_flag("CLICKUP_ENABLE_DESTRUCTIVE", False)
ADMIN_EMAILS = frozenset(
    e.strip().lower()
    for e in (os.environ.get("CLICKUP_ADMIN_EMAILS") or "").split(",")
    if e.strip()
)
RATE_CAPACITY = _env_int("CLICKUP_RATE_CAPACITY", 60)
RATE_REFILL_PER_MINUTE = _env_int("CLICKUP_RATE_REFILL_PER_MINUTE", 60)

DB_PATH = os.environ.get("CLICKUP_DB_PATH") or "/data/clickup.db"

# Phases 1-2 are the everyday surface; 3-4 are opt-in via CLICKUP_TOOL_PROFILE=full.
PROFILE_PHASES: dict[str, frozenset[int]] = {
    "core": frozenset({1, 2}),
    "full": frozenset({1, 2, 3, 4}),
}
