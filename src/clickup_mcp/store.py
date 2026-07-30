"""Persistent OAuth + grant storage.

Whoop/Withings/Fitbit keep OAuth clients, codes, and tokens in module-level dicts,
so a container restart forces every user to re-register and re-authorize. That is
tolerable for a single-user server and not for a shared one, so this is SQLite on
the /data volume instead.

Two different secrets live here and are treated differently:

* **MCP tokens** (access, refresh, auth codes) are bearer credentials this server
  issues. They are stored as SHA-256 hashes — we only ever need to *recognise* one,
  never to reproduce it.
* **ClickUp access tokens** must be replayed upstream on every call, so they are
  encrypted with Fernet rather than hashed. ClickUp tokens never expire, which makes
  a leaked store permanently valuable; encryption at rest is not optional.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from clickup_mcp.constants import DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id   TEXT PRIMARY KEY,
    client_info TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clickup_grants (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    clickup_user_id  TEXT NOT NULL UNIQUE,
    email            TEXT,
    username         TEXT,
    token_enc        BLOB NOT NULL,
    workspaces       TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_auth (
    state      TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash  TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    grant_id   INTEGER NOT NULL REFERENCES clickup_grants(id) ON DELETE CASCADE,
    payload    TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    grant_id   INTEGER NOT NULL REFERENCES clickup_grants(id) ON DELETE CASCADE,
    scopes     TEXT NOT NULL,
    resource   TEXT,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    grant_id   INTEGER NOT NULL REFERENCES clickup_grants(id) ON DELETE CASCADE,
    scopes     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    grant_id        INTEGER,
    clickup_user_id TEXT,
    tool            TEXT NOT NULL,
    arguments       TEXT,
    status          TEXT NOT NULL,
    error           TEXT,
    duration_ms     INTEGER,
    -- Identifiers from the tool's result. Without this the trail answers "who
    -- called create_task" but not "what did it create", which is exactly the
    -- question asked when reconciling the audit against ClickUp.
    result_ref      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(clickup_user_id, ts);
CREATE INDEX IF NOT EXISTS idx_access_grant ON access_tokens(grant_id);
CREATE INDEX IF NOT EXISTS idx_refresh_grant ON refresh_tokens(grant_id);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ClickUpGrant:
    """A single ClickUp user's authorization, with the token already decrypted."""

    id: int
    clickup_user_id: str
    email: str | None
    username: str | None
    access_token: str
    workspaces: list[dict[str, Any]]

    @property
    def label(self) -> str:
        """Safe-to-log identifier — never the token, never the raw email."""
        return f"clickup_user:{self.clickup_user_id}"


class StoreError(RuntimeError):
    pass


