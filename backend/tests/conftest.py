import sys
import pytest


@pytest.fixture(autouse=True)
def _reset_reloaded_modules():
    """Keep DB-reload tests isolated.

    Several tests call ``importlib.reload(database)``, which rebuilds the
    SQLAlchemy ``Base`` (and its empty metadata). ``models`` stays bound to the
    previous ``Base``, so a later reload-test would run ``init_db()`` against an
    empty metadata and fail with "no such table". Dropping the reloaded modules
    after each test forces the next reload-test to re-import ``models`` fresh,
    re-synced to the freshly reloaded ``Base``.
    """
    yield
    for name in ("main", "models"):
        sys.modules.pop(name, None)
