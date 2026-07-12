from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

import os
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "training.db"))
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


_MIGRATIONS = {
    "activities": {
        "source": "TEXT DEFAULT 'strava'",
        "average_heartrate": "FLOAT",
        "max_heartrate": "FLOAT",
        "average_cadence": "FLOAT",
        "hr_zones": "JSON",
        "running_dynamics": "JSON",
    },
    "splits": {
        "average_cadence": "FLOAT",
    },
}


async def init_db() -> None:
    """Create all tables, then add any missing columns (idempotent)."""
    async with engine.begin() as conn:
        from models import Activity, Split, Stream  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        for table, cols in _MIGRATIONS.items():
            existing = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            have = {r[1] for r in existing.fetchall()}
            for col, decl in cols.items():
                if col not in have:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {col} {decl}"
                    )


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
