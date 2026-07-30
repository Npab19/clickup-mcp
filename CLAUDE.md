# CLAUDE.md — ClickUp MCP Server

Notes for working on this codebase.

## What this is

A **multi-user** MCP server for ClickUp. Pattern B (Python + FastMCP + OAuth
delegation) from `../General.Claude.md`, extended with the per-identity governance
layer from `../CW Manage 3/src/middleware/`.

Reference implementations, and what each contributes:

- **`../Whoop/`** — OAuth delegation mechanics, client shape, `transform.py`,
  Docker layout. Single-user; do not copy its token handling.
- **`../CW Manage 3/src/middleware/`** — genuinely multi-user. `policy-gate.ts`,
  `audit-capture.ts`, `rate-limit.ts`, `identity-resolver.ts` are the originals for
  this server's `policy.py`, `audit.py`, `rate_limit.py`, `context.py`. Note that
  `General.Claude.md` files it under "Pattern A: TypeScript + API Key", which badly
  undersells it.

## The one rule

**Never introduce process-global state keyed on anything but grant id.**

The whole design turns on `context.current_client()` resolving the calling user's
token per request. Three things must stay namespaced by grant:

- `ClickUpClient._caches` — an un-namespaced cache serves user A's tasks to user B
- `rate_limit._buckets`
- everything written to `audit_log`

`tests/test_isolation.py` is the gate. Run it before and after any change to
`store.py`, `client.py`, `context.py`, or `oauth_provider.py`.

## ClickUp API facts that trip people up

- **The v3 spec is ~20% of the API.** Chat, Docs, audit logs, and a few strays.
  Tasks/Lists/Spaces/Folders/time tracking/comments/custom fields are **v2 only**.
  Both specs are vendored: `ClickUp_PUBLIC_API_V3.yaml` (35 ops) and
  `clickup-api-v2-reference.json` (137 ops).
- **No PKCE, no scopes** on ClickUp's side. Do not add them back to
  `oauth_provider.authorize()`.
- **No refresh token, tokens never expire.** There is deliberately no refresh path
  in `client.py`. A 401 with an `OAUTH_*` ECODE means revoked → prompt re-auth. A
  401 with any other code is a permissions error and must NOT be reported as a dead
  token (`_looks_like_bad_token` draws that line).
- **Rate limit is per token** = per user. 100/min on most plans.
- **The v2 spec has wrong types** — `time_spent`/`time_estimate` are declared
  `string|null` but come back as integers. Don't codegen models from it.
- **"Team" means Workspace** in the v2 API. Tools expose it as `workspace_id`;
  paths use `team_id`. `_common.client_and_workspace()` does the mapping and fills
  in the default when the user has exactly one Workspace.
- **User Groups are also called "Teams"** in ClickUp's UI. Different thing. See
  `tools/groups.py`.
- **Folderless Lists still carry a `folder` object** — a placeholder
  `{"name": "hidden", "hidden": true}`, not an omission. Read it naively and you
  report a folder named "hidden"; that was 54 of the first 100 live tasks. Use
  `transform._folder_name()`, which checks the flag.
- **A task's `space` has only an id**, no name — hence the `space_id` key.
- **Task payloads are ~11 KB each.** One 100-task page measured 1.12 MB raw versus
  41 KB summarized. `raw=True` on a list query is a context-window hazard; say so
  when a tool offers it.

## Adding a tool

```python
from clickup_mcp.app import tool
from clickup_mcp.context import current_client

@tool(phase=1, destructive=False, admin=False)
async def do_something(task_id: str, raw: bool = False) -> dict:
    """One-line summary the model reads first.

    Args:
        task_id: ClickUp Task id.
        raw: Return ClickUp's full response.
    """
    client = await current_client()
    payload = await client.get(f"/v2/task/{require_id(task_id, 'task_id')}")
    return payload if raw else summarize_task(payload)
```

Rules:

1. Use `@tool(...)` from `app`, never `@mcp.tool()` directly — the metadata is what
   `policy.py` gates on.
2. Get the client from `current_client()` or `client_and_workspace()`. There is no
   global client.
3. Summarize by default, offer `raw: bool = False`. ClickUp task objects are
   enormous and a 100-task page will blow the context window.
4. Anything destructive gets `destructive=True` **and** `confirm: bool = False` with
   `require_confirm()`, and a docstring that says what cascades.
5. Docstrings are the model's only documentation. Say when *not* to use the tool
   (e.g. "use `search_tasks` instead of walking the hierarchy").
6. Errors should say what to do next. "Do not retry" when retrying cannot help.

Register the module in `tools/__init__.py` under its phase.

## Categorization

`tests/test_categorization.py` pins all of this — read it before changing a flag.

### Phase — what a tool is *for*, never which API version serves it

`CLICKUP_TOOL_PROFILE` controls what is advertised, not what exists. `core` =
phases 1–2, `full` = all 150. Past ~60–80 tools, model tool selection measurably
degrades, so the default profile has to stay the everyday surface.

| Phase | Content | Count |
|---|---|---|
| 1 | Hierarchy, tasks, comments, custom fields, template listing, move-task | 49 |
| 2 | Time tracking, tags, checklists, dependencies, members, attachments, view reading | 40 |
| 3 | Docs, Chat, entity attachments, ACLs | 31 |
| 4 | View authoring, goals, webhooks, users, guests, groups, audit logs | 30 |

