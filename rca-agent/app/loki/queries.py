"""LogQL query builders. Labels used (`compose_service`, `service_name`)
are exactly what alloy/config.alloy actually attaches — confirmed against
that file, not assumed (#18 requires discovering labels rather than
guessing them; label_names()/label_values() in client.py exist for the
cases a playbook needs to verify that at runtime).

`service_name` values are "expense-backend"/"expense-frontend" (alloy
overrides them to match the trace's service.name resource attribute);
`compose_service` values are the plain compose service names
("backend"/"frontend"/"mysql").
"""

_ERROR_TERMS = ("error", "Error", "ERROR", "exception", "timeout", "ECONNREFUSED", "ETIMEDOUT")


def backend_errors() -> str:
    return '{compose_service="backend"} |~ "(?i)error|exception|timeout"'


def backend_status_5xx() -> str:
    return '{compose_service="backend"} | json | status_code >= 500'


def frontend_upstream_errors() -> str:
    return '{compose_service="frontend"} |~ "(?i)upstream.*(error|timed out|refused)"'


def mysql_logs() -> str:
    return '{compose_service="mysql"}'


def mysql_connection_errors() -> str:
    return '{compose_service="backend"} |~ "(?i)(ECONNREFUSED|ETIMEDOUT|PROTOCOL_CONNECTION_LOST|connect ECONNREFUSED|Too many connections)"'


def by_service(compose_service: str, needle: str | None = None) -> str:
    base = f'{{compose_service="{compose_service}"}}'
    return f'{base} |~ "(?i){needle}"' if needle else base


def trace_id_search(trace_id: str) -> str:
    """Correlates a Tempo trace back to logs the same way Grafana's own
    "Logs for this span" button does (README's Bridge #3) — a plain text
    search, deliberately not a label lookup (trace_id is unbounded
    cardinality, wrong shape for a Loki label)."""
    return f'{{}} |= "{trace_id}"'
