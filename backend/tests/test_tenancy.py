"""The isolation core: one place that scopes every query to the current user.

These use a private schema so they test the mechanism, not RunFlow's models.
"""
import tempfile

import pytest
import asyncio

from sqlalchemy import (Column, ForeignKey, Integer, String, delete, exists, func, insert,
                        literal_column, select, table, text, union, update)
from sqlalchemy import column as sa_column
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, joinedload, relationship, selectinload

import tenancy
from tenancy import CrossTenantAccess, NoUserContext, TenantMixin

pytestmark = pytest.mark.no_user  # these tests manage the context themselves


class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    items = relationship("_Item")


class _Item(TenantMixin, _Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    children = relationship("_Child", back_populates="item")


class _Child(TenantMixin, _Base):
    __tablename__ = "children"
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    label = Column(String)
    item = relationship("_Item", back_populates="children")


class _Note(_Base):
    """Not a tenant table: reaches tenant data only through a join or subquery."""
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer)
    txt = Column(String)


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
            s.add_all([_Note(id=1, item_id=10, txt="n1"), _Note(id=2, item_id=20, txt="n2")])
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


async def test_an_aggregate_with_no_user_raises_before_running():
    """A COUNT loads no rows, so only the pre-query check can refuse it."""
    maker = await _db()
    async with maker() as s:
        with pytest.raises(NoUserContext):
            await s.execute(select(func.count(_Item.id)))


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
            with pytest.raises(CrossTenantAccess):
                await s.execute(
                    select(_Item).from_statement(text("SELECT * FROM items")))


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


# ---- statements that reach a tenant table without a top-level tenant mapper ----

async def _as(uid, stmt, how="all"):
    maker = await _db()
    with tenancy.user_context(uid):
        async with maker() as s:
            r = await s.execute(stmt)
            return getattr(r, how)()


async def _all_items():
    maker = await _db()
    with tenancy.system_scope():
        async with maker() as s:
            return dict((await s.execute(select(_Item.id, _Item.name))).all())


async def test_count_star_via_select_from_is_scoped():
    assert await _as(2, select(func.count()).select_from(_Item), "scalar_one") == 1


async def test_count_star_via_select_from_with_no_user_raises():
    maker = await _db()
    async with maker() as s:
        with pytest.raises(NoUserContext):
            await s.execute(select(func.count()).select_from(_Item))


async def test_count_over_a_subquery_is_scoped():
    stmt = select(func.count()).select_from(select(_Item).subquery())
    assert await _as(2, stmt, "scalar_one") == 1


async def test_non_tenant_table_joined_to_a_tenant_table_is_scoped():
    stmt = select(_Note.txt).join(_Item, _Item.id == _Note.item_id)
    assert (await _as(2, stmt, "scalars")).all() == ["n2"]


async def test_in_subquery_on_a_tenant_table_is_scoped():
    stmt = select(_Note.txt).where(_Note.item_id.in_(select(_Item.id)))
    assert (await _as(2, stmt, "scalars")).all() == ["n2"]


async def test_scalar_subquery_is_scoped():
    stmt = select(select(func.count(_Item.id)).scalar_subquery())
    assert await _as(2, stmt, "scalar_one") == 1


async def test_union_is_scoped():
    stmt = union(select(_Item.name), select(_Child.label))
    assert sorted(r[0] for r in await _as(2, stmt)) == ["c2", "two-a"]


async def test_cte_is_scoped():
    stmt = select(literal_column("name")).select_from(select(_Item.name).cte("x"))
    assert [r[0] for r in await _as(2, stmt)] == ["two-a"]


async def test_exists_on_a_tenant_table_is_refused():
    for stmt in (select(exists().where(_Item.id == 10)),
                 select(_Note.txt).where(exists().where(_Item.id == _Note.item_id))):
        with pytest.raises(CrossTenantAccess):
            await _as(2, stmt)


async def test_core_table_select_is_refused():
    with pytest.raises(CrossTenantAccess):
        await _as(2, select(_Item.__table__))


async def test_select_of_a_plain_table_column_is_refused():
    with pytest.raises(CrossTenantAccess):
        await _as(2, select(_Item.__table__.c.name))


async def test_core_table_update_is_refused_and_modifies_nothing():
    with pytest.raises(CrossTenantAccess):
        await _as(2, update(_Item.__table__).values(name="x"))
    assert await _all_items() == {10: "one-a", 11: "one-b", 20: "two-a"}


async def test_insert_is_refused():
    with pytest.raises(CrossTenantAccess):
        await _as(2, insert(_Item).values(name="x", user_id=2))
    assert await _all_items() == {10: "one-a", 11: "one-b", 20: "two-a"}


async def _as_many(uid):
    maker = await _db()
    with tenancy.user_context(uid):
        async with maker() as s:
            await s.execute(insert(_Item), [{"name": "x", "user_id": uid},
                                            {"name": "y", "user_id": uid}])


async def test_insert_executemany_is_refused():
    with pytest.raises(CrossTenantAccess):
        await _as_many(2)
    assert await _all_items() == {10: "one-a", 11: "one-b", 20: "two-a"}


async def test_insert_with_no_user_raises_no_user_context():
    maker = await _db()
    async with maker() as s:
        with pytest.raises(NoUserContext):
            await s.execute(insert(_Item).values(name="x", user_id=1))


async def test_update_that_sets_user_id_is_refused_and_modifies_nothing():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            with pytest.raises(CrossTenantAccess):
                await s.execute(update(_Item).values(user_id=1))
    with tenancy.system_scope():
        async with maker() as s:
            owners = dict((await s.execute(select(_Item.id, _Item.user_id))).all())
    assert owners == {10: 1, 11: 1, 20: 2}


