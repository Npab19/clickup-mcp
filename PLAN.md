# ClickUp MCP Server — Build Plan

## 0. What I reviewed

**Existing MCP servers in `Git/MCP/`** (13 projects) and the pattern guide at
`General.Claude.md`, which documents four established patterns:

| Pattern | Stack | Auth | Examples |
|---|---|---|---|
| A | TypeScript | API key | Datto BCDR, **CW Manage 3** |
| B | Python + FastMCP | **User OAuth delegation** | Fitbit, Withings, **Whoop** |
| C | Python + FastMCP | Bearer-token middleware + static key | Everhour, YNAB, Ninja, Hudu4, Mailprotector |
| D | TypeScript + Express | User OAuth 2.1 | Oura, Spotify |

**Decision: Pattern B (Python + FastMCP).** Two servers are the reference:

- **Whoop** — the OAuth-delegation mechanics, client, transform, and Docker layout.
- **CW Manage 3** — the *multi-user* identity, policy, audit, and rate-limit layer.
  It is filed under Pattern A but has outgrown that label (see §3).

ClickUp is the first server that needs **both**: per-user upstream tokens (Whoop's
OAuth delegation, extended) *and* per-identity governance (CW Manage 3's stack).

Whoop's file layout, which ClickUp should mirror:

```
src/whoop_mcp/
  app.py             # FastMCP instance + AuthSettings + provider wiring
  server.py          # entrypoint, OAuth callback route, /health
  oauth_provider.py  # OAuthAuthorizationServerProvider implementation
  client.py          # httpx client: token mgmt, retry, cache, rate-limit logging
  constants.py       # URLs + scopes, single source of truth
  logging_setup.py   # structured logging
  transform.py       # response slimming (critical for context budget)
  validation.py      # input validation helpers
  resources.py       # MCP resources
  tools/*.py         # one module per API domain, each imports `mcp` and `client`
Dockerfile           # python:3.12-slim, non-root appuser, /data volume
docker-compose.yml   # app + pinned cloudflared tunnel
requirements.lock    # fully pinned, pip-compile style
.github/workflows/docker-publish.yml  # GHCR publish
```

---

## 1. Finding #1 — the spec you linked is only ~20% of the API

`ClickUp_PUBLIC_API_V3.yaml` contains **23 paths / 35 operations**, covering only:

- Chat (channels, messages, replies, reactions) — 19 ops
- Docs (search, create, pages) — 8 ops
- Audit logs — 1 op (Enterprise only)
- Attachments by parent entity, ACLs, `moveTask`, `time_estimates_by_user` — 7 ops

**Tasks, Lists, Folders, Spaces, Time Tracking, Comments, Custom Fields, Goals,
Views, Webhooks, Tags, Checklists, Members, and Guests are all v2 only.**

The v2 spec — **82 paths / 137 operations** — is at:

```
https://developer.clickup.com/openapi/clickup-api-v2-reference.json
```

Both are downloaded into this directory. The build must target **both versions**.
Base URLs are compatible: one httpx client on `https://api.clickup.com/api`
serves `/v2/...` and `/v3/...` paths.

v2 operation counts by tag:

```
Time Tracking 13 | Views 12 | Lists 11 | Comments 10 | Guests 10 | Tasks 10
Goals 8 | Checklists 6 | Custom Fields 6 | Folders 6 | Tags 6 | Spaces 5
Task Relationships 4 | User Groups 4 | Time Tracking (Legacy) 4 | Users 4
Webhooks 4 | Workspaces 3 | Templates 3 | Members 2 | Attachments 1
Roles 1 | Shared Hierarchy 1 | Custom Task Types 1
```

> Known upstream defect: the v2 spec mis-types several task fields
> (`time_spent`, `time_estimate` declared `string|null`, API returns integers).
> Do not code-generate models blindly from it — hand-write the transform layer.

---

## 2. Finding #2 — ClickUp OAuth differs materially from Whoop/Fitbit/Withings

| | Whoop (reference) | **ClickUp** |
|---|---|---|
| Authorize URL | `.../oauth/oauth2/auth` | `https://app.clickup.com/api` |
| Token URL | `.../oauth/oauth2/token` | `https://api.clickup.com/api/v2/oauth/token` |
| Token params | form-encoded body | `client_id`, `client_secret`, `code` in body |
| PKCE to provider | ✅ sent | ❌ **not supported** — must be removed |
| Scopes | 7 explicit scopes | ❌ **none** — user picks Workspaces on consent screen |
| Access token TTL | 3600s | **never expires** ("subject to change") |
| Refresh token | ✅ rotating | ❌ **none issued** |
| API auth header | `Bearer {token}` | `Bearer {token}` (OAuth) — note `pk_` personal tokens use no prefix |
| Rate limit | global | **100 req/min per token** (Free/Unlimited/Business), 1 000 (Business Plus), 10 000 (Enterprise) — headers `X-RateLimit-Limit`/`-Remaining`/`-Reset`, HTTP 429 |

**Consequences for the port:**

1. Delete the PKCE-to-provider block in `oauth_provider.authorize()`. PKCE is still
   used *between Claude and our server* — that stays.
2. Delete `_refresh_access_token()` / `_ensure_token()` from the client. There is
   nothing to refresh. A 401 means the user revoked the app → surface a
   **re-authenticate** message instead of retrying.
3. Because tokens never expire, a leaked token store is **permanent** access.
   Encryption at rest is not optional here (see §3.4).
4. Rate limiting is **per token → per user**, which is good: one heavy user cannot
   starve the others. Track budget per user, not globally.

---

## 3. Finding #3 — multi-user: half of it already exists, in CW Manage 3

Two different things get called "multi-user", and the estate already solves one:

| | upstream credential | caller identity | servers |
|---|---|---|---|
| **Single-user OAuth** | one user's token, global singleton | none | Whoop, Withings, Fitbit |
| **Shared-credential multi-tenant** | one shared API key | OAuth sub → DB-mapped member → per-identity policy | **CW Manage 3** |
| **Per-user OAuth multi-tenant** | *each caller's own token* | same | **ClickUp (new)** |

Whoop/Withings/Fitbit hold **one global token set** in the module-level `client`
singleton, persisted to `/data/.x_tokens.json` — whoever authenticates last owns the
server. Fine for personal health data, wrong for a team.

**CW Manage 3 is already genuinely multi-user** and is the closer precedent for
governance. `src/middleware/` contains a complete stack worth porting:

| file | does |
|---|---|
| `identity-resolver.ts` | OAuth `sub`/`email` → `user_mappings` → CW member + security role → `ResolvedPolicy` (allowed tools, field projections), admin detection |
| `policy-gate.ts` | Proxies the MCP server so disallowed tools **never enter the registry** — `tools/list` omits them entirely. Also wraps handlers for field redaction + TTL'd context injection |
| `audit-capture.ts` | Persists every `tools/call` (tool, arguments, status, duration, request id) to the DB |
| `rate-limit.ts` | Token-bucket per identity, separate capacities for users vs service accounts, DB-tunable |
| `service-account-auth.ts` | `sa_<prefix>_<secret>` keys for non-interactive callers, bypassing the JWT path |
| `request-context.ts` | Request id + structured JSON access log incl. `authSub`/`authEmail` |

ClickUp inherits that layer. What it does **not** inherit — and what is genuinely
new — is that the upstream credential is per-caller rather than shared. Four changes:

### 3.1 Per-request identity resolution

Verified available in the installed SDK (`mcp` 1.26.0):

```python
from mcp.server.auth.middleware.auth_context import get_access_token
# returns the AccessToken for the *current* request, or None
```

Tools stop reading a global token. Instead:

```python
# clickup_mcp/context.py
async def current_client() -> ScopedClickUpClient:
    token = get_access_token()
    if token is None:
        raise ClickUpAuthError("Not authenticated.")
    grant = await store.get_grant(token.token)
    if grant is None:
        raise ClickUpAuthError("ClickUp authorization not found — re-authenticate.")
    return ScopedClickUpClient(grant)
```

One shared `httpx.AsyncClient` (connection pooling) — the per-user part is only the
`Authorization` header, the cache namespace, and the rate-limit budget.

### 3.2 Binding the ClickUp token to the MCP token

The chain that must be maintained through the OAuth dance:

```
1. Claude → GET /authorize                    (MCP OAuth + PKCE from Claude)
2. provider stores pending_auth[state]
3. redirect → https://app.clickup.com/api?client_id=…&redirect_uri={SERVER_URL}/clickup-callback&state={state}
4. ClickUp → GET /clickup-callback?code=X&state=…
5. POST /api/v2/oauth/token                   → clickup_access_token
6. GET /api/v2/user with that token           → ClickUp user id + email  (identity)
7. mint mcp_code;  bind  mcp_code → (clickup_token, user_id, email)
8. redirect back to Claude with mcp_code
9. Claude → POST /token  → exchange_authorization_code
      mint mcp_access_token + mcp_refresh_token
      REBIND: mcp_access_token → grant,  mcp_refresh_token → grant   ← new
10. tool call → get_access_token().token → grant → ClickUp API
```

Step 9's rebind is what Whoop's provider does not do. `exchange_refresh_token()`
must carry the same binding onto the newly-minted pair, or every MCP token refresh
silently de-authenticates the user.

Dedupe grants by ClickUp `user.id` so a re-authorization replaces rather than
accumulates rows.

### 3.3 Persistent OAuth state (SQLite, replacing in-memory dicts)

Whoop keeps `_clients`, `_auth_codes`, `_mcp_tokens`, `_mcp_refresh_tokens` in
plain dicts. Restart the container and **every user must re-register and re-auth**.
Acceptable for one user; not for a team.

`store.py` — SQLite via `aiosqlite` on the `/data` volume:

| table | purpose |
|---|---|
| `oauth_clients` | dynamic client registrations (`OAuthClientInformationFull`) |
| `auth_codes` | short-lived MCP codes, 300s TTL, deleted on exchange |
| `access_tokens` | MCP access tokens → client_id, scopes, expires_at |
| `refresh_tokens` | MCP refresh tokens → client_id, scopes |
| `clickup_grants` | ClickUp user_id, email, **encrypted** access token, workspaces, created_at |
| `token_grants` | join: mcp token/refresh/code → clickup_grants.id |

Background sweep for expired codes and access tokens.

### 3.4 Encryption at rest + cache isolation

- ClickUp tokens encrypted with `cryptography.fernet`, key from
  `TOKEN_ENCRYPTION_KEY` env (fail fast at startup if absent).
  `cryptography` is already a transitive dep via `pyjwt` in the existing lockfiles.
- Whoop's response cache is keyed `(path, params)`. Copying that verbatim into a
  multi-user server **leaks one user's tasks to another**. Cache key must be
  `(grant_id, path, params)`, with per-grant eviction.
- Same for the rate-limit tracker: keyed per grant.
- Log ClickUp user ids, never tokens or emails at INFO.

### 3.5 Governance layer (ported from CW Manage 3) — required by the full-CRUD decision

Full CRUD including deletes is in scope, over a shared server, across a team. The
mitigations are already written in TypeScript in CW Manage 3 and need porting to
Python/FastMCP:

- **`policy.py`** — port of `policy-gate.ts`. FastMCP has no `.tool()` proxy hook,
  so instead gate at the `list_tools`/`call_tool` layer: a per-identity allowed-tool
  set filters the advertised list and rejects the call. Destructive tools
  (`delete_task`, `delete_list`, `delete_space`, `delete_folder`, `delete_goal`,
  `delete_view`, `delete_webhook`, `remove_user_from_workspace`) sit behind an
  explicit opt-in, off by default.
- **`audit.py`** — port of `audit-capture.ts`. Every `tools/call` recorded with
  ClickUp user id, tool, arguments, status, duration, request id. With deletes
  enabled this is the only way to answer "who deleted that Space".
- **`rate_limit.py`** — token bucket per grant. Distinct from ClickUp's own
  100/min-per-token ceiling; this one protects the *workspace* from a runaway loop.
- **`request_context.py`** — request id header + structured access log.

**The strongest safeguard is free:** because every call uses the caller's own
ClickUp OAuth token, ClickUp enforces that user's real permissions upstream. The
server cannot do anything the user could not do in the ClickUp UI. That is a
materially better position than CW Manage 3's shared-key model, where the policy
layer *is* the only boundary. Here the policy layer is defence in depth.

Still required regardless: destructive tools carry explicit `confirm: bool = False`
parameters and blunt "this permanently deletes X and all its children" docstrings,
since ClickUp deletes of Spaces/Folders/Lists cascade.

---

## 4. Tool surface

**Scope decision: all four phases, full CRUD including deletes.** That is ~172
operations, landing at roughly **130–140 tools** after merging near-duplicates
(e.g. one `create_list` covering foldered + folderless).

**This is a real problem and needs saying plainly:** past ~60–80 tools, model tool
selection measurably degrades — more mis-picks, more context spent on the tool list
alone. The estate's largest server (Hudu4) has 29 tool modules and is already at the
edge. 140 flat tools will make the server worse at the common cases.

Two mitigations, both reusing §3.5:

1. **`CLICKUP_TOOL_PROFILE` env** — `core` (Phase 1+2, ~50 tools) / `full` (all).
   Full capability stays *built*; what is *advertised* is configurable.
2. **Per-identity gating** — the `policy-gate.ts` port already filters `tools/list`
   per identity, so an admin sees the admin surface and a normal user does not.

Recommendation: build all four phases as specified, ship `core` as the default
profile, and let `full` be opt-in per deployment or per identity. Nothing is cut —
this only changes what a given caller is shown.

Tools marked 🔴 are destructive/cascading and gated per §3.5.

### Phase 1 — core hierarchy + tasks (v2), ~40 tools — profile `core`
| module | tools |
|---|---|
| `auth.py` | `whoami`, `list_authorized_workspaces` |
| `workspaces.py` | plan, seats, custom roles, custom task types, shared hierarchy |
| `spaces.py` | list / get / create / update / 🔴 delete |
| `folders.py` | list / get / create / update / 🔴 delete / create-from-template |
| `lists.py` | list (foldered + folderless) / get / create / update / 🔴 delete, add+remove task, create-from-template |
| `tasks.py` | `search_tasks` (**`GetFilteredTeamTasks` — the workhorse**), get, list-by-list, create, update, 🔴 delete, merge, time-in-status, bulk-time-in-status, create-from-template |
| `comments.py` | task/list/view comments, threaded replies, create, update, 🔴 delete |
| `custom_fields.py` | accessible fields (list/folder/space/team), set value, remove value |

### Phase 2 — time tracking + collaboration, ~30 tools — profile `core`
`time_tracking.py` (13 v2 ops incl. start / stop / current / 🔴 delete entry +
4 legacy `/task/{id}/time` ops), `tags.py` (6, incl. 🔴 delete space tag),
`checklists.py` (6, incl. 🔴 deletes), `task_relationships.py` (dependencies +
links, 4), `members.py` (2), `attachments.py` (multipart — the only non-JSON
endpoint, needs its own client path).

### Phase 3 — v3 Docs + Chat, ~25 tools — profile `full`
`docs.py` (search / create / get / page-listing / pages / create-page / edit-page),
`chat.py` (channels CRUD incl. 🔴 delete, DM + location channels, messages, replies,
reactions, followers, members, tagged users — 19 ops),
`v3_tasks.py` (`moveTask`, time-estimates-by-user replace/patch),
`v3_attachments.py` (entity attachments, ACL patch).

### Phase 4 — admin, ~35 tools — profile `full`
`views.py` (12, incl. 🔴 delete), `goals.py` (8, incl. 🔴 deletes),
`webhooks.py` (4, incl. 🔴 delete), `templates.py` (3),
`guests.py` (10, incl. 🔴 remove-from-workspace),
`users.py` (4, incl. 🔴 remove-user-from-workspace),
`groups.py` (4, incl. 🔴 delete), `audit_logs.py` (Enterprise only).

### Response slimming is mandatory
ClickUp task objects are enormous (full status arrays, all custom fields, all
watchers, nested list/folder/space objects). A 100-task response will blow the
context window. `transform.py` mirrors Whoop's approach:

- `summarize_task()` → id, custom_id, name, status, assignees (names only),
  due_date, priority, list/folder/space names, url
- every tool takes `raw: bool = False` to bypass slimming
- collection tools cap `limit` and surface `last_page` / pagination cursors

### Server instructions
Follow Mailprotector's `SERVER_INSTRUCTIONS` model — a substantial docstring on the
`FastMCP` instance teaching the model ClickUp's hierarchy
(Workspace → Space → Folder → List → Task), that IDs are opaque strings, that
`search_tasks` beats walking the tree, and which operations are destructive.

---

## 5. Deployment

Identical to Whoop:

```yaml
services:
  clickup-mcp:
    build: .
    environment:
      - CLICKUP_CLIENT_ID / CLICKUP_CLIENT_SECRET
      - SERVER_URL                # public https:// — enforced non-localhost at startup
      - TOKEN_ENCRYPTION_KEY
      - CLICKUP_DB_PATH=/data/clickup.db
      - CLICKUP_TOOL_PROFILE=core       # core | full
      - CLICKUP_ENABLE_DESTRUCTIVE=false
      - CLICKUP_ADMIN_EMAILS            # identities allowed the admin surface
    expose: ["8000"]
    volumes: [clickup-data:/data]
    deploy: { resources: { limits: { cpus: '1' } } }
    restart: unless-stopped
  cloudflared:
    image: cloudflare/cloudflared:2026.3.0   # pinned per convention
```

ClickUp app registration must list **`{SERVER_URL}/clickup-callback`** as an exact
redirect URI. `/health` reports process status plus authorized-grant count (no PII).

---

## 6. Build order

1. Scaffold: `pyproject.toml`, `requirements.lock`, `Dockerfile`, compose, CI, licence, `.gitignore`
2. `constants.py` + `store.py` (SQLite + Fernet) + tests for the store
3. `oauth_provider.py` — Whoop's, minus provider-PKCE, plus grant binding, backed by `store`
4. `client.py` — `ScopedClickUpClient`, per-grant cache + rate limit, 429/5xx retry, no refresh path
5. `app.py` / `server.py` / `logging_setup.py` — callback route, `/health`
6. **🚩 GATE: end-to-end auth test with two different ClickUp users, asserting user A
   cannot read user B's tasks and that the cache does not cross-serve.** No tools
   until this passes.
7. Governance layer (§3.5): `policy.py`, `audit.py`, `rate_limit.py`, `request_context.py`
8. `transform.py` + `validation.py`
9. Phase 1 → Phase 2 (profile `core` shippable here) → Phase 3 → Phase 4
10. `README.md`, `SECURITY.md`, `CLAUDE.md`

Step 6 is the gate — the multi-user auth is the only part that can be
architecturally wrong; everything downstream is mechanical. Step 7 lands before any
destructive tool exists, so no delete is ever reachable without audit + gating.

---

## 7. Decisions taken

| | |
|---|---|
| **Stack** | Python + FastMCP (Pattern B) |
| **Write scope** | Full CRUD **including deletes**, gated per §3.5 and default-off |
| **Coverage** | All four phases (v2 + v3, ~172 operations) |
| **Default profile** | `core` (Phases 1–2); `full` opt-in per deployment or identity |

### Flagged for your call later (not blocking)

- **Tool-count ergonomics** (§4). 130–140 flat tools will degrade tool selection.
  The profile + per-identity gating is my mitigation; if you would rather cut
  surface outright, Phase 4's `guests.py`/`groups.py`/`views.py` are the least-used.
- **`GetAuthorizedTeams` vs `team_id` plumbing.** Nearly every v2 endpoint needs a
  workspace id. Worth caching the caller's authorized workspaces on the grant at
  auth time and defaulting `team_id` when the user has exactly one.
- **Audit DB.** CW Manage 3 audits to Postgres (`DATABASE_URL`). Plan here is the
  same SQLite file as the token store — simpler, but if you want the ClickUp audit
  trail alongside the CW one, point both at Postgres instead.
