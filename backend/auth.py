"""Accounts: passwords, sessions, invites, and a login rate limiter.

Cookie values, invite codes and connector tokens are random and only their
sha256 is stored, so a copy of the database does not hand out live sessions.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timedelta

import bcrypt
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError, OperationalError

import config

log = logging.getLogger(__name__)

BCRYPT_ROUNDS = 12
MIN_PASSWORD_LEN = 10
MAX_PASSWORD_BYTES = 72          # bcrypt's hard limit
# Not a valid bcrypt string, so nothing ever verifies against it. Used for the
# migrated owner until a real password is set from the admin CLI.
UNUSABLE_PASSWORD = "!unusable"
# Compared against when the email is unknown, so a miss costs the same time
# as a wrong password and response timing does not reveal who has an account.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


class AuthError(Exception):
    """A user-facing auth failure. The message is safe to return."""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_email(email: str) -> str:
    e = unicodedata.normalize("NFKC", (email or "").strip()).lower()
    local, sep, domain = e.partition("@")
    if (not sep or not local or "." not in domain or "@" in domain
            or domain.startswith(".") or domain.endswith(".") or len(e) > 254
            or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in e)):
        raise AuthError("Enter a valid email address.")
    return e


def check_password_policy(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise AuthError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw((password or "").encode(), password_hash.encode())
    except ValueError:  # malformed hash, e.g. UNUSABLE_PASSWORD
        return False


async def create_user(session, email: str, password: str | None,
                      is_admin: bool = False):
    """Create a user. password=None gives an unusable hash (set it later)."""
    if password is not None:
        check_password_policy(password)
    pw_hash = (await asyncio.to_thread(hash_password, password)
               if password else UNUSABLE_PASSWORD)
    return await _insert_user(session, email, pw_hash, is_admin)


async def _insert_user(session, email: str, pw_hash: str, is_admin: bool = False):
    from models import User
    user = User(email=normalize_email(email), password_hash=pw_hash,
                is_admin=is_admin, created_at=datetime.utcnow())
    session.add(user)
    await session.flush()
    return user


async def get_user_by_email(session, email: str):
    from models import User
    return (await session.execute(
        select(User).where(User.email == normalize_email(email))
    )).scalar_one_or_none()


async def authenticate(session, email: str, password: str):
    """The user if the password is right, else None. Never says which part failed."""
    try:
        user = await get_user_by_email(session, email)
    except AuthError:
        user = None
    # Run bcrypt in a thread (it takes ~200 ms) so the event loop keeps serving.
    # An unknown email, or a hash that is not bcrypt (the unusable placeholder),
    # still pays for one full check so timing does not reveal the account.
    if user is None or not user.password_hash.startswith("$2"):
        await asyncio.to_thread(verify_password, password, _DUMMY_HASH)
        return None
    ok = await asyncio.to_thread(verify_password, password, user.password_hash)
    return user if ok else None


async def create_session(session, user_id: int) -> str:
    from models import AuthSession
    raw = new_token()
    now = datetime.utcnow()
    session.add(AuthSession(token_hash=hash_token(raw), user_id=user_id,
                            created_at=now,
                            expires_at=now + timedelta(days=config.SESSION_TTL_DAYS)))
    await session.flush()
    return raw


async def resolve_session(session, raw: str | None) -> int | None:
    """The user id for a cookie value, or None. Slides the expiry forward daily.

    The slide COMMITS the session, so give this its own session; do not call it
    inside a transaction you still need. The slide is best-effort: if the
    database is locked it is rolled back and the (valid) session still resolves.
    """
    if not raw:
        return None
    from models import AuthSession
    row = await session.get(AuthSession, hash_token(raw))
    now = datetime.utcnow()
    if row is None or row.expires_at <= now:
        return None
    fresh_expiry = now + timedelta(days=config.SESSION_TTL_DAYS)
    user_id = row.user_id  # read before commit/rollback expire the row
    if fresh_expiry - row.expires_at > timedelta(days=1):
        row.expires_at = fresh_expiry
        try:
            await session.commit()
        except OperationalError:
            await session.rollback()
    return user_id


async def end_session(session, raw: str | None) -> None:
    if not raw:
        return
    from models import AuthSession
    await session.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(raw)))


async def create_invite(session, created_by: int | None, uses: int = 1,
                        expires_days: int | None = None) -> str:
    from models import Invite
    if uses < 1:
        raise AuthError("An invite needs at least one use.")
    raw = new_token()
    now = datetime.utcnow()
    session.add(Invite(code_hash=hash_token(raw), created_by=created_by, uses_left=uses,
                       expires_at=now + timedelta(days=expires_days) if expires_days else None,
                       created_at=now))
    await session.flush()
    return raw


async def signup(session, email: str, password: str, invite_code: str):
    """Create an account against an invite.

    The invite is validated and decremented before the email is inserted, so a
    junk code never reveals whether an email is registered. The password is
    hashed first so bcrypt never runs while SQLite's write lock is held.

    Everything happens in the caller's transaction: if the caller rolls back,
    the account and the invite use both go. A duplicate email (including a
    concurrent same-email race, caught by the UNIQUE constraint) ROLLS BACK THE
    CALLER'S SESSION, which restores the invite use, and raises AuthError.
    """
    from models import Invite
    email = normalize_email(email)
    check_password_policy(password)
    pw_hash = await asyncio.to_thread(hash_password, password)
    now = datetime.utcnow()
    res = await session.execute(
        update(Invite)
        .where(Invite.code_hash == hash_token(invite_code or ""),
               Invite.uses_left > 0,
               (Invite.expires_at.is_(None)) | (Invite.expires_at > now))
        .values(uses_left=Invite.uses_left - 1)
    )
    if res.rowcount != 1:
        raise AuthError("That invite code is not valid.")
    try:
        return await _insert_user(session, email, pw_hash)
    except IntegrityError:
        await session.rollback()
        raise AuthError("That email is already registered.") from None


class LoginRateLimiter:
    """In-memory failed-login counter. Single process, which is what we run.

    Memory is bounded: keys are truncated, empty ones dropped, blocked() never
    inserts, and past max_keys the dict is swept then (failing open) cleared.
    """

    def __init__(self, per_email: int = 5, per_ip: int = 20,
                 window_sec: float = 15 * 60, clock=time.monotonic,
                 max_keys: int = 50_000):
        self.per_email, self.per_ip, self.window = per_email, per_ip, window_sec
        self.clock = clock
        self.max_keys = max_keys
        self._email: dict[str, deque] = {}
        self._ip: dict[str, deque] = {}

    @staticmethod
    def _key(value: str) -> str:
        return (value or "").strip().lower()[:254]

    def _live(self, d: dict, key: str) -> int:
        """Entries inside the window for key; drops the key once it is empty."""
        q = d.get(key)
        if q is None:
            return 0
        cutoff = self.clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del d[key]
            return 0
        return len(q)

    def _enforce_cap(self, d: dict, name: str) -> None:
        if len(d) <= self.max_keys:
            return
        for k in list(d):
            self._live(d, k)
        if len(d) > self.max_keys:
            # Evict the oldest keys (insertion order; recent failures are moved
            # to the end), so a flood cannot wipe a victim's lockout.
            target = int(self.max_keys * 0.9)
            log.warning("login rate limiter %s table over %d keys; evicting oldest",
                        name, self.max_keys)
            while len(d) > target:
                del d[next(iter(d))]

    def blocked(self, email: str, ip: str) -> bool:
        return (self._live(self._email, self._key(email)) >= self.per_email
                or self._live(self._ip, self._key(ip)) >= self.per_ip)

    def record_failure(self, email: str, ip: str) -> None:
        now = self.clock()
        for d, key in ((self._email, self._key(email)), (self._ip, self._key(ip))):
            q = d.pop(key, None) or deque()
            q.append(now)
            d[key] = q  # re-insert: most recently used keys sit at the end
        self._enforce_cap(self._email, "email")
        self._enforce_cap(self._ip, "ip")

    def record_success(self, email: str) -> None:
        self._email.pop(self._key(email), None)
