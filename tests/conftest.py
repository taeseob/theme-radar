import pytest

from tests import factories
from theme_radar.db import connect, migrate


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "theme_radar.sqlite3"


@pytest.fixture
def con(db_path):
    con = connect(db_path)
    migrate(con)
    yield con
    con.close()


@pytest.fixture
def market(con):
    return factories.build_market(con)
