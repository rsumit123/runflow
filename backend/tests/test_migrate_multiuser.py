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
                                           strava_athlete_id=kw.get("athlete", "4242"),
                                           confirmed=True)


async def test_migration_assigns_everything_to_the_owner(monkeypatch):
    path = _old_db()
    c0 = sqlite3.connect(path)
    original = "\n".join(c0.iterdump())
    c0.close()
    backup = path + ".bak"
    report = await _run(monkeypatch, path, backup)

    # The sqlite backup API copies pages, not bytes (the header change counter differs),
    # so compare the whole content: schema and every row.
    cb = sqlite3.connect(backup)
    assert "\n".join(cb.iterdump()) == original
    cb.close()
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
        await migrate_multiuser.migrate(_old_db(), None, "o@x.co", confirmed=True)


async def test_migration_refuses_while_the_app_has_the_db_open(monkeypatch):
    path = _old_db()
    Path(path + "-wal").write_bytes(b"x")
    import migrate_multiuser
    with pytest.raises(RuntimeError, match="stop"):
        await migrate_multiuser.migrate(path, path + ".bak", "o@x.co", confirmed=True)


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


# ---- fix round: backup verification, preflight, rebuild safety, admin ----

def _prep(monkeypatch, path, *, key="gen", strava=True, garmin="ok", mcp=True):
    """Point the app modules at `path` and set the environment; returns the migration module.
    garmin: "ok" (valid-looking tokens), "junk" (only .DS_Store), or None (no directory)."""
    import importlib, sys, config, database
    monkeypatch.setenv("DB_PATH", path)
    if key == "gen":
        key = Fernet.generate_key().decode()
    if key is None:
        monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    else:
        monkeypatch.setenv("CREDENTIALS_KEY", key)
    for name, val, on in (("strava_access_token", "OLD-AT", strava),
                          ("strava_refresh_token", "OLD-RT", strava),
                          ("MCP_SECRET", "the-old-mcp-secret", mcp)):
        if on:
            monkeypatch.setenv(name, val)
        else:
            monkeypatch.delenv(name, raising=False)
    if garmin is None:
        monkeypatch.setenv("GARMIN_TOKENSTORE", "/nonexistent-garmin-dir")
    else:
        d = Path(tempfile.mkdtemp())
        (d / (".DS_Store" if garmin == "junk" else "oauth.json")).write_text("{}")
        monkeypatch.setenv("GARMIN_TOKENSTORE", str(d))
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
    importlib.reload(config); importlib.reload(database)
    import migrate_multiuser
    if garmin == "ok":
        monkeypatch.setattr(migrate_multiuser, "_read_garmin_tokens", lambda d: "G" * 600)
    return migrate_multiuser


def _no_files_left(path):
    return not any(Path(path + s).exists() for s in ("-wal", "-shm", "-journal"))


async def test_truncated_existing_backup_aborts_before_any_write(monkeypatch):
    path = _old_db()
    before = Path(path).read_bytes()
    bak = path + ".bak"
    Path(bak).write_bytes(before[:3000] if len(before) > 3000 else before[: len(before) // 2])
    mm = _prep(monkeypatch, path)
    with pytest.raises(RuntimeError, match=r"\.bak"):
        await mm.migrate(path, bak, "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before


async def test_existing_backup_with_other_counts_is_rejected(monkeypatch):
    path = _old_db()
    bak = path + ".bak"
    src = sqlite3.connect(path); dst = sqlite3.connect(bak); src.backup(dst)
    dst.execute("DELETE FROM activities"); dst.commit(); dst.close(); src.close()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path)
    with pytest.raises(RuntimeError, match="row counts"):
        await mm.migrate(path, bak, "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before


async def test_valid_existing_backup_is_accepted_and_untouched(monkeypatch):
    path = _old_db()
    bak = path + ".bak"
    src = sqlite3.connect(path); dst = sqlite3.connect(bak); src.backup(dst); dst.close(); src.close()
    good = Path(bak).read_bytes()
    mm = _prep(monkeypatch, path)
    report = await mm.migrate(path, bak, "o@x.co", confirmed=True)
    assert report["after"]["activities"] == 2
    assert Path(bak).read_bytes() == good


async def test_backup_path_equal_to_db_is_refused(monkeypatch):
    path = _old_db()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path)
    with pytest.raises(RuntimeError, match="itself"):
        await mm.migrate(path, path, "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before


async def test_fresh_backup_is_complete_and_intact(monkeypatch):
    path = _old_db()
    bak = path + ".bak"
    mm = _prep(monkeypatch, path)
    await mm.migrate(path, bak, "o@x.co", confirmed=True)
    con = sqlite3.connect(bak)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 2
    assert "user_id" not in [r[1] for r in con.execute("PRAGMA table_info(activities)")]
    con.close()
    assert not Path(bak + ".tmp").exists()


@pytest.mark.parametrize("key", [None, "abc"])
async def test_bad_credentials_key_aborts_before_any_write(monkeypatch, key):
    path = _old_db()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path, key=key)
    with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
        await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before
    assert not Path(path + ".bak").exists()


async def test_garmin_dir_without_token_files_aborts_before_any_write(monkeypatch):
    path = _old_db()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path, garmin="junk")
    with pytest.raises(RuntimeError, match="Garmin"):
        await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before
    assert not Path(path + ".bak").exists()


async def test_missing_sources_need_explicit_flags(monkeypatch):
    path = _old_db()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path, strava=False, garmin=None, mcp=False)
    with pytest.raises(RuntimeError, match="--no-"):
        await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before
    report = await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True,
                              no_strava=True, no_garmin=True, no_connector=True)
    assert report["after"]["activities"] == 2
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
    con.close()


