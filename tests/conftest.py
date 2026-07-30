from __future__ import annotations

import os

from cryptography.fernet import Fernet

# runtime.py validates configuration at import time and exits the process if it is
# missing, so these have to be set before any clickup_mcp module is imported.
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("CLICKUP_CLIENT_ID", "test-client-id")
os.environ.setdefault("CLICKUP_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SERVER_URL", "http://localhost:8000")

import pytest  # noqa: E402

from clickup_mcp.store import Store  # noqa: E402


@pytest.fixture
async def store(tmp_path):
    s = Store(db_path=str(tmp_path / "test.db"), encryption_key=Fernet.generate_key().decode())
    await s.connect()
    try:
        yield s
    finally:
        await s.close()
