# ClickUp MCP Server

[![Build](https://github.com/Npab19/clickup-mcp/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Npab19/clickup-mcp/actions/workflows/docker-publish.yml)
[![Image](https://img.shields.io/badge/ghcr.io-npab19%2Fclickup--mcp-blue?logo=docker)](https://github.com/Npab19/clickup-mcp/pkgs/container/clickup-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A multi-user MCP server for ClickUp. Each person who connects authorizes with their
own ClickUp account, and every API call runs against **their** OAuth token — so
ClickUp enforces their real permissions and no one can see anything through this
server that they could not see in the ClickUp UI.

Covers **150 tools** across ClickUp API v2 and v3: the Workspace hierarchy, tasks,
comments, custom fields, time tracking, tags, checklists, dependencies,
attachments, Docs, Chat, views, goals, templates, webhooks, and Workspace admin.

---

## Why this one is different from the other servers in this estate

The OAuth servers here (Whoop, Withings, Fitbit) hold **one** token set in a
module-level singleton — whoever authenticates last owns the server. That is fine
for personal health data and wrong for a team on ClickUp.

This server keeps a **per-user grant**. The MCP access token presented on each
request is mapped to exactly one ClickUp authorization:

```
Claude → /authorize → app.clickup.com consent → /clickup-callback
      → ClickUp token + GET /v2/user (identity) → grant row (encrypted)
      → MCP code → MCP access + refresh token, both bound to that grant
      → tool call → get_access_token() → grant → ClickUp API
```

The response cache, the rate-limit bucket, and the audit trail are all namespaced
by grant id.

---

## Quick start

1. **Create a ClickUp OAuth app** at
   <https://app.clickup.com/settings/apps>. Set the redirect URL to exactly
   `{SERVER_URL}/clickup-callback`.

2. **Configure**

   ```bash
   cp .env.example .env
   # generate a key for the token store
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Fill in `CLICKUP_CLIENT_ID`, `CLICKUP_CLIENT_SECRET`, `SERVER_URL`,
   `TOKEN_ENCRYPTION_KEY`, and `CLOUDFLARE_TUNNEL_TOKEN`.

3. **Run** — either build from source:

   ```bash
   docker compose up -d --build
   curl https://your-host/health
   ```

   ...or run the published image, which is what `docker-compose.yml` points at
   by default:

   ```bash
   docker compose pull && docker compose up -d
   ```

   Published to `ghcr.io/npab19/clickup-mcp` for `linux/amd64` and `linux/arm64`
   on every push to `main`, and pullable without authentication. Pin a specific
   build with `CLICKUP_IMAGE_TAG=sha-<commit>` in `.env`.

   Because the compose file declares both `image:` and `build:`, `up` will use a
   locally-built image if one is tagged — run `docker compose pull` to fetch the
   published one, or `docker compose up -d --build` to force a local build.

4. **Connect** your MCP client to `https://your-host/mcp`. It will walk you through
   the ClickUp consent screen on first use. Each additional user repeats step 4 and
   gets their own grant.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CLICKUP_CLIENT_ID` | — | **Required.** OAuth app client id. |
| `CLICKUP_CLIENT_SECRET` | — | **Required.** OAuth app client secret. |
| `SERVER_URL` | `http://localhost:8000` | **Required in production.** Must be HTTPS; the process refuses to start on a non-HTTPS non-localhost URL. |
| `TOKEN_ENCRYPTION_KEY` | — | **Required.** Fernet key encrypting ClickUp tokens at rest. |
| `CLICKUP_DB_PATH` | `/data/clickup.db` | SQLite store. |
| `CLICKUP_ACCESS_TOKEN_TTL` | `86400` | Seconds an MCP access token stays valid. |
| `CLICKUP_REFRESH_GRACE` | `300` | Seconds a rotated refresh token keeps working. |
| `CLICKUP_TOOL_PROFILE` | `core` | `core` = phases 1–2 (89 tools). `full` = all 150. |
| `CLICKUP_ENABLE_DESTRUCTIVE` | `false` | Expose the 20 tools that irreversibly delete content. |
| `CLICKUP_ADMIN_EMAILS` | *(empty)* | Comma-separated ClickUp emails allowed the 20 admin tools. Empty means **nobody**. |
| `CLICKUP_RATE_CAPACITY` | `60` | Per-user token bucket size. |
| `CLICKUP_RATE_REFILL_PER_MINUTE` | `60` | Per-user refill rate. |

### On the tool profile

150 flat tools measurably degrades a model's tool selection — past roughly 60–80,
mis-picks rise and the tool list alone eats context. Everything is *built*; the
profile controls what is *advertised*. `core` is the recommended default and covers
the daily surface: hierarchy, tasks, comments, custom fields, time tracking, tags,
checklists, dependencies, attachments.

---

## Tools by phase

| Phase | Content | Tools |
|---|---|---|
| **1** — core hierarchy + tasks | workspaces, spaces, folders, lists, tasks, comments, custom fields, template listing, move-task | 49 |
| **2** — time tracking + collaboration | time entries, tags, checklists, dependencies, members, attachments, view reading | 40 |
| **3** — API v3 | Docs, Chat, entity attachments, ACLs | 31 |
| **4** — admin | view authoring, goals, webhooks, users, guests, groups, audit logs | 30 |

Tools are filed by **what they are for**, not by which API version serves them —
`move_task_to_list` is v3-only but is core task work, so it sits in phase 1.

`search_tasks` (ClickUp's `GetFilteredTeamTasks`) is the workhorse — one call
against a whole Workspace with filters, instead of walking
Spaces → Folders → Lists → Tasks.

---

## Safety model

Four independent layers, each fail-closed:

1. **ClickUp's own permissions.** The real boundary. Every call carries the
   caller's token, so the server cannot exceed what that person can do in the UI.
2. **Tool gating** (`policy.py`). Phase, destructive, and admin gates decide what
   each caller is shown. A hidden tool is neither advertised nor callable.
3. **Confirmation guards.** Every destructive tool takes `confirm: bool = False`
   and refuses without it. ClickUp deletes cascade — a Space takes its Folders,
   Lists, and Tasks with it.
4. **Audit** (`audit.py`). Every `tools/call` is recorded with the acting ClickUp
   user, arguments, outcome, and duration.

Secrets: MCP tokens are stored as SHA-256 hashes; ClickUp tokens are Fernet-encrypted
because they must be replayed upstream. ClickUp access tokens **never expire**, so a
leaked store is permanently valuable — treat the `clickup-data` volume as a secret.

---

## ClickUp API notes

Things that differ from a typical OAuth integration, all verified against the live
API:

- **No PKCE, no scopes.** ClickUp's authorize endpoint is `https://app.clickup.com/api`
  and supports neither. The user picks which Workspaces to grant on the consent
  screen. PKCE between your MCP client and this server is unaffected.
- **Tokens never expire and no refresh token is issued.** There is no refresh path
  in `client.py` by design. A 401 means the user revoked the integration; the only
  recovery is re-authorization, so the server says so instead of retrying.
- **Rate limits are per token**, i.e. per user: 100 req/min on Free, Unlimited, and
  Business; 1,000 on Business Plus; 10,000 on Enterprise.
- **The v3 spec is only a fragment** — Chat, Docs, and audit logs. Tasks, Lists,
  Spaces, time tracking, and everything else are v2 only. Both specs are vendored in
  this repo.

---

## Development

```bash
pip install -e ".[dev]"
pytest -q          # 367 tests
```

`tests/test_isolation.py` is the gate: it proves the response cache cannot serve one
user's data to another, that each request carries the right token, and that an MCP
token refresh preserves its ClickUp binding. Run it before touching `store.py`,
`client.py`, or `oauth_provider.py`.

**Regenerating `requirements.lock`** — do it in a Linux container, not on Windows.
`pip-compile` resolves for the host platform and will otherwise pin `pywin32`,
which breaks the Docker build:

```powershell
docker run --rm -v "${PWD}:/w" -w /w python:3.12.10-slim `
  sh -c "pip install -q pip-tools && pip-compile --quiet --strip-extras --output-file requirements.lock pyproject.toml"
```

## Layout

```
src/clickup_mcp/
  runtime.py         # singletons + startup config checks
  store.py           # SQLite: clients, codes, tokens, grants, audit
  oauth_provider.py  # OAuth 2.1 server delegating to ClickUp
  client.py          # per-grant HTTP client, cache, rate-limit handling
  context.py         # MCP token -> ClickUp grant (the multi-user hinge)
  policy.py          # per-identity tool gating
  audit.py           # tool-call audit trail
  rate_limit.py      # per-identity token bucket
  transform.py       # response slimming
  validation.py      # input coercion + confirm guard
  app.py             # FastMCP instance + @tool decorator
  server.py          # callback route, /health, entrypoint
  tools/             # 20 modules, 150 tools
```