async def test_unconfirmed_migration_is_refused(monkeypatch):
    path = _old_db()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path)
    with pytest.raises(RuntimeError, match="not confirmed"):
        await mm.migrate(path, path + ".bak", "o@x.co", confirmed=False)
    assert Path(path).read_bytes() == before


async def test_live_journal_blocks_the_migration(monkeypatch):
    """Another connection mid-write holds the database, so its journal is real
    (non-zero header) and SQLite cannot roll it back."""
    path = _old_db()
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO activities (id, name) VALUES (99, 'in flight')")
    assert Path(path + "-journal").exists()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path)
    try:
        with pytest.raises(RuntimeError, match="another process has the database open; stop"):
            await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True)
    finally:
        holder.execute("ROLLBACK"); holder.close()
    assert Path(path).read_bytes() == before
    assert not Path(path + ".bak").exists()


async def test_stale_zero_header_journal_is_cleared_and_migration_proceeds(monkeypatch):
    path = _old_db()
    Path(path + "-journal").write_bytes(bytes(512))
    mm = _prep(monkeypatch, path)
    report = await mm.migrate(path, path + ".bak", "o@x.co", confirmed=True)
    assert report["after"]["activities"] == 2
    assert not Path(path + "-journal").exists()


@pytest.mark.parametrize("header,stale", [(b"", True), (bytes(28), True), (bytes(512), True),
                                          (b"\xd9\xd5\x05\xf9" + bytes(24), False),
                                          (bytes(27) + b"\x01", False), (b"x", False)])
def test_journal_header_classification(header, stale):
    import migrate_multiuser
    assert migrate_multiuser._journal_header_is_stale(header) is stale


async def test_stale_journal_with_a_damaged_database_aborts(monkeypatch):
    path = _old_db()
    Path(path + "-journal").write_bytes(bytes(512))
    mm = _prep(monkeypatch, path)
    real = sqlite3.connect

    class Bad:
        def __init__(self, c): self.c = c
        def execute(self, sql, *a):
            if "integrity_check" in sql:
                class R:
                    def fetchone(self): return ("row 3 missing",)
                return R()
            return self.c.execute(sql, *a)
        def close(self): self.c.close()
    monkeypatch.setattr(mm.sqlite3, "connect", lambda *a, **k: Bad(real(*a, **k)))
    with pytest.raises(RuntimeError, match="integrity_check"):
        mm._resolve_journal(path)
    assert Path(path + "-journal").exists()


