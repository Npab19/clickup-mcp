"""Audit trail content.

Motivated by a real reconciliation: a live `create_task` was recorded as success,
but the trail stored only its arguments, so there was no way to tell which task it
produced or whether it still existed. Four queries later the answer was "the user
deleted it" — one query, had the result been recorded.
"""
from __future__ import annotations

import pytest

from clickup_mcp.audit import _redact, result_ref
from clickup_mcp.store import Store


def test_result_ref_captures_a_created_task_id():
    ref = result_ref({"id": "86bb4x7ff", "name": "test", "status": "to do"})
    assert "86bb4x7ff" in ref


def test_result_ref_finds_ids_nested_in_a_wrapper():
    ref = result_ref({"uploaded": True, "attachment": {"id": "att_1", "url": "https://x"}})
    assert "att_1" in ref


def test_result_ref_records_deletions():
    ref = result_ref({"deleted": True, "task_id": "86bb4x7ff"})
    assert "deleted" in ref and "86bb4x7ff" in ref


def test_result_ref_is_bounded_and_quiet_on_junk():
    assert result_ref(None) is None
    assert result_ref("a string") is None
    assert result_ref({"nothing": "identifying"}) is None
    # deeply nested beyond the walk limit — must not recurse forever
    deep = {"a": {"b": {"c": {"d": {"e": {"id": "hidden"}}}}}}
    assert result_ref(deep) is None
    big = result_ref({"id": "x" * 5000})
    assert big is not None and len(big) <= 500


def test_secrets_are_redacted_from_arguments():
    out = _redact({"task_id": "1", "api_key": "sk_live_x", "Token": "abc"})
    assert out["task_id"] == "1"
    assert out["api_key"] == "<redacted>"
    assert out["Token"] == "<redacted>"


async def test_audit_row_round_trips_with_its_result(store):
    grant_id = await store.upsert_grant("111", "a@example.com", "Ada", "tok")
    await store.record_audit(
        grant_id=grant_id,
        clickup_user_id="111",
        tool="create_task",
        arguments={"list_id": "901414414408", "name": "test"},
        status="success",
        error=None,
        duration_ms=863,
        result_ref='{"id": "86bb4x7ff"}',
    )
    async with store._db.execute(
        "SELECT tool, status, result_ref, duration_ms FROM audit_log"
    ) as cur:
        row = await cur.fetchone()
    assert row["tool"] == "create_task"
    assert row["status"] == "success"
    assert "86bb4x7ff" in row["result_ref"]


async def test_result_ref_column_is_added_to_a_pre_existing_database(tmp_path):
    """A deployed database predates the column; CREATE TABLE IF NOT EXISTS is a no-op
    on an existing table, so the migration has to add it explicitly."""
    import aiosqlite
    from cryptography.fernet import Fernet

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
            " grant_id INTEGER, clickup_user_id TEXT, tool TEXT NOT NULL, arguments TEXT,"
            " status TEXT NOT NULL, error TEXT, duration_ms INTEGER)"
        )
        await conn.execute(
            "INSERT INTO audit_log (ts, tool, status) VALUES (1.0, 'legacy_call', 'success')"
        )
        await conn.commit()

    s = Store(db_path=str(path), encryption_key=Fernet.generate_key().decode())
    await s.connect()
    try:
        async with s._db.execute("PRAGMA table_info(audit_log)") as cur:
            columns = {r["name"] for r in await cur.fetchall()}
        assert "result_ref" in columns
        # the pre-existing row must survive
        async with s._db.execute("SELECT tool FROM audit_log") as cur:
            assert (await cur.fetchone())["tool"] == "legacy_call"
    finally:
        await s.close()


@pytest.mark.parametrize("bad", [object(), {1: 2}, [[[[["deep"]]]]]])
def test_result_ref_never_raises(bad):
    result_ref(bad)
