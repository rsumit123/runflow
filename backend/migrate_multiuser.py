"""One-shot migration from single-athlete RunFlow to accounts.

Run on the server with the app STOPPED:

  python migrate_multiuser.py --backup /data/training.pre-multiuser.db \\
      --owner-email rsumit123@gmail.com [--strava-athlete-id 12345]

Copies the database to --backup first and never touches that copy. Safe to run
again: every step checks before it acts. Exits non-zero if any table's row
count changes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

TENANT_TABLES = [
    "activities", "splits", "streams", "best_efforts", "chat_messages",
    "daily_wellness", "route_labels", "goals", "route_merges", "plans",
    "planned_workouts",
]
OWNER_ID = 1


def _counts(con) -> dict:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in TENANT_TABLES if t in have}


def _pk(con, table) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5]]


def _has_global_unique_route_key(con) -> bool:
    for idx in con.execute("PRAGMA index_list(route_labels)").fetchall():
        name, unique = idx[1], idx[2]
        if unique:
            cols = [r[2] for r in con.execute(f"PRAGMA index_info('{name}')").fetchall()]
            if cols == ["route_key"]:
                return True
    return False


def _rebuild(con, table: str) -> None:
    """Recreate `table` from the current model definition, keeping every row."""
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    import models
    t = models.Base.metadata.tables[table]
    old_cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    keep = [c.name for c in t.columns if c.name in old_cols]
    con.execute("BEGIN")
    try:
        for idx in con.execute(f"PRAGMA index_list({table})").fetchall():
            if idx[3] == "c":  # explicitly created; autoindexes go with the table
                con.execute(f'DROP INDEX "{idx[1]}"')
        con.execute(f"ALTER TABLE {table} RENAME TO {table}__old")
        con.execute(str(CreateTable(t).compile(dialect=sqlite_dialect.dialect())))
        for index in t.indexes:
            con.execute(str(CreateIndex(index).compile(dialect=sqlite_dialect.dialect())))
        cols = ", ".join(keep)
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {table}__old")
        con.execute(f"DROP TABLE {table}__old")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _read_garmin_tokens(directory: str) -> str:
    from garminconnect import Garmin
    g = Garmin()
    g.client.load(directory)  # reads the token files; no network
    return g.client.dumps()


async def migrate(db_path, backup_path, owner_email: str,
                  strava_athlete_id: str | None = None) -> dict:
    if not backup_path:
        raise ValueError("a backup path is required")
    db_path, backup_path = str(db_path), str(backup_path)
    for suffix in ("-wal", "-shm"):
        if Path(db_path + suffix).exists():
            raise RuntimeError(f"{db_path}{suffix} exists: stop the app before migrating")

    import auth
    import config
    import credentials
    import database
    if str(database.DB_PATH) != db_path:
        raise RuntimeError(f"DB_PATH is {database.DB_PATH}, expected {db_path}")

    if not Path(backup_path).exists():
        shutil.copy2(db_path, backup_path)
        print(f"backup written: {backup_path}")
    else:
        print(f"backup already present, left untouched: {backup_path}")

    con = sqlite3.connect(db_path, isolation_level=None)
    before = _counts(con)
    con.close()

    await database.init_db()  # new tables + nullable user_id columns
    await database.engine.dispose()

    con = sqlite3.connect(db_path, isolation_level=None)
    email = auth.normalize_email(owner_email)
    row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise RuntimeError("users already exist but the owner is not among them")
        con.execute("INSERT INTO users (id, email, password_hash, is_admin, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (OWNER_ID, email, auth.UNUSABLE_PASSWORD, datetime.utcnow()))
    elif row[0] != OWNER_ID:
        raise RuntimeError(f"owner exists with id {row[0]}, expected {OWNER_ID}")

    for t in TENANT_TABLES:
        con.execute(f"UPDATE {t} SET user_id = ? WHERE user_id IS NULL", (OWNER_ID,))
        # A fresh schema gets ix_<table>_user_id from the model; a migrated one
        # only got the column from ALTER, so add the index here.
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_user_id ON {t} (user_id)")

    if _pk(con, "daily_wellness") != ["user_id", "date"]:
        _rebuild(con, "daily_wellness")
        print("daily_wellness rebuilt with a per-user key")
    if _has_global_unique_route_key(con):
        _rebuild(con, "route_labels")
        print("route_labels rebuilt with a per-user unique key")

    def has_cred(provider):
        return con.execute("SELECT 1 FROM credentials WHERE user_id = ? AND provider = ?",
                           (OWNER_ID, provider)).fetchone() is not None

    now = datetime.utcnow()
    at, rt = os.getenv("strava_access_token"), os.getenv("strava_refresh_token")
    if rt and not has_cred("strava"):
        if not strava_athlete_id:
            print("WARNING: no --strava-athlete-id; Strava webhook events will not route "
                  "to the owner until it is set")
        con.execute("INSERT INTO credentials (user_id, provider, secret_blob, external_id, "
                    "status, updated_at) VALUES (?, 'strava', ?, ?, 'ok', ?)",
                    (OWNER_ID, credentials.encrypt({"access_token": at or "",
                                                    "refresh_token": rt,
                                                    "expires_at": None}),
                     str(strava_athlete_id) if strava_athlete_id else None, now))
        print("Strava tokens moved into credentials")

    store = os.getenv("GARMIN_TOKENSTORE") or config.GARMIN_TOKENSTORE
    if store and os.path.isdir(store) and os.listdir(store) and not has_cred("garmin"):
        con.execute("INSERT INTO credentials (user_id, provider, secret_blob, external_id, "
                    "status, updated_at) VALUES (?, 'garmin', ?, NULL, 'ok', ?)",
                    (OWNER_ID, credentials.encrypt({"tokens": _read_garmin_tokens(store)}), now))
        print("Garmin tokens moved into credentials")

    secret = (os.getenv("MCP_SECRET") or "").strip()
    if secret and con.execute("SELECT 1 FROM connector_tokens WHERE user_id = ?",
                              (OWNER_ID,)).fetchone() is None:
        con.execute("INSERT INTO connector_tokens (token_hash, user_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (auth.hash_token(secret), OWNER_ID, now))
        print("existing connector URL kept for the owner")

    after = _counts(con)
    nulls = {t: con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id IS NULL").fetchone()[0]
             for t in after}
    con.close()
    for t, n in before.items():
        if after.get(t) != n:
            raise RuntimeError(f"row count changed in {t}: {n} -> {after.get(t)}")
    if any(nulls.values()):
        raise RuntimeError(f"rows left without an owner: {nulls}")
    return {"before": before, "after": after}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backup", required=True)
    p.add_argument("--owner-email", required=True)
    p.add_argument("--strava-athlete-id")
    a = p.parse_args()
    import database
    report = asyncio.run(migrate(database.DB_PATH, a.backup, a.owner_email,
                                 a.strava_athlete_id))
    for t in report["after"]:
        print(f"  {t:18} {report['before'].get(t, 0):>7} -> {report['after'][t]:>7}")
    print("migration complete. Next: python admin.py set-password", a.owner_email)


if __name__ == "__main__":
    sys.exit(main())
