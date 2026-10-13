"""The isolation core: one place that scopes every query to the current user.

These use a private schema so they test the mechanism, not RunFlow's models.
"""
import tempfile

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, func, select, text, update, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

import tenancy
from tenancy import CrossTenantAccess, NoUserContext, TenantMixin

pytestmark = pytest.mark.no_user  # these tests manage the context themselves


class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)


class _Item(TenantMixin, _Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)


class _Child(TenantMixin, _Base):
    __tablename__ = "children"
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    label = Column(String)


async def _db():
    engine = create_async_engine(f"sqlite+aiosqlite:///{tempfile.mktemp(suffix='.db')}")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Seed two users' rows. system_scope lets one session write for both.
    with tenancy.system_scope():
        async with maker() as s:
            s.add_all([_User(id=1), _User(id=2)])
            s.add_all([_Item(id=10, name="one-a", user_id=1),
                       _Item(id=11, name="one-b", user_id=1),
                       _Item(id=20, name="two-a", user_id=2)])
            s.add_all([_Child(item_id=10, label="c1", user_id=1),
                       _Child(item_id=20, label="c2", user_id=2)])
            await s.commit()
    return maker


async def test_select_returns_only_the_current_users_rows():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            names = (await s.execute(select(_Item.name))).scalars().all()
    assert names == ["two-a"]


async def test_count_is_scoped():
    maker = await _db()
    with tenancy.user_context(1):
        async with maker() as s:
            n = (await s.execute(select(func.count(_Item.id)))).scalar_one()
    assert n == 2


async def test_query_with_no_user_raises():
    maker = await _db()
    async with maker() as s:
        with pytest.raises(NoUserContext):
            await s.execute(select(_Item))


async def test_session_get_for_another_users_row_returns_none():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:  # fresh session: no identity-map shortcut
            assert await s.get(_Item, 10) is None
            assert (await s.get(_Item, 20)).name == "two-a"


async def test_children_queried_directly_are_scoped():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            labels = (await s.execute(select(_Child.label))).scalars().all()
            joined = (await s.execute(
                select(_Child.label).join(_Item, _Item.id == _Child.item_id)
            )).scalars().all()
    assert labels == ["c2"]
    assert joined == ["c2"]


async def test_orm_update_and_delete_touch_only_the_current_users_rows():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            await s.execute(update(_Item).values(name="renamed"))
            await s.execute(delete(_Child))
            await s.commit()
    with tenancy.system_scope():
        async with maker() as s:
            names = dict((await s.execute(select(_Item.id, _Item.name))).all())
            children = (await s.execute(select(_Child.user_id))).scalars().all()
    assert names == {10: "one-a", 11: "one-b", 20: "renamed"}
    assert children == [1]


async def test_new_row_is_stamped_with_the_current_user():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            s.add(_Item(name="fresh"))
            await s.commit()
    with tenancy.system_scope():
        async with maker() as s:
            row = (await s.execute(select(_Item).where(_Item.name == "fresh"))).scalar_one()
    assert row.user_id == 2


async def test_new_row_with_no_user_raises():
    maker = await _db()
    async with maker() as s:
        s.add(_Item(name="orphan"))
        with pytest.raises(NoUserContext):
            await s.commit()


async def test_writing_a_row_for_another_user_raises():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            s.add(_Item(name="forged", user_id=1))
            with pytest.raises(CrossTenantAccess):
                await s.commit()


async def test_reassigning_a_rows_owner_raises():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            row = await s.get(_Item, 20)
            row.user_id = 1
            with pytest.raises(CrossTenantAccess):
                await s.commit()


async def test_a_textual_statement_cannot_leak_another_users_rows():
    """from_statement bypasses criteria; the load-time check must still catch it."""
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            try:
                rows = (await s.execute(
                    select(_Item).from_statement(text("SELECT * FROM items"))
                )).scalars().all()
            except CrossTenantAccess:
                return
    assert all(r.user_id == 2 for r in rows), [r.user_id for r in rows]


async def test_system_scope_sees_every_user():
    maker = await _db()
    with tenancy.system_scope():
        async with maker() as s:
            n = (await s.execute(select(func.count(_Item.id)))).scalar_one()
    assert n == 3


def test_user_context_restores_the_previous_value():
    assert tenancy.current_user_id() is None
    with tenancy.user_context(5):
        with tenancy.user_context(6):
            assert tenancy.current_user_id() == 6
        assert tenancy.current_user_id() == 5
    assert tenancy.current_user_id() is None


@pytest.mark.parametrize("bad", [0, -1, None, "1", True])
def test_user_context_rejects_non_ids(bad):
    with pytest.raises(ValueError):
        with tenancy.user_context(bad):
            pass
