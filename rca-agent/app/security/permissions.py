"""Enforces read-only behavior technically, not by prompting the LLM to
behave (agent-spec.md #36, #70). This module is the one place in the
codebase that knows what a "safe" MySQL statement looks like; every other
module that touches MySQL goes through it.

There is deliberately no execute_shell(), docker exec/restart/stop, or
generic SQL executor anywhere in this codebase (#22, #35). The LLM never
receives a tool object for those, so there is nothing for it to call even
if fully compromised by adversarial content in logs/traces/source code.
"""
import re

# Anything that isn't a plain read is rejected outright — allow-list, not
# deny-list, on the leading keyword.
_ALLOWED_SQL_PREFIXES = ("SELECT ", "SHOW ")

_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"REPLACE|CALL|LOAD\s+DATA|INTO\s+OUTFILE|SET\s)\b",
    re.IGNORECASE,
)

# Predefined, parameter-free diagnostic queries only (#21) — never built
# from LLM output or request input. Keys are what investigation code asks
# for by name; values are the literal SQL sent to MySQL.
SAFE_QUERY_LIBRARY = {
    "processlist": "SHOW PROCESSLIST",
    "status": "SHOW STATUS",
    "variables": "SHOW VARIABLES",
    "innodb_status_connections": "SHOW STATUS LIKE 'Threads_connected'",
    "innodb_status_running": "SHOW STATUS LIKE 'Threads_running'",
    "slow_queries": "SHOW STATUS LIKE 'Slow_queries'",
    "max_connections": "SHOW VARIABLES LIKE 'max_connections'",
    "aborted_connects": "SHOW STATUS LIKE 'Aborted_connects'",
}


class PermissionDeniedError(Exception):
    pass


def assert_safe_sql(query_key: str) -> str:
    """The only way to get SQL text out of this module — by a fixed key
    into SAFE_QUERY_LIBRARY, never by passing through caller-built text."""
    try:
        sql = SAFE_QUERY_LIBRARY[query_key]
    except KeyError:
        raise PermissionDeniedError(f"unknown query key: {query_key!r}")
    _assert_readonly_sql_text(sql)
    return sql


def _assert_readonly_sql_text(sql: str) -> None:
    stripped = sql.strip().upper()
    if not stripped.startswith(_ALLOWED_SQL_PREFIXES):
        raise PermissionDeniedError(f"statement is not a read: {sql!r}")
    if _FORBIDDEN_SQL_KEYWORDS.search(sql):
        raise PermissionDeniedError(f"statement contains a mutating keyword: {sql!r}")


# Docker operations this codebase is permitted to call. No docker-py method
# outside this set is ever invoked — see app/docker/readonly_client.py.
ALLOWED_DOCKER_OPERATIONS = frozenset({"list_containers", "inspect_container", "container_stats"})

# Explicit denylist mirrored from agent-spec.md #22, kept here as
# documentation and for the permissions test suite — nothing in this
# codebase calls docker.Client methods like these, and any future addition
# that does must not appear in ALLOWED_DOCKER_OPERATIONS above.
FORBIDDEN_DOCKER_OPERATIONS = frozenset({
    "exec_run", "restart", "stop", "remove", "kill", "update", "run", "create",
})
