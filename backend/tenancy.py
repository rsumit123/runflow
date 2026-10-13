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

from sqlalchemy import Column, ForeignKey, Integer, event, inspect as sa_inspect
from sqlalchemy.orm import Session, declared_attr, with_loader_criteria

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
    """Run the block as `user_id`. Nests, and restores the previous user on exit."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError(f"user_context needs a positive int user id, got {user_id!r}")
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


@contextmanager
def system_scope() -> Iterator[None]:
    """See every user's rows. For auth, webhook routing and migrations only."""
    token = _system.set(True)
    try:
        yield
    finally:
        _system.reset(token)


def _is_tenant(mapper) -> bool:
    cls = getattr(mapper, "class_", None)
    return cls is not None and issubclass(cls, TenantMixin)


@event.listens_for(Session, "do_orm_execute")
def _scope_to_current_user(state) -> None:
    if _system.get():
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    # Refreshing an already-loaded row's attributes, or loading its related
    # rows, needs no new filter: the parent was scoped when it was loaded,
    # and the criteria propagate to its loaders.
    if state.is_select and (state.is_column_load or state.is_relationship_load):
        return
    if not any(_is_tenant(m) for m in state.all_mappers):
        return
    uid = _current_user.get()
    if uid is None:
        raise NoUserContext("query touches a per-user table with no user in context")
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
    for obj in session.dirty:
        if isinstance(obj, TenantMixin) and sa_inspect(obj).attrs.user_id.history.has_changes():
            raise CrossTenantAccess(f"{type(obj).__name__}.user_id cannot be reassigned")