Two rules that are easy to violate:

- **A tool must be reachable together with whatever it depends on.**
  `create_task_from_template` is useless without `list_task_templates`; they were
  split across phases 1 and 4, so the default profile could create from a template
  but never discover one. The test enforces `lister.phase <= creator.phase`.
- **File by domain, not by API version.** `move_task_to_list` and
  `update_time_estimates_by_user` live in `v3_tasks.py` because ClickUp only
  exposes them on v3, but they are core task work and time tracking — phases 1
  and 2, not 3.

### destructive — irreversible loss, or a cascading delete

Not "uses the DELETE verb". Removing something re-addable — a dependency, a task
link, a time-entry tag, one guest's access to one item — is **not** destructive;
flagging it hides an ordinary workflow tool behind an admin env var for no safety
gain. `merge_tasks` *is* destructive despite being a POST, because the source
Tasks are consumed and there is no unmerge.

Every destructive tool must also take `confirm: bool = False`, and no
non-destructive tool may take one — a `confirm` on a harmless tool trains the
model to pass `confirm=True` reflexively.

### MCP annotations — how the *client* categorizes

Phase/destructive/admin are server-side only; they never reach the client. Clients
group and present tools using the standard `ToolAnnotations`, and a server that
sets none of them gets every tool filed under "Other". The `@tool` decorator emits:

| Annotation | Source |
|---|---|
| `title` | derived from the function name (`search_tasks` → "Search Tasks"), overridable |
| `readOnlyHint` | derived — true when the function issues no mutating verb |
| `destructiveHint` | mirrors the `destructive` gate |
| `idempotentHint` | derived — true when only GET/PUT/PATCH/DELETE are issued |
| `openWorldHint` | always true; everything talks to ClickUp |
| `_meta["clickup/domain"]` | per-module grouping label (Tasks, Hierarchy, Chat, Admin, …) |

`readOnlyHint`/`idempotentHint` are inferred by AST-walking each function for the
`client.<verb>(...)` calls it makes, so they cannot drift from the implementation.
Override only where the verb misleads — `query_audit_logs` is a POST that modifies
nothing. A tool that issues *no* request (`whoami`) is read-only and idempotent;
the subset tests are written so the empty set gives that answer.

`tests/test_annotations.py` verifies every claimed `readOnlyHint` against the verbs
actually issued — a false one is a safety claim a client may act on.

### admin — restricted to `CLICKUP_ADMIN_EMAILS`

Member/guest management, groups, webhooks, ACLs, audit logs. Admin tools must not
sit in phases 1–2, or the default profile would advertise something the gate then
refuses.

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

`respx` mocks the ClickUp API. `conftest.py` sets the env vars `runtime.py` requires
*before* import, because `runtime.py` calls `sys.exit()` on missing config.

When asserting "a secret is not in the database file", checkpoint WAL first —
`_db_bytes()` in `test_store.py` does this. Without it the rows sit in the `-wal`
sidecar and the assertion passes for the wrong reason.

## Two traps this codebase already fell into

**1. `requirements.lock` must be generated for Linux, not Windows.** `pip-compile`
resolves for the host platform, so running it on Windows pins `pywin32`, which does
not exist on the Linux image and fails the Docker build. Regenerate with:

```powershell
docker run --rm -v "${PWD}:/w" -w /w python:3.12.10-slim `
  sh -c "pip install -q pip-tools && pip-compile --quiet --strip-extras --output-file requirements.lock pyproject.toml"
```

**2. FastMCP's `lifespan=` argument is not a process lifespan.** It is wired into
the low-level MCP *session*. `streamable_http_app()` hardcodes
`lifespan=lambda app: self.session_manager.run()` and ignores it at the ASGI level.
Passing startup work there means it never runs for custom routes — `/clickup-callback`
would meet an unconnected store and the whole OAuth flow would fail, with every unit
test still green because the fixtures call `store.connect()` themselves.

The fix is in two places, and both should stay:
`GovernedFastMCP.streamable_http_app()` wraps the real ASGI lifespan, and
`Store._ensure_connected()` connects lazily so any other entry point is safe
regardless. `tests/test_lifecycle.py` guards both.

## Known gaps

- Live verification so far covers the OAuth flow and the Phase 1 read path
  (workspaces, spaces, folders, lists, task search) against one real Workspace.
  Write operations, Phase 2–4 tools, and multi-user isolation with real data are
  still only covered by mocks.
- `update_view` replaces the whole view definition; the tool documents the
  read-modify-write requirement but does not enforce it.
- Attachment upload accepts text or base64 (10 MB cap) because MCP tool calls carry
  JSON, not binary. ClickUp has no attachment-delete endpoint.

## Verified in the container

Image builds on `python:3.12.10-slim`, runs as uid 1000, and serves:
`/health` reports `ok`, OAuth discovery returns the metadata document, dynamic
client registration persists, `/authorize` 302s to `app.clickup.com` with no PKCE
or scope parameters, unauthenticated `/mcp` returns 401, and the SQLite store
survives a container restart.
