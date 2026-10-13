"""The one-shot migration of the real database, and the admin CLI."""
import sqlite3
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import tenancy

pytestmark = pytest.mark.no_user

OLD_SCHEMA = """
CREATE TABLE activities (id BIGINT PRIMARY KEY, name VARCHAR, distance FLOAT,
                         start_date DATETIME);
CREATE TABLE daily_wellness (date VARCHAR PRIMARY KEY, resting_hr INTEGER, raw JSON);
CREATE TABLE route_labels (id INTEGER PRIMARY KEY AUTOINCREMENT,
                           route_key VARCHAR NOT NULL UNIQUE, name VARCHAR NOT NULL);
CREATE INDEX ix_route_labels_route_key ON route_labels (route_key);
INSERT INTO activities VALUES (1, 'old run', 5000, '2026-10-01 07:00:00');
INSERT INTO activities VALUES (2, 'older run', 3000, '2026-09-01 07:00:00');
INSERT INTO daily_wellness VALUES ('2026-10-01', 52, '{}');
INSERT INTO daily_wellness VALUES ('2026-10-02', 53, '{}');
INSERT INTO route_labels (route_key, name) VALUES ('loop', 'Home loop');
"""


def _old_db() -> str:
    path = tempfile.mktemp(suffix="-old.db")
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.commit()
    con.close()
    return path


async def _run(monkeypatch, path, backup, **kw):
    import importlib, sys, config, database
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("CREDENTIALS_KEY", kw.pop("key", Fernet.generate_key().decode()))
    monkeypatch.setenv("strava_access_token", "OLD-AT")
    monkeypatch.setenv("strava_refresh_token", "OLD-RT")
    monkeypatch.setenv("MCP_SECRET", "the-old-mcp-secret")
    tokdir = Path(tempfile.mkdtemp())
    (tokdir / "oauth.json").write_text("{}")
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(tokdir))
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
    importlib.reload(config); importlib.reload(database)
    import migrate_multiuser
    monkeypatch.setattr(migrate_multiuser, "_read_garmin_tokens", lambda d: "G" * 600)
    return await migrate_multiuser.migrate(path, backup, "Owner@Example.com",
                                           strava_athlete_id=kw.get("athlete", "4242"))


async def test_migration_assigns_everything_to_the_owner(monkeypatch):
    path = _old_db()
    original = Path(path).read_bytes()
    backup = path + ".bak"
    report = await _run(monkeypatch, path, backup)

    assert Path(backup).read_bytes() == original
    assert report["before"]["activities"] == report["after"]["activities"] == 2
    assert report["before"]["daily_wellness"] == report["after"]["daily_wellness"] == 2
    assert report["before"]["route_labels"] == report["after"]["route_labels"] == 1

    con = sqlite3.connect(path)
    for t in ("activities", "daily_wellness", "route_labels"):
        assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id IS NOT 1").fetchone()[0] == 0, t
    pk = [r[1] for r in sorted(con.execute("PRAGMA table_info(daily_wellness)"),
                               key=lambda r: r[5]) if r[5]]
    assert pk == ["user_id", "date"]
    con.execute("INSERT INTO route_labels (route_key, name, user_id) VALUES ('loop', 'x', 2)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO route_labels (route_key, name, user_id) VALUES ('loop', 'y', 1)")
    user = con.execute("SELECT id, email, is_admin, password_hash FROM users").fetchall()
    assert user == [(1, "owner@example.com", 1, "!unusable")]
    import auth
    token_hash = con.execute("SELECT token_hash, user_id FROM connector_tokens").fetchall()
    assert token_hash == [(auth.hash_token("the-old-mcp-secret"), 1)]
    con.close()

    import credentials, database
    async with database.async_session() as s:
        st = await credentials.get(s, 1, "strava")
        gm = await credentials.get(s, 1, "garmin")
    assert st.data["refresh_token"] == "OLD-RT" and st.external_id == "4242"
    assert gm.data == {"tokens": "G" * 600}


async def test_migration_is_idempotent(monkeypatch):
    path = _old_db()
    key = Fernet.generate_key().decode()
    first = await _run(monkeypatch, path, path + ".bak", key=key)
    second = await _run(monkeypatch, path, path + ".bak", key=key)
    assert second["after"] == first["after"]
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 2
    con.close()


async def test_migration_refuses_without_a_backup_path(monkeypatch):
    import migrate_multiuser
    with pytest.raises(ValueError, match="backup"):
        await migrate_multiuser.migrate(_old_db(), None, "o@x.co")


async def test_migration_refuses_while_the_app_has_the_db_open(monkeypatch):
    path = _old_db()
    Path(path + "-wal").write_bytes(b"x")
    import migrate_multiuser
    with pytest.raises(RuntimeError, match="stop"):
        await migrate_multiuser.migrate(path, path + ".bak", "o@x.co")


async def test_admin_set_password_then_login(monkeypatch):
    path = _old_db()
    await _run(monkeypatch, path, path + ".bak")
    import admin, auth, database
    await admin.set_password("owner@example.com", "a brand new password")
    async with database.async_session() as s:
        assert await auth.authenticate(s, "owner@example.com", "a brand new password")


async def test_admin_delete_user_removes_only_their_rows(monkeypatch):
    from tests.multiuser_fixtures import seed_user_a, seed_user_b, setup_app
    main, database = await setup_app(monkeypatch, "adm")
    await seed_user_a(database)
    await seed_user_b(database)
    import admin
    await admin.delete_user("b@example.com")
    from sqlalchemy import func, select
    from models import Activity, User
    with tenancy.system_scope():
        async with database.async_session() as s:
            owners = (await s.execute(select(Activity.user_id))).scalars().all()
            users = (await s.execute(select(User.email))).scalars().all()
    assert set(owners) == {1} and users == ["a@example.com"]
    with pytest.raises(SystemExit, match="last admin"):
        await admin.delete_user("a@example.com")


