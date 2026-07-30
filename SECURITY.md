# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please **do not open a public GitHub issue**.

Instead, report it privately by opening a [GitHub Security Advisory](https://github.com/npab19/clickup-mcp/security/advisories/new).

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

You can expect an initial response within 48 hours.

## Scope

This project is a self-hosted, **multi-user** MCP server. The following are in scope:

- **Cross-user data exposure** — any path by which one authenticated user can read
  or modify another user's ClickUp data. This is the highest-severity class here.
- Authentication or authorization bypass, including the tool-gating layer
- OAuth token leakage, or recovery of ClickUp tokens from the store
- Injection vulnerabilities in MCP tool handlers
- Container escape or privilege escalation
- Credential exposure in logs, error messages, or the audit trail

## Out of Scope

- Vulnerabilities in the upstream ClickUp API itself
- Issues requiring physical access to the host machine
- The fact that a ClickUp admin can act as an admin — this server delegates to
  ClickUp's own permission model by design

## Security model

**The real boundary is ClickUp's own permissions.** Every API call is made with the
calling user's OAuth token, so the server cannot do anything that user could not do
in the ClickUp UI. The layers below are defence in depth and blast-radius limits,
not the primary control.

### Multi-user isolation

The MCP access token on each request maps to exactly one ClickUp grant. Three things
are namespaced by grant id, and all three are covered by `tests/test_isolation.py`:

- the HTTP response cache (an un-namespaced cache is a cross-user data leak)
- the per-identity rate-limit bucket
- the audit trail

### Secrets at rest

| Secret | Storage | Why |
|---|---|---|
| MCP access / refresh tokens, auth codes | SHA-256 hash | Only ever need to be recognised, never reproduced |
| ClickUp access tokens | Fernet-encrypted (`TOKEN_ENCRYPTION_KEY`) | Must be replayed upstream on every call |

**ClickUp access tokens never expire and ClickUp issues no refresh token.** A leaked
store therefore grants indefinite access until each user manually revokes the
integration in ClickUp. Treat the `clickup-data` volume as a secret, back it up
encrypted, and rotate `TOKEN_ENCRYPTION_KEY` only when you are prepared to have
every user re-authorize (the server detects undecryptable rows and prompts re-auth
rather than failing).

The database file is created `0600` and the container runs as a non-root user.

### Transport

`SERVER_URL` must be HTTPS; the process refuses to start otherwise, except on
localhost for development. Deploy behind the Cloudflare Tunnel in
`docker-compose.yml` rather than exposing port 8000.

### Destructive operations

Off by default (`CLICKUP_ENABLE_DESTRUCTIVE=false`). When enabled, every destructive
tool additionally requires an explicit `confirm=True`. ClickUp deletes cascade —
deleting a Space destroys its Folders, Lists, and Tasks — and there is no undo
through the API.

### Admin surface

`CLICKUP_ADMIN_EMAILS` gates 20 tools covering member management, guest access,
webhooks, ACLs, and audit logs. An **empty** value means nobody, not everybody.

### Logging

Logs carry ClickUp user ids and grant ids, never tokens. Audit arguments are
redacted for keys matching `token`, `access_token`, `password`, `secret`, `api_key`.
Registered MCP client ids are logged by 12-character prefix only.