async def test_existing_backup_with_same_counts_but_different_rows_is_rejected(monkeypatch):
    path = _old_db()
    bak = path + ".bak"
    src = sqlite3.connect(path); dst = sqlite3.connect(bak); src.backup(dst)
    dst.execute("DELETE FROM activities WHERE id = 1")
    dst.execute("INSERT INTO activities VALUES (77, 'other', 1, '2026-01-01 00:00:00')")
    dst.commit(); dst.close(); src.close()
    before = Path(path).read_bytes()
    mm = _prep(monkeypatch, path)
    with pytest.raises(RuntimeError, match="does not match"):
        await mm.migrate(path, bak, "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before


async def test_existing_backup_that_was_already_migrated_is_rejected(monkeypatch):
    path = _old_db()
    mm = _prep(monkeypatch, path)
    await mm.migrate(path, path + ".first.bak", "o@x.co", confirmed=True)
    migrated_copy = path + ".migrated.bak"
    src = sqlite3.connect(path); dst = sqlite3.connect(migrated_copy); src.backup(dst)
    dst.close(); src.close()
    before = Path(path).read_bytes()
    with pytest.raises(RuntimeError, match="already migrated"):
        await mm.migrate(path, migrated_copy, "o@x.co", confirmed=True)
    assert Path(path).read_bytes() == before


async def test_rerun_passes_even_when_daily_wellness_rowids_had_gaps(monkeypatch):
    """The rebuild renumbers daily_wellness rowids; the backup check must not care."""
    path = _old_db()
    con = sqlite3.connect(path)
    con.execute("DELETE FROM daily_wellness WHERE date = '2026-10-01'")
    con.execute("INSERT INTO daily_wellness VALUES ('2026-10-05', 50, '{}')")
    con.commit(); con.close()
    mm = _prep(monkeypatch, path)
    bak = path + ".bak"
    first = await mm.migrate(path, bak, "o@x.co", confirmed=True)
    second = await mm.migrate(path, bak, "o@x.co", confirmed=True)
    assert second["after"] == first["after"]


async def test_typo_in_owner_email_needs_the_fix_flag(monkeypatch):
    path = _old_db()
    mm = _prep(monkeypatch, path)
    await mm.migrate(path, path + ".bak", "typo@x.co", confirmed=True)
    before = Path(path).read_bytes()
    with pytest.raises(RuntimeError, match="--fix-owner-email"):
        await mm.migrate(path, path + ".bak", "right@x.co", confirmed=True)
    assert Path(path).read_bytes() == before
    await mm.migrate(path, path + ".bak", "Right@x.co", confirmed=True, fix_owner_email=True)
    con = sqlite3.connect(path)
    assert con.execute("SELECT id, email FROM users").fetchall() == [(1, "right@x.co")]
    con.close()


async def test_admin_set_email(monkeypatch):
    path = _old_db()
    mm = _prep(monkeypatch, path)
    await mm.migrate(path, path + ".bak", "typo@x.co", confirmed=True)
    import admin
    await admin.set_email("typo@x.co", "Right@X.co")
    assert [u[1] for u in await admin.list_users()] == ["right@x.co"]
    with pytest.raises(SystemExit, match="no user"):
        await admin.set_email("typo@x.co", "other@x.co")


async def test_failed_row_copy_rolls_the_rebuild_back(monkeypatch):
    path = _old_db()
    mm = _prep(monkeypatch, path)

    def short_copy(con, table, cols):
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {table}__old "
                    f"WHERE rowid != (SELECT MAX(rowid) FROM {table}__old)")
    monkeypatch.setattr(mm, "_copy_rows", short_copy)
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("ALTER TABLE daily_wellness ADD COLUMN user_id INTEGER")
    con.execute("UPDATE daily_wellness SET user_id = 1")
    with pytest.raises(RuntimeError, match="copied 1 of 2"):
        mm._rebuild(con, "daily_wellness")
    assert mm._pk(con, "daily_wellness") == ["date"]
    assert con.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0] == 2
    assert con.execute("SELECT name FROM sqlite_master WHERE name LIKE '%__old'").fetchall() == []
    con.close()


async def test_admin_delete_user_removes_invites_they_created(monkeypatch):
    from tests.multiuser_fixtures import seed_user_a, seed_user_b, setup_app
    main, database = await setup_app(monkeypatch, "inv")
    import admin, auth
    from models import Invite
    from sqlalchemy import select
    async with database.async_session() as s:
        await auth.create_invite(s, created_by=2, uses=1)
        await auth.create_invite(s, created_by=1, uses=1)
        await s.commit()
    await admin.delete_user("b@example.com")
    async with database.async_session() as s:
        creators = (await s.execute(select(Invite.created_by))).scalars().all()
    assert creators == [1]


@pytest.mark.parametrize("flag", ["--uses", "--expires-days"])
def test_admin_create_invite_rejects_nonpositive_numbers(monkeypatch, flag):
    import sys, admin
    monkeypatch.setattr(sys, "argv", ["admin.py", "create-invite", flag, "0"])
    with pytest.raises(SystemExit, match="at least 1"):
        admin.main()


async def test_admin_set_password_checks_user_and_terminal_before_prompting(monkeypatch):
    import asyncio, sys, admin
    from tests.multiuser_fixtures import setup_app
    await setup_app(monkeypatch, "pw")
    monkeypatch.setattr("getpass.getpass", lambda *a: pytest.fail("prompted"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys, "argv", ["admin.py", "set-password", "nobody@example.com"])
    with pytest.raises(SystemExit, match="no user"):
        await asyncio.to_thread(admin.main)
    monkeypatch.setattr(sys, "argv", ["admin.py", "set-password", "a@example.com"])
    with pytest.raises(SystemExit, match="terminal"):
        await asyncio.to_thread(admin.main)