# The two rebuilt tables exactly as the pre-branch models.py (beefa4b) created them.
ORIGINAL_DAILY_WELLNESS = """
CREATE TABLE daily_wellness (
    date VARCHAR NOT NULL,
    readiness_score INTEGER,
    readiness_level VARCHAR,
    sleep_hours FLOAT,
    sleep_score INTEGER,
    body_battery_peak INTEGER,
    hrv_last_night INTEGER,
    hrv_status VARCHAR,
    resting_hr INTEGER,
    raw JSON,
    fetched_at DATETIME,
    PRIMARY KEY (date)
);
"""
ORIGINAL_ROUTE_LABELS = """
CREATE TABLE route_labels (
    id INTEGER NOT NULL,
    route_key VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (route_key)
);
CREATE UNIQUE INDEX ix_route_labels_route_key ON route_labels (route_key);
"""
WELLNESS_COLS = ["date", "readiness_score", "readiness_level", "sleep_hours", "sleep_score",
                 "body_battery_peak", "hrv_last_night", "hrv_status", "resting_hr", "raw",
                 "fetched_at"]


def _stripped_ddl(table_names) -> str:
    """Every other tenant table as the current models define it, minus the
    user_id column, its foreign key and its index: the pre-migration shape."""
    import re
    import models
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    out = []
    for name in table_names:
        t = models.Base.metadata.tables[name]
        ddl = str(CreateTable(t).compile(dialect=sqlite_dialect.dialect()))
        ddl = re.sub(r"^\s*user_id INTEGER[^\n]*\n", "", ddl, flags=re.M)
        ddl = re.sub(r",\s*FOREIGN KEY\(user_id\)[^\n]*", "", ddl)
        ddl = re.sub(r",\s*CONSTRAINT \w+ UNIQUE \(user_id[^\n]*", "", ddl)
        assert "user_id" not in ddl, ddl
        out.append(ddl + ";")
        for idx in t.indexes:
            if "user_id" not in [c.name for c in idx.columns]:
                out.append(str(CreateIndex(idx).compile(dialect=sqlite_dialect.dialect())) + ";")
    return "\n".join(out)


async def test_migration_of_the_full_original_schema_keeps_every_column_and_row(monkeypatch):
    import sys, importlib, config, database
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
    importlib.reload(config); importlib.reload(database)
    import models
    others = [t for t in migrate_tables() if t not in ("daily_wellness", "route_labels")]
    path = tempfile.mktemp(suffix="-full.db")
    con = sqlite3.connect(path)
    con.executescript(_stripped_ddl(others) + ORIGINAL_DAILY_WELLNESS + ORIGINAL_ROUTE_LABELS)
    wellness = [
        ("2026-10-01", 71, "GOOD", 7.5, 82, 88, 61, "BALANCED", 52,
         '{"a": [1, 2], "b": null}', "2026-10-01 06:30:00.000000"),
        ("2026-10-02", None, None, None, None, None, None, None, None, None, None),
        ("2026-10-03", 40, "LOW", 5.25, 55, 30, 44, "NONE", 58, '{}', "2026-10-03 06:31:12.500000"),
    ]
    con.executemany("INSERT INTO daily_wellness VALUES (?,?,?,?,?,?,?,?,?,?,?)", wellness)
    labels = [(7, "loop", "Home loop"), (9, "river", "River out-and-back")]
    con.executemany("INSERT INTO route_labels VALUES (?,?,?)", labels)
    con.execute("INSERT INTO activities (id, name, distance) VALUES (5, 'run', 5000)")
    con.commit()
    con.close()

    report = await _run(monkeypatch, path, path + ".bak")
    assert report["before"]["daily_wellness"] == report["after"]["daily_wellness"] == 3
    assert report["before"]["route_labels"] == report["after"]["route_labels"] == 2
    assert report["before"]["activities"] == report["after"]["activities"] == 1

    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(daily_wellness)")]
    assert sorted(cols) == sorted(WELLNESS_COLS + ["user_id"])
    got = con.execute(f"SELECT {', '.join(WELLNESS_COLS)} FROM daily_wellness ORDER BY date").fetchall()
    assert got == wellness
    assert {r[0] for r in con.execute("SELECT user_id FROM daily_wellness")} == {1}
    assert sorted(r[1] for r in con.execute("PRAGMA table_info(route_labels)")) == \
        ["id", "name", "route_key", "user_id"]
    assert con.execute("SELECT id, route_key, name, user_id FROM route_labels ORDER BY id"
                       ).fetchall() == [(7, "loop", "Home loop", 1), (9, "river", "River out-and-back", 1)]
    # Autoincrement-style ids keep working after the rebuild.
    con.execute("INSERT INTO route_labels (route_key, name, user_id) VALUES ('new', 'n', 1)")
    assert con.execute("SELECT MAX(id) FROM route_labels").fetchone()[0] == 10
    # Every tenant table has its user_id index.
    for t in migrate_tables():
        names = {r[1] for r in con.execute(f"PRAGMA index_list({t})")}
        assert f"ix_{t}_user_id" in names or t in ("daily_wellness",), (t, names)
    con.close()


def migrate_tables():
    import migrate_multiuser
    return migrate_multiuser.TENANT_TABLES
