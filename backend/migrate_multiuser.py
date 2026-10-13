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


def _copy_rows(con, table: str, cols: str) -> None:
    con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {table}__old")


def _rebuild(con, table: str) -> None:
    """Recreate `table` from the current model definition, keeping every row.
    Rolls back, leaving the table as it was, if the copy loses a row."""
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
        _copy_rows(con, table, ", ".join(keep))
        n_old = con.execute(f"SELECT COUNT(*) FROM {table}__old").fetchone()[0]
        n_new = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if n_old != n_new:
            raise RuntimeError(f"rebuilding {table} copied {n_new} of {n_old} rows")
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


def _ro(path: str):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def _signature(con) -> dict:
    """Per tenant table: (row count, a fingerprint of which rows exist).

    The fingerprint is SUM(rowid). The migration keeps every table's rowids, with
    one exception: daily_wellness is keyed by a text date, so it has no stable
    integer rowid and the rebuild renumbers it (any gap in the old numbering
    shifts the sum). For that table the fingerprint is a hash of its dates, which
    the rebuild preserves. A table that does not exist counts as (0, 0), so the
    empty tables a finished migration adds do not make a re-run look different.
    """
    import hashlib
    sig = {}
    for t in TENANT_TABLES:
        if not _has_table(con, t):
            continue
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if t == "daily_wellness":
            h = hashlib.sha256()
            for (d,) in con.execute("SELECT date FROM daily_wellness ORDER BY date"):
                h.update(str(d).encode() + b"\n")
            fp = h.hexdigest() if n else 0
        else:
            fp = con.execute(f"SELECT COALESCE(SUM(rowid), 0) FROM {t}").fetchone()[0]
        sig[t] = (n, fp)
    return sig


def _verify_backup(backup_path: str, source_sig: dict) -> None:
    """An existing backup is only trusted if it is intact, pre-migration, and
    matches the source."""
    hint = "move it away or choose another --backup path"
    try:
        con = _ro(backup_path)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            migrated = _has_table(con, "users") or (
                _has_table(con, "activities") and "user_id" in
                [r[1] for r in con.execute("PRAGMA table_info(activities)")])
            sig = _signature(con)
        finally:
            con.close()
    except sqlite3.Error as e:
        raise RuntimeError(f"existing backup {backup_path} is unreadable ({e}); {hint}") from e
    if ok != "ok":
        raise RuntimeError(f"existing backup {backup_path} fails integrity_check ({ok}); {hint}")
    if migrated:
        raise RuntimeError(f"existing backup {backup_path} was already migrated (it has a "
                           f"users table or user_id columns), so it is not a pre-migration "
                           f"backup; {hint}")
    if any(sig.get(t, (0, 0)) != source_sig.get(t, (0, 0)) for t in set(sig) | set(source_sig)):
        raise RuntimeError(f"existing backup {backup_path} does not match the database: "
                           f"row counts or row ids differ; {hint}")


