import pytest

from app.storage.database import check_connection


def test_postgresql_connection():
    try:
        assert check_connection() is True
    except RuntimeError as exc:
        pytest.fail(str(exc))