async def test_bulk_update_by_primary_key_with_user_id_is_refused():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            with pytest.raises(CrossTenantAccess):
                await s.execute(update(_Item), [{"id": 20, "user_id": 1}])
    with tenancy.system_scope():
        async with maker() as s:
            assert (await s.get(_Item, 20)).user_id == 2


async def test_editing_another_users_loaded_object_raises_on_flush():
    maker = await _db()
    async with maker() as s:
        with tenancy.user_context(1):
            row = await s.get(_Item, 10)
        with tenancy.user_context(2):
            row.name = "hijacked"
            with pytest.raises(CrossTenantAccess):
                await s.commit()
    assert (await _all_items())[10] == "one-a"


async def test_deleting_another_users_loaded_object_raises_on_flush():
    maker = await _db()
    async with maker() as s:
        with tenancy.user_context(1):
            row = await s.get(_Item, 10)
        with tenancy.user_context(2):
            await s.delete(row)
            with pytest.raises(CrossTenantAccess):
                await s.commit()
    assert 10 in await _all_items()


async def test_refreshing_another_users_object_does_not_return_its_data():
    maker = await _db()
    async with maker() as s:
        with tenancy.user_context(1):
            row = await s.get(_Item, 10)
        s.expire(row)
        with tenancy.user_context(2):
            with pytest.raises(CrossTenantAccess):
                await s.refresh(row)
        assert "name" not in row.__dict__  # nothing was re-read into the object


async def test_system_scope_wins_over_a_nested_user_context():
    maker = await _db()
    with tenancy.system_scope():
        with tenancy.user_context(2):
            async with maker() as s:
                n = (await s.execute(select(func.count(_Item.id)))).scalar_one()
    assert n == 3


async def test_one_session_queried_under_two_users_sees_each_users_own_rows():
    maker = await _db()
    async with maker() as s:
        with tenancy.user_context(1):
            a = (await s.execute(select(_Item.name).order_by(_Item.id))).scalars().all()
        with tenancy.user_context(2):
            b = (await s.execute(select(_Item.name).order_by(_Item.id))).scalars().all()
    assert a == ["one-a", "one-b"]
    assert b == ["two-a"]


async def test_concurrent_workers_each_see_only_their_own_rows():
    maker = await _db()

    async def worker(uid):
        with tenancy.user_context(uid):
            out = []
            for _ in range(5):
                async with maker() as s:
                    out.append((await s.execute(
                        select(_Item.user_id).distinct())).scalars().all())
                await asyncio.sleep(0)
            return out

    results = await asyncio.gather(worker(1), worker(2), worker(1), worker(2))
    for uid, res in zip((1, 2, 1, 2), results):
        assert all(r == [uid] for r in res), (uid, res)


# ---- eager loads must keep working, and stay scoped ----

async def test_selectinload_one_to_many_from_a_tenant_parent():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            items = (await s.execute(
                select(_Item).options(selectinload(_Item.children)))).scalars().all()
            assert [(i.name, [c.label for c in i.children]) for i in items] == [
                ("two-a", ["c2"])]


async def test_selectinload_many_to_one():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            kids = (await s.execute(
                select(_Child).options(selectinload(_Child.item)))).scalars().all()
            assert [(c.label, c.item.name) for c in kids] == [("c2", "two-a")]


async def test_selectinload_from_a_non_tenant_parent_is_scoped():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            users = (await s.execute(
                select(_User).where(_User.id == 2)
                .options(selectinload(_User.items)))).scalars().all()
            assert [[i.name for i in u.items] for u in users] == [["two-a"]]
    with tenancy.user_context(2):
        async with maker() as s:  # asking for user 1's row still gets only user 2's items
            users = (await s.execute(
                select(_User).options(selectinload(_User.items)))).scalars().all()
            assert sorted(i.name for u in users for i in u.items) == ["two-a"]


async def test_selectinload_with_no_user_raises():
    maker = await _db()
    async with maker() as s:
        with pytest.raises(NoUserContext):
            await s.execute(select(_Item).options(selectinload(_Item.children)))


async def test_joinedload_one_to_many_is_scoped():
    maker = await _db()
    with tenancy.user_context(2):
        async with maker() as s:
            items = (await s.execute(
                select(_Item).options(joinedload(_Item.children)))).unique().scalars().all()
            assert [(i.name, [c.label for c in i.children]) for i in items] == [
                ("two-a", ["c2"])]


# ---- lightweight sqlalchemy.table() is a Core reference too ----

_LIGHT = lambda: table("items", sa_column("name"))  # noqa: E731


async def _refused_and_unchanged(stmt):
    maker = await _db()
    for ctx in (tenancy.user_context(2), None):
        async with maker() as s:
            if ctx is None:
                with pytest.raises(NoUserContext):
                    await s.execute(stmt)
            else:
                with ctx:
                    with pytest.raises(CrossTenantAccess):
                        await s.execute(stmt)
    assert await _all_items() == {10: "one-a", 11: "one-b", 20: "two-a"}


async def test_lightweight_table_select_is_refused():
    await _refused_and_unchanged(select(sa_column("name")).select_from(_LIGHT()))


async def test_lightweight_table_count_is_refused():
    await _refused_and_unchanged(select(func.count()).select_from(_LIGHT()))


async def test_lightweight_table_update_is_refused():
    await _refused_and_unchanged(update(_LIGHT()).values(name="x"))


async def test_lightweight_table_delete_is_refused():
    await _refused_and_unchanged(delete(table("items")))
