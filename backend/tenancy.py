"""Per-user data isolation, enforced in one place.

Every data table carries a `user_id`. Rather than trusting each of ~90
hand-written queries to remember `WHERE user_id = ?`, session events do it for
all of them:

  * every ORM select, update and delete that touches a tenant table gets
    `user_id = <current user>` added;
  * every new tenant row is stamped with the current user;
  * a row that arrives belonging to someone else raises.

With no user in context, touching a tenant table raises. Nothing ever falls
back to a default user. The only way to cross users is `system_scope()`, which
is meant to be rare and easy to grep for.

Kept separate from `database.py` on purpose: tests reload `database`, and a
reload would replace the ContextVar and lose these listeners. Never reload
this module.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Column, ForeignKey, Integer, Table, event, inspect as sa_inspect
from sqlalchemy.orm import Session, declared_attr, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.selectable import Exists
from sqlalchemy.sql.util import find_tables

_current_user: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "runflow_current_user", default=None)
_system: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "runflow_system_scope", default=False)


class NoUserContext(RuntimeError):
    """A per-user table was touched with no user in context."""


class CrossTenantAccess(RuntimeError):
    """A row belonging to another user was loaded or written."""


class TenantMixin:
    """Marks a model as owned by a user, and gives it `user_id`."""

    @declared_attr
    def user_id(cls):  # noqa: N805 - declared_attr receives the class
        return Column(Integer, ForeignKey("users.id"), nullable=False, index=True)


def current_user_id() -> int | None:
    return _current_user.get()


def in_system_scope() -> bool:
    return _system.get()


@contextmanager
def user_context(user_id: int) -> Iterator[None]:
    """Run the block as `user_id`. Nests, and restores the previous user on exit.

    Inside `system_scope()` this has no effect on filtering: system scope wins.
    """
    if type(user_id) is not int or user_id <= 0:
        raise ValueError(f"user_context needs a positive int user id, got {user_id!r}")
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


@contextmanager
def system_scope() -> Iterator[None]:
    """See every user's rows. For auth, webhook routing and migrations only.

    System scope wins over a nested `user_context`: inside it, filtering and
    ownership checks are off even if a user is also set.
    """
    token = _system.set(True)
    try:
        yield
    finally:
        _system.reset(token)


def _tenant_table_names() -> set[str]:
    """Names of every table owned by a TenantMixin subclass.

    Walked on each call rather than cached: tests reload `models`, which creates
    fresh classes, and a stale cache would silently stop scoping the new ones.
    """
    names: set[str] = set()
    stack = list(TenantMixin.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        table = getattr(cls, "__table__", None)
        if table is not None:
            names.add(table.name)
    return names


def _is_tenant(mapper) -> bool:
    cls = getattr(mapper, "class_", None)
    return cls is not None and issubclass(cls, TenantMixin)


def _tenant_tables_in(stmt, tenant_names: set[str]) -> set[str]:
    """Tenant tables referenced ANYWHERE in the statement (subqueries, joins,
    CTEs, unions, select_from, CRUD targets), not just by its top-level mapper."""
    found = find_tables(stmt, check_columns=True, include_aliases=True,
                        include_joins=True, include_selects=True, include_crud=True)
    return {t.name for t in found if isinstance(t, Table)} & tenant_names


def _entity_table_names(element) -> set[str]:
    ent = element._annotations.get("parententity")
    try:
        return {t.name for t in ent.mapper.tables}
    except AttributeError:
        return set()


def _uses_core_reference(stmt, tenant_names: set[str]) -> bool:
    """True if the statement names a tenant table or column without going
    through an ORM entity. with_loader_criteria only filters ORM entities, so
    these cannot be scoped and must be refused.

    `select(Item.name)` legitimately also yields the raw Table (the column's
    `.table`), so a raw Table only counts when no ORM-annotated element in the
    statement refers to it.
    """
    raw_tables: set[str] = set()
    orm_tables: set[str] = set()
    for el in visitors.iterate(stmt):
        annotated = "parententity" in getattr(el, "_annotations", {})
        if annotated:
            orm_tables |= _entity_table_names(el)
        elif isinstance(el, Table):
            if el.name in tenant_names:
                raw_tables.add(el.name)
        elif isinstance(el, Column):
            # ORM-built columns carry "parententity" (select(Item.name)) or only
            # "_orm_adapt" (the WHERE clause Session.get builds). A column with
            # neither came from table.c.<name>.
            if ("_orm_adapt" not in el._annotations
                    and getattr(el.table, "name", None) in tenant_names):
                return True
    return bool(raw_tables - orm_tables)


def _has_tenant_exists(stmt, tenant_names: set[str]) -> bool:
    """EXISTS (...) subqueries are not reached by loader criteria."""
    for el in visitors.iterate(stmt):
        if isinstance(el, Exists) and _tenant_tables_in(el, tenant_names):
            return True
    return False


def _sets_user_id(state) -> bool:
    """Does this UPDATE assign `user_id`, via .values() or bulk parameters?"""
    stmt = state.statement
    keys: list = []
    if getattr(stmt, "_values", None):
        keys.extend(stmt._values.keys())
    if getattr(stmt, "_ordered_values", None):
        keys.extend(k for k, _ in stmt._ordered_values)
    for batch in getattr(stmt, "_multi_values", None) or ():
        for row in batch:
            keys.extend(row.keys())
    params = state.parameters
    if isinstance(params, dict):
        keys.extend(params.keys())
    elif params:
        for p in params:
            keys.extend(p.keys())
    return any(getattr(k, "key", k) == "user_id" for k in keys)


@event.listens_for(Session, "do_orm_execute")
def _scope_to_current_user(state) -> None:
    if _system.get():
        return
    names = _tenant_table_names()
    touched = _tenant_tables_in(state.statement, names)
    if not touched:
        return
    # Column loads and relationship loads are deliberately NOT skipped: they
    # carry the same criteria. (Refreshing an existing object is the one load
    # the criteria do not reach; the "refresh" listener below covers that.)
    uid = _current_user.get()
    if uid is None:
        raise NoUserContext("query touches a per-user table with no user in context")
    if state.is_insert:
        raise CrossTenantAccess(
            "bulk insert() into a per-user table is not allowed; use session.add")
    if _uses_core_reference(state.statement, names) or _has_tenant_exists(state.statement, names):
        raise CrossTenantAccess(
            "statement reaches a per-user table in a way that cannot be scoped "
            "(Core table/column reference or EXISTS); use the ORM entity")
    if state.is_update and _sets_user_id(state):
        raise CrossTenantAccess("user_id cannot be reassigned")
    state.statement = state.statement.options(
        with_loader_criteria(TenantMixin, lambda cls: cls.user_id == uid,
                             include_aliases=True)
    )


@event.listens_for(TenantMixin, "load", propagate=True)
def _refuse_foreign_rows(target, context) -> None:
    """Backstop: a row that got past the criteria (e.g. from text) must not escape."""
    if _system.get():
        return
    uid = _current_user.get()
    if uid is None:
        raise NoUserContext(f"{type(target).__name__} loaded with no user in context")
    if target.user_id != uid:
        raise CrossTenantAccess(
            f"{type(target).__name__} belonging to another user was loaded")


@event.listens_for(TenantMixin, "refresh", propagate=True)
def _refuse_foreign_refresh(target, context, attrs) -> None:
    """with_loader_criteria is not applied when an existing object is refreshed
    or has expired attributes reloaded, so check ownership once the row is in.
    The object is expired again so the foreign data is not left readable."""
    if _system.get():
        return
    uid = _current_user.get()
    owner = target.__dict__.get("user_id")
    if uid is not None and owner == uid:
        return
    session = sa_inspect(target).session
    if session is not None:
        session.expire(target)
    if uid is None:
        raise NoUserContext(f"{type(target).__name__} refreshed with no user in context")
    raise CrossTenantAccess(
        f"{type(target).__name__} belonging to another user (or of unverifiable "
        "owner) was refreshed")


@event.listens_for(Session, "before_flush")
def _stamp_and_guard_owner(session, flush_context, instances) -> None:
    system = _system.get()
    uid = _current_user.get()
    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        if obj.user_id is None:
            if uid is None:
                raise NoUserContext(
                    f"new {type(obj).__name__} with no user in context")
            obj.user_id = uid
        elif not system and obj.user_id != uid:
            raise CrossTenantAccess(
                f"new {type(obj).__name__} written for another user")
    if system:
        return
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantMixin):
            continue
        if uid is None:
            raise NoUserContext(f"{type(obj).__name__} changed with no user in context")
        if obj.user_id != uid:
            raise CrossTenantAccess(
                f"{type(obj).__name__} belonging to another user cannot be changed or deleted")
    for obj in session.dirty:
        if isinstance(obj, TenantMixin) and sa_inspect(obj).attrs.user_id.history.has_changes():
            raise CrossTenantAccess(f"{type(obj).__name__}.user_id cannot be reassigned")
