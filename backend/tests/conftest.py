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


import tenancy


@pytest.fixture(autouse=True)
def _default_user(request):
    """Run each test as user 1, the single-athlete world the existing tests
    were written for. Tests that manage the context themselves opt out with
    @pytest.mark.no_user."""
    if request.node.get_closest_marker("no_user"):
        yield
        return
    with tenancy.user_context(1):
        yield
