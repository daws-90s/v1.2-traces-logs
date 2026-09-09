"""MySQL diagnostics through a dedicated read-only account
(MYSQL_RCA_USERNAME — see expense-mysql-v1/migrations/005_rca_readonly_user.sql,
which grants SELECT + PROCESS + REPLICATION CLIENT only, same shape as the
existing metrics_exporter user in 003_metrics_user.sql, never INSERT/UPDATE/
DELETE/DDL).

The only way to run a query through this client is by a fixed key into
app.security.permissions.SAFE_QUERY_LIBRARY (#21) — there is no method here
that accepts arbitrary SQL text, from an LLM or anywhere else.
"""
import logging

import pymysql

from app.config import settings
from app.security.permissions import PermissionDeniedError, assert_safe_sql

logger = logging.getLogger(__name__)


class DatasourceUnavailableError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"mysql unavailable: {detail}")


class MySQLReadOnlyClient:
    def _connect(self):
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_rca_username,
            password=settings.mysql_rca_password,
            database=settings.mysql_database,
            connect_timeout=settings.query_timeout_seconds,
            read_timeout=settings.query_timeout_seconds,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def run_safe_query(self, query_key: str) -> list[dict]:
        try:
            sql = assert_safe_sql(query_key)
        except PermissionDeniedError:
            raise
        try:
            conn = self._connect()
        except pymysql.MySQLError as exc:
            raise DatasourceUnavailableError(str(exc)) from exc
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
        except pymysql.MySQLError as exc:
            raise DatasourceUnavailableError(str(exc)) from exc
        finally:
            conn.close()

    def diagnostics_snapshot(self) -> dict:
        """A bundle of the safe queries an investigation typically wants
        together — connections, running threads, slow-query counter, max
        connections, current process list (truncated)."""
        snapshot = {}
        for key in (
            "innodb_status_connections",
            "innodb_status_running",
            "slow_queries",
            "max_connections",
            "aborted_connects",
        ):
            try:
                rows = self.run_safe_query(key)
                snapshot[key] = rows[0] if rows else None
            except DatasourceUnavailableError as exc:
                snapshot[key] = {"error": str(exc)}
        try:
            snapshot["processlist"] = self.run_safe_query("processlist")[:25]
        except DatasourceUnavailableError as exc:
            snapshot["processlist"] = {"error": str(exc)}
        return snapshot
