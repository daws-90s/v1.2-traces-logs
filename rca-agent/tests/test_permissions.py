import pytest

from app.security.permissions import PermissionDeniedError, assert_safe_sql


def test_known_safe_query_returns_sql():
    assert assert_safe_sql("processlist") == "SHOW PROCESSLIST"


def test_unknown_query_key_rejected():
    with pytest.raises(PermissionDeniedError):
        assert_safe_sql("drop_everything")


@pytest.mark.parametrize("key", ["processlist", "status", "variables", "max_connections"])
def test_all_library_entries_are_reads(key):
    sql = assert_safe_sql(key)
    assert sql.strip().upper().startswith(("SELECT", "SHOW"))