class Store:
    """Async SQLite store. One connection guarded by a lock — the workload is
    low-volume and this avoids both connection churn and `database is locked`."""

    def __init__(self, db_path: str | None = None, encryption_key: str | None = None):
        self._db_path = Path(db_path or DB_PATH)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        # Separate from _lock: _ensure_connected() runs *before* operations take
        # _lock, and asyncio.Lock is not reentrant.
        self._connect_lock = asyncio.Lock()

        key = encryption_key or os.environ.get("TOKEN_ENCRYPTION_KEY") or ""
        if not key:
            raise StoreError(
                "TOKEN_ENCRYPTION_KEY is required — ClickUp tokens never expire, so "
                "they are encrypted at rest. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as e:
            raise StoreError(
                "TOKEN_ENCRYPTION_KEY is not a valid Fernet key (44-char urlsafe base64)."
            ) from e

    # --- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Open the database and apply the schema. Idempotent and concurrency-safe."""
        async with self._connect_lock:
            if self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.executescript(_SCHEMA)
            await self._migrate(conn)
            await conn.commit()
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                # chmod is a no-op on Windows volumes — not worth failing startup for.
                pass
            # Publish only once fully initialised, so a concurrent _ensure_connected
            # can never observe a connection without its schema.
            self._conn = conn
            logger.info("Store ready", extra={"db_path": str(self._db_path)})

    @staticmethod
    async def _migrate(conn: aiosqlite.Connection) -> None:
        """Additive column migrations.

        `CREATE TABLE IF NOT EXISTS` leaves an existing table untouched, so a
        deployed database keeps its old shape. Columns added after first release
        have to be applied explicitly.
        """
        async with conn.execute("PRAGMA table_info(audit_log)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if "result_ref" not in columns:
            await conn.execute("ALTER TABLE audit_log ADD COLUMN result_ref TEXT")
            logger.info("Migrated audit_log: added result_ref")

    async def close(self) -> None:
        async with self._connect_lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    async def _ensure_connected(self) -> None:
        """Connect on first use.

        FastMCP's `streamable_http_app()` hardcodes its own ASGI lifespan, and the
        `lifespan=` argument it accepts belongs to the MCP *session*, not the
        process. Custom routes — `/clickup-callback` above all — therefore run
        outside any lifespan we control. `server.py` still connects eagerly at
        startup; this makes every other entry point safe regardless.
        """
        if self._conn is None:
            await self.connect()

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise StoreError("Store.connect() has not been awaited")
        return self._conn

    # --- OAuth client registrations -----------------------------------------

    async def put_client(self, client_id: str, client_json: str) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT INTO oauth_clients (client_id, client_info, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(client_id) DO UPDATE SET client_info=excluded.client_info",
                (client_id, client_json, time.time()),
            )
            await self._db.commit()

    async def get_client(self, client_id: str) -> str | None:
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT client_info FROM oauth_clients WHERE client_id = ?", (client_id,)
            ) as cur:
                row = await cur.fetchone()
        return row["client_info"] if row else None

    # --- pending upstream authorizations ------------------------------------

    async def put_pending(self, state: str, payload: dict[str, Any], ttl: int) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO pending_auth (state, payload, expires_at) VALUES (?, ?, ?)",
                (state, json.dumps(payload), time.time() + ttl),
            )
            await self._db.commit()

    async def take_pending(self, state: str) -> dict[str, Any] | None:
        """Fetch and delete in one shot — a state parameter is single-use."""
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT payload, expires_at FROM pending_auth WHERE state = ?", (state,)
            ) as cur:
                row = await cur.fetchone()
            await self._db.execute("DELETE FROM pending_auth WHERE state = ?", (state,))
            await self._db.commit()
        if row is None or row["expires_at"] < time.time():
            return None
        return json.loads(row["payload"])

    # --- ClickUp grants -----------------------------------------------------

    async def upsert_grant(
        self,
        clickup_user_id: str,
        email: str | None,
        username: str | None,
        access_token: str,
        workspaces: list[dict[str, Any]] | None = None,
    ) -> int:
        """Insert or refresh a user's grant. Keyed on ClickUp user id so
        re-authorizing replaces the row rather than accumulating duplicates."""
        enc = self._fernet.encrypt(access_token.encode("utf-8"))
        now = time.time()
        ws = json.dumps(workspaces or [])
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                """
                INSERT INTO clickup_grants
                    (clickup_user_id, email, username, token_enc, workspaces, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(clickup_user_id) DO UPDATE SET
                    email=excluded.email,
                    username=excluded.username,
                    token_enc=excluded.token_enc,
                    workspaces=excluded.workspaces,
                    updated_at=excluded.updated_at
                """,
                (clickup_user_id, email, username, enc, ws, now, now),
            )
            async with self._db.execute(
                "SELECT id FROM clickup_grants WHERE clickup_user_id = ?", (clickup_user_id,)
            ) as cur:
                row = await cur.fetchone()
            await self._db.commit()
        if row is None:
            raise StoreError("grant upsert did not yield an id")
        return int(row["id"])

    def _row_to_grant(self, row: aiosqlite.Row) -> ClickUpGrant | None:
        try:
            token = self._fernet.decrypt(row["token_enc"]).decode("utf-8")
        except InvalidToken:
            # Almost always a rotated TOKEN_ENCRYPTION_KEY. The row is unusable;
            # surfacing None makes the user re-authorize rather than see a 500.
            logger.error(
                "Could not decrypt stored ClickUp token — TOKEN_ENCRYPTION_KEY may have "
                "changed. Affected user must re-authorize.",
                extra={"grant_id": row["id"]},
            )
            return None
        return ClickUpGrant(
            id=int(row["id"]),
            clickup_user_id=row["clickup_user_id"],
            email=row["email"],
            username=row["username"],
            access_token=token,
            workspaces=json.loads(row["workspaces"] or "[]"),
        )

    async def get_grant(self, grant_id: int) -> ClickUpGrant | None:
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT * FROM clickup_grants WHERE id = ?", (grant_id,)
            ) as cur:
                row = await cur.fetchone()
        return self._row_to_grant(row) if row else None

    async def count_grants(self) -> int:
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute("SELECT COUNT(*) AS n FROM clickup_grants") as cur:
                row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def delete_grant(self, grant_id: int) -> None:
        """Revoke a user entirely. Tokens cascade via the FK."""
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute("DELETE FROM clickup_grants WHERE id = ?", (grant_id,))
            await self._db.commit()

    # --- authorization codes ------------------------------------------------

    async def put_auth_code(
        self, code: str, client_id: str, grant_id: int, payload: str, expires_at: float
    ) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO auth_codes (code_hash, client_id, grant_id, payload, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash(code), client_id, grant_id, payload, expires_at),
            )
            await self._db.commit()

    async def get_auth_code(self, code: str) -> tuple[str, int, str] | None:
        """Returns (client_id, grant_id, payload) if the code is live."""
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT client_id, grant_id, payload, expires_at FROM auth_codes WHERE code_hash = ?",
                (token_hash(code),),
            ) as cur:
                row = await cur.fetchone()
        if row is None or row["expires_at"] < time.time():
            return None
        return row["client_id"], int(row["grant_id"]), row["payload"]

    async def delete_auth_code(self, code: str) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute("DELETE FROM auth_codes WHERE code_hash = ?", (token_hash(code),))
            await self._db.commit()

    # --- MCP access / refresh tokens ----------------------------------------

    async def put_access_token(
        self,
        token: str,
        client_id: str,
        grant_id: int,
        scopes: list[str],
        expires_at: float | None,
        resource: str | None = None,
    ) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO access_tokens "
                "(token_hash, client_id, grant_id, scopes, resource, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash(token), client_id, grant_id, json.dumps(scopes), resource, expires_at),
            )
            await self._db.commit()

    async def get_access_token(self, token: str) -> dict[str, Any] | None:
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT client_id, grant_id, scopes, resource, expires_at FROM access_tokens "
                "WHERE token_hash = ?",
                (token_hash(token),),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            await self.delete_access_token(token)
            return None
        return {
            "client_id": row["client_id"],
            "grant_id": int(row["grant_id"]),
            "scopes": json.loads(row["scopes"]),
            "resource": row["resource"],
            "expires_at": row["expires_at"],
        }

    async def delete_access_token(self, token: str) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "DELETE FROM access_tokens WHERE token_hash = ?", (token_hash(token),)
            )
            await self._db.commit()

    async def put_refresh_token(
        self, token: str, client_id: str, grant_id: int, scopes: list[str]
    ) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO refresh_tokens (token_hash, client_id, grant_id, scopes) "
                "VALUES (?, ?, ?, ?)",
                (token_hash(token), client_id, grant_id, json.dumps(scopes)),
            )
            await self._db.commit()

    async def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        await self._ensure_connected()
        async with self._lock:
            async with self._db.execute(
                "SELECT client_id, grant_id, scopes FROM refresh_tokens WHERE token_hash = ?",
                (token_hash(token),),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "client_id": row["client_id"],
            "grant_id": int(row["grant_id"]),
            "scopes": json.loads(row["scopes"]),
        }

    async def delete_refresh_token(self, token: str) -> None:
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "DELETE FROM refresh_tokens WHERE token_hash = ?", (token_hash(token),)
            )
            await self._db.commit()

    # --- audit --------------------------------------------------------------

    async def record_audit(
        self,
        grant_id: int | None,
        clickup_user_id: str | None,
        tool: str,
        arguments: Any,
        status: str,
        error: str | None,
        duration_ms: int,
        result_ref: str | None = None,
    ) -> None:
        try:
            args_json = json.dumps(arguments, default=str)[:4000]
        except (TypeError, ValueError):
            args_json = None
        await self._ensure_connected()
        async with self._lock:
            await self._db.execute(
                "INSERT INTO audit_log "
                "(ts, grant_id, clickup_user_id, tool, arguments, status, error, "
                " duration_ms, result_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    grant_id,
                    clickup_user_id,
                    tool,
                    args_json,
                    status,
                    (error or "")[:1000] or None,
                    duration_ms,
                    result_ref,
                ),
            )
            await self._db.commit()

    # --- housekeeping -------------------------------------------------------

    async def sweep_expired(self) -> int:
        """Delete expired codes, pending states, and access tokens."""
        now = time.time()
        await self._ensure_connected()
        async with self._lock:
            cur = await self._db.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,))
            removed = cur.rowcount or 0
            cur = await self._db.execute("DELETE FROM pending_auth WHERE expires_at < ?", (now,))
            removed += cur.rowcount or 0
            cur = await self._db.execute(
                "DELETE FROM access_tokens WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
            )
            removed += cur.rowcount or 0
            await self._db.commit()
        return removed
