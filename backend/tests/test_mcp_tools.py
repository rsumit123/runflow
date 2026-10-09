"""Tool-level tests against a temp SQLite DB."""
import tempfile
import pytest


@pytest.mark.asyncio
async def test_mcp_mounts_only_when_a_secret_is_set(monkeypatch):
    """No MCP_SECRET must mean no mounted route — fail closed, not fail open."""
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    assert not any("/mcp/" in getattr(r, "path", "") for r in main.app.routes)


@pytest.mark.asyncio
async def test_mcp_mounts_under_the_secret_path(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    assert any(getattr(r, "path", "") == "/mcp/testsecret" for r in main.app.routes)
