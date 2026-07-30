"""Store behaviour: encryption at rest, hashed MCP tokens, grant dedupe, sweeping."""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from clickup_mcp.store import Store, StoreError, token_hash


async def _db_bytes(store: Store, path) -> bytes:
    """Read the database file with WAL folded in.

    Without the checkpoint the rows are still sitting in the -wal sidecar and the
    main file is empty, which makes every "secret is not in the file" assertion
    below pass for the wrong reason.
    """
    await store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await store._db.commit()
    return path.read_bytes()


async def test_clickup_token_is_encrypted_at_rest(store, tmp_path):
    await store.upsert_grant("111", "a@example.com", "Ada", "cu_secret_token_value")

    blob = await _db_bytes(store, tmp_path / "test.db")

    # Sanity: the row really is in the file we are searching.
    assert b"a@example.com" in blob
    # ...and the token in it is not readable.
    assert b"cu_secret_token_value" not in blob


async def test_grant_roundtrips_and_dedupes_by_clickup_user(store):
    first = await store.upsert_grant("111", "a@example.com", "Ada", "token-one", [{"id": "9"}])
    second = await store.upsert_grant("111", "a@example.com", "Ada", "token-two", [{"id": "9"}])
    assert first == second, "re-authorizing must update the row, not create another"

    grant = await store.get_grant(first)
    assert grant is not None
    assert grant.access_token == "token-two"
    assert grant.workspaces == [{"id": "9"}]
    assert await store.count_grants() == 1


async def test_mcp_tokens_are_stored_hashed_not_plaintext(store, tmp_path):
    grant_id = await store.upsert_grant("111", "a@example.com", "Ada", "tok")
    await store.put_access_token("mcp_supersecret", "client-1", grant_id, ["clickup"], None)

    blob = await _db_bytes(store, tmp_path / "test.db")
    assert token_hash("mcp_supersecret").encode() in blob
    assert b"mcp_supersecret" not in blob

    # ...and it is still resolvable by the raw value.
    record = await store.get_access_token("mcp_supersecret")
    assert record is not None and record["grant_id"] == grant_id


async def test_expired_access_token_is_rejected_and_removed(store):
    grant_id = await store.upsert_grant("111", None, None, "tok")
    await store.put_access_token("mcp_x", "client-1", grant_id, ["clickup"], time.time() - 5)

    assert await store.get_access_token("mcp_x") is None
    assert await store.get_access_token("mcp_x") is None  # idempotent after cleanup


async def test_pending_state_is_single_use(store):
    await store.put_pending("state-1", {"client_id": "c"}, ttl=60)
    assert await store.take_pending("state-1") == {"client_id": "c"}
    assert await store.take_pending("state-1") is None


async def test_expired_pending_state_is_not_returned(store):
    await store.put_pending("state-2", {"client_id": "c"}, ttl=-1)
    assert await store.take_pending("state-2") is None


async def test_deleting_a_grant_cascades_to_its_tokens(store):
    grant_id = await store.upsert_grant("111", None, None, "tok")
    await store.put_access_token("mcp_a", "client-1", grant_id, ["clickup"], None)
    await store.put_refresh_token("mcpr_a", "client-1", grant_id, ["clickup"])

    await store.delete_grant(grant_id)

    assert await store.get_access_token("mcp_a") is None
    assert await store.get_refresh_token("mcpr_a") is None


async def test_sweep_removes_only_expired_rows(store):
    grant_id = await store.upsert_grant("111", None, None, "tok")
    await store.put_access_token("live", "c", grant_id, ["clickup"], time.time() + 600)
    await store.put_access_token("dead", "c", grant_id, ["clickup"], time.time() - 600)
    await store.put_auth_code("old", "c", grant_id, "{}", time.time() - 600)

    removed = await store.sweep_expired()

    assert removed == 2
    assert await store.get_access_token("live") is not None


async def test_undecryptable_grant_returns_none_rather_than_raising(store, tmp_path):
    """A rotated TOKEN_ENCRYPTION_KEY must force re-auth, not a 500."""
    grant_id = await store.upsert_grant("111", None, None, "tok")
    await store.close()

    other = Store(db_path=str(tmp_path / "test.db"), encryption_key=Fernet.generate_key().decode())
    await other.connect()
    try:
        assert await other.get_grant(grant_id) is None
    finally:
        await other.close()


def test_missing_encryption_key_is_a_startup_error(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(StoreError, match="TOKEN_ENCRYPTION_KEY"):
        Store(db_path=str(tmp_path / "x.db"))


def test_malformed_encryption_key_is_a_startup_error(tmp_path):
    with pytest.raises(StoreError, match="valid Fernet key"):
        Store(db_path=str(tmp_path / "x.db"), encryption_key="not-a-fernet-key")