def _write_backup(db_path: str, backup_path: str) -> None:
    tmp = backup_path + ".tmp"
    if os.path.exists(tmp):
        os.unlink(tmp)
    src, dst = _ro(db_path), sqlite3.connect(tmp)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"new backup {tmp} fails integrity_check")
    finally:
        dst.close()
        src.close()
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, backup_path)
    dfd = os.open(os.path.dirname(os.path.abspath(backup_path)), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _resolve_journal(db_path: str) -> None:
    """Deal with `<db>-journal`, which a hard-killed run can leave behind.

    Decided by LOCK, never by the journal's header: a stale journal carries a random
    checksum nonce, and a live writer's journal has a zeroed magic until it syncs.
    If we can take an EXCLUSIVE lock nobody else has the database, so any journal
    left after SQLite's own hot-journal rollback is stale.
    """
    journal = Path(db_path + "-journal")
    if not journal.exists():
        return
    live = (f"{journal} exists and another process has the database open; "
            "stop the app")
    con = sqlite3.connect(db_path, timeout=2, isolation_level=None)
    try:
        try:
            con.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as e:
            if "locked" in str(e) or "busy" in str(e):
                raise RuntimeError(live) from e
            raise
        removed = False
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            # Whatever journal survives our EXCLUSIVE lock is stale. Remove it while
            # still holding the lock, so no other process can start a write and
            # have its fresh journal deleted.
            if ok == "ok" and journal.exists():
                journal.unlink()
                removed = True
        finally:
            con.execute("ROLLBACK")
    finally:
        con.close()
    if ok != "ok":
        raise RuntimeError(f"{db_path} fails integrity_check ({ok}) after an interrupted "
                           "run; restore from the backup")
    if removed:
        print(f"removed stale journal {journal} (database unlocked, integrity_check ok)")
    else:
        print(f"recovered an interrupted run: rolled back {journal}")


def preflight(db_path, backup_path, owner_email, *, confirmed, no_strava=False,
              no_garmin=False, no_connector=False, fix_owner_email=False) -> dict:
    """Everything that can be checked without writing. Raises before any mutation."""
    import auth
    import config
    import credentials
    import database
    if not backup_path:
        raise ValueError("a backup path is required")
    if not Path(db_path).exists():
        raise RuntimeError(f"{db_path} does not exist")
    if os.path.exists(backup_path) and os.path.samefile(db_path, backup_path):
        raise RuntimeError("the backup path is the database itself")
    if Path(os.path.realpath(backup_path)) == Path(os.path.realpath(db_path)):
        raise RuntimeError("the backup path is the database itself")
    _resolve_journal(db_path)
    for suffix in ("-wal", "-shm"):
        if Path(db_path + suffix).exists():
            raise RuntimeError(f"{db_path}{suffix} exists: stop the app before migrating")
    if str(database.DB_PATH) != db_path:
        raise RuntimeError(f"DB_PATH is {database.DB_PATH}, expected {db_path}")

    email = auth.normalize_email(owner_email)
    con = _ro(db_path)
    try:
        before = _counts(con)
        sig = _signature(con)
        users = (con.execute("SELECT id, email, password_hash FROM users").fetchall()
                 if _has_table(con, "users") else [])

        def present(sql, *args):
            return (_has_table(con, sql.split(" FROM ")[1].split()[0])
                    and con.execute(sql, args).fetchone() is not None)
        have_strava = present("SELECT 1 FROM credentials WHERE user_id = ? AND provider = ?",
                              OWNER_ID, "strava")
        have_garmin = present("SELECT 1 FROM credentials WHERE user_id = ? AND provider = ?",
                              OWNER_ID, "garmin")
        have_conn = present("SELECT 1 FROM connector_tokens WHERE user_id = ?", OWNER_ID)
    finally:
        con.close()

    plan = {"fix_email": False}
    if users and not any(u[1] == email for u in users):
        only = users[0] if len(users) == 1 else None
        if only and only[0] == OWNER_ID and only[2] == auth.UNUSABLE_PASSWORD:
            if not fix_owner_email:
                raise RuntimeError(f"the owner is currently {only[1]}, not {email}; if that "
                                   "was a typo, re-run with --fix-owner-email")
            plan["fix_email"] = True
        else:
            raise RuntimeError("users already exist but the owner is not among them")

    at, rt = os.getenv("strava_access_token"), os.getenv("strava_refresh_token")
    store = os.getenv("GARMIN_TOKENSTORE") or config.GARMIN_TOKENSTORE
    garmin_dir = bool(store and os.path.isdir(store) and os.listdir(store))
    secret = (os.getenv("MCP_SECRET") or "").strip()

    def decide(name, found, have, skip):
        if have:
            state = "already present"
        elif skip:
            state = "skipped by flag"
        elif found:
            state = "found, will move"
        else:
            state = "NOT FOUND"
        print(f"  {name:10} {state}")
        if state == "NOT FOUND":
            raise RuntimeError(f"no {name} source found and none stored; pass "
                               f"--no-{name} to proceed without it")
        return state == "found, will move"
    print(f"owner: {email}")
    plan["strava"] = decide("strava", bool(rt), have_strava, no_strava)
    plan["garmin"] = decide("garmin", garmin_dir, have_garmin, no_garmin)
    plan["connector"] = decide("connector", bool(secret), have_conn, no_connector)

    if plan["strava"] or plan["garmin"] or plan["connector"]:
        try:
            if credentials.decrypt(credentials.encrypt({"probe": "x"})) != {"probe": "x"}:
                raise ValueError("round trip differs")
        except Exception as e:
            raise RuntimeError(f"CREDENTIALS_KEY is missing or invalid ({e!r}); "
                               "nothing was changed") from e
    plan["garmin_tokens"] = None
    if plan["garmin"]:
        try:
            plan["garmin_tokens"] = _read_garmin_tokens(store)
        except Exception as e:
            raise RuntimeError(f"{store} holds no valid Garmin token files ({e!r}); "
                               "pass --no-garmin to skip") from e
    if not confirmed:
        raise RuntimeError(f"not confirmed: re-run with --yes to migrate to owner {email}")
    plan.update(before=before, sig=sig, email=email, at=at, rt=rt, secret=secret, store=store)
    return plan


async def migrate(db_path, backup_path, owner_email: str,
                  strava_athlete_id: str | None = None, *, confirmed: bool = False,
                  no_strava: bool = False, no_garmin: bool = False,
                  no_connector: bool = False, fix_owner_email: bool = False) -> dict:
    if not backup_path:
        raise ValueError("a backup path is required")
    db_path, backup_path = str(db_path), str(backup_path)
    plan = preflight(db_path, backup_path, owner_email, confirmed=confirmed,
                     no_strava=no_strava, no_garmin=no_garmin,
                     no_connector=no_connector, fix_owner_email=fix_owner_email)
    import auth
    import credentials
    import database
    before, email = plan["before"], plan["email"]

    if os.path.exists(backup_path):
        _verify_backup(backup_path, plan["sig"])
        print(f"backup already present and verified, left untouched: {backup_path}")
    else:
        _write_backup(db_path, backup_path)
        print(f"backup written: {backup_path}")

    await database.init_db()  # new tables + nullable user_id columns
    await database.engine.dispose()

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("BEGIN")
    try:
        if plan["fix_email"]:
            con.execute("UPDATE users SET email = ? WHERE id = ?", (email, OWNER_ID))
        row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
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
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    if _pk(con, "daily_wellness") != ["user_id", "date"]:
        _rebuild(con, "daily_wellness")
        print("daily_wellness rebuilt with a per-user key")
    if _has_global_unique_route_key(con):
        _rebuild(con, "route_labels")
        print("route_labels rebuilt with a per-user unique key")

    now = datetime.utcnow()
    con.execute("BEGIN")
    try:
        if plan["strava"]:
            if not strava_athlete_id:
                print("WARNING: no --strava-athlete-id; Strava webhook events will not "
                      "route to the owner until it is set")
            con.execute("INSERT INTO credentials (user_id, provider, secret_blob, external_id, "
                        "status, updated_at) VALUES (?, 'strava', ?, ?, 'ok', ?)",
                        (OWNER_ID, credentials.encrypt({"access_token": plan["at"] or "",
                                                        "refresh_token": plan["rt"],
                                                        "expires_at": None}),
                         str(strava_athlete_id) if strava_athlete_id else None, now))
            print("Strava tokens moved into credentials")
        if plan["garmin"]:
            con.execute("INSERT INTO credentials (user_id, provider, secret_blob, external_id, "
                        "status, updated_at) VALUES (?, 'garmin', ?, NULL, 'ok', ?)",
                        (OWNER_ID, credentials.encrypt({"tokens": plan["garmin_tokens"]}), now))
            print("Garmin tokens moved into credentials")
        if plan["connector"]:
            con.execute("INSERT INTO connector_tokens (token_hash, user_id, created_at) "
                        "VALUES (?, ?, ?)", (auth.hash_token(plan["secret"]), OWNER_ID, now))
            print("existing connector URL kept for the owner")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    after = _counts(con)
    nulls = {t: con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id IS NULL").fetchone()[0]
             for t in after}
    integrity = con.execute("PRAGMA integrity_check").fetchall()
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    for t, n in before.items():
        if after.get(t) != n:
            raise RuntimeError(f"row count changed in {t}: {n} -> {after.get(t)}")
    if any(nulls.values()):
        raise RuntimeError(f"rows left without an owner: {nulls}")
    if integrity != [("ok",)]:
        raise RuntimeError(f"integrity_check failed after migrating: {integrity[:5]}")
    if fk:
        raise RuntimeError(f"foreign_key_check failed after migrating: {fk[:5]}")
    return {"before": before, "after": after}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backup", required=True)
    p.add_argument("--owner-email", required=True)
    p.add_argument("--strava-athlete-id")
    p.add_argument("--yes", action="store_true", help="confirm the owner email")
    p.add_argument("--no-strava", action="store_true")
    p.add_argument("--no-garmin", action="store_true")
    p.add_argument("--no-connector", action="store_true")
    p.add_argument("--fix-owner-email", action="store_true",
                   help="correct the email of an owner created with a typo")
    a = p.parse_args()
    import database
    confirmed = a.yes
    if not confirmed and sys.stdin.isatty():
        confirmed = input(f"Migrate everything to owner {a.owner_email.strip().lower()}? "
                          "[y/N] ").strip().lower() == "y"
    try:
        report = asyncio.run(migrate(
            database.DB_PATH, a.backup, a.owner_email, a.strava_athlete_id,
            confirmed=confirmed, no_strava=a.no_strava, no_garmin=a.no_garmin,
            no_connector=a.no_connector, fix_owner_email=a.fix_owner_email))
    except (RuntimeError, ValueError) as e:
        sys.exit(f"ABORTED: {e}")
    for t in report["after"]:
        print(f"  {t:18} {report['before'].get(t, 0):>7} -> {report['after'][t]:>7}")
    print("migration complete. Next: python admin.py set-password", a.owner_email)


if __name__ == "__main__":
    sys.exit(main())
