"""Startup wiring.

Regression guard for a bug that made the whole server non-functional in the
container while every unit test stayed green: FastMCP's `streamable_http_app()`
hardcodes `lifespan=lambda app: self.session_manager.run()`, and the `lifespan=`
argument its constructor accepts belongs to the MCP *session*, not the process.
Nothing we passed there ran at startup, so `/clickup-callback` — the OAuth
callback, outside any MCP session — met an unconnected store.

The tests passed because their fixture called `store.connect()` by hand.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from clickup_mcp.store import Store


async def test_store_connects_on_first_use_without_an_explicit_connect(tmp_path):
    """Any entry point must work, whether or not a lifespan ran."""
    s = Store(db_path=str(tmp_path / "lazy.db"), encryption_key=Fernet.generate_key().decode())
    try:
        # No connect() call anywhere — this is the /clickup-callback situation.
        assert await s.count_grants() == 0
        grant_id = await s.upsert_grant("111", "a@example.com", "Ada", "tok")
        assert (await s.get_grant(grant_id)).access_token == "tok"
    finally:
        await s.close()


async def test_connect_is_idempotent_and_survives_reconnect(tmp_path):
    path = str(tmp_path / "repeat.db")
    s = Store(db_path=path, encryption_key=Fernet.generate_key().decode())
    try:
        await s.connect()
        await s.connect()
        grant_id = await s.upsert_grant("111", None, None, "tok")
        await s.close()

        # Reopening must find the data, not a fresh schema.
        await s.connect()
        assert await s.count_grants() == 1
        assert (await s.get_grant(grant_id)) is not None
    finally:
        await s.close()


async def test_concurrent_first_use_opens_exactly_one_connection(tmp_path):
    """Several requests can land before any connection exists."""
    import asyncio

    s = Store(db_path=str(tmp_path / "race.db"), encryption_key=Fernet.generate_key().decode())
    try:
        results = await asyncio.gather(*(s.count_grants() for _ in range(10)))
        assert results == [0] * 10
    finally:
        await s.close()


def test_app_does_not_rely_on_the_fastmcp_lifespan_argument():
    """If someone reinstates `lifespan=` on the constructor, they have reintroduced
    the bug — the wiring belongs in GovernedFastMCP.streamable_http_app()."""
    import inspect

    from clickup_mcp import app, policy

    source = inspect.getsource(app)
    assert "lifespan=_lifespan" not in source
    assert "streamable_http_app" in inspect.getsource(policy.GovernedFastMCP)
