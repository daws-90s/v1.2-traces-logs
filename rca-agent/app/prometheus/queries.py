"""PromQL query library, keyed by category (#14). Metric names below are
this stack's real, currently-scraped metrics (see
../../prometheus/prometheus.yml and expense-backend-v1.2/src/metrics.js) —
not assumed. Query selection per alert happens in app/playbooks/*.py, not
here; this module only builds the strings.
"""

# --- HTTP / RED metrics (backend, job="expense-backend") ---

def http_request_rate(window: str = "5m") -> str:
    return f'sum(rate(http_requests_total[{window}])) by (route, method)'


def http_error_rate_5xx(window: str = "5m") -> str:
    return (
        f'sum(rate(http_requests_total{{status_code=~"5.."}}[{window}])) '
        f'/ sum(rate(http_requests_total[{window}]))'
    )


def http_error_rate_4xx(window: str = "5m") -> str:
    return (
        f'sum(rate(http_requests_total{{status_code=~"4.."}}[{window}])) '
        f'/ sum(rate(http_requests_total[{window}]))'
    )


def http_latency_quantile(quantile: float, window: str = "5m", route: str | None = None) -> str:
    label_filter = f'route="{route}"' if route else ""
    inner = f'http_request_duration_seconds_bucket{{{label_filter}}}'
    return f'histogram_quantile({quantile}, sum(rate({inner}[{window}])) by (le, route))'


def http_requests_by_route_status(window: str = "5m") -> str:
    return f'sum(rate(http_requests_total[{window}])) by (route, status_code)'


# --- Target / instance health ---

def target_up(job: str | None = None) -> str:
    return f'up{{job="{job}"}}' if job else "up"


def mysql_up() -> str:
    return "mysql_up"


# --- MySQL (mysqld_exporter) ---

def mysql_connections_current() -> str:
    return "mysql_global_status_threads_connected"


def mysql_connections_max() -> str:
    return "mysql_global_variables_max_connections"


def mysql_connection_utilization() -> str:
    return (
        "mysql_global_status_threads_connected / mysql_global_variables_max_connections"
    )


def mysql_slow_queries_rate(window: str = "5m") -> str:
    return f"rate(mysql_global_status_slow_queries[{window}])"


def mysql_queries_rate(window: str = "5m") -> str:
    return f"rate(mysql_global_status_queries[{window}])"


def mysql_threads_running() -> str:
    return "mysql_global_status_threads_running"


# --- Nginx (nginx-exporter, stub_status) ---

def nginx_active_connections() -> str:
    return 'nginx_connections_active{job="nginx-frontend"}'


def nginx_requests_rate(window: str = "5m") -> str:
    return f'rate(nginx_http_requests_total{{job="nginx-frontend"}}[{window}])'


# --- Container resources (cAdvisor). Note: cAdvisor's "name" label is the
# raw containerd ID on this host, not "backend"/"frontend" — filter on
# "image" instead (see stack-components.md / docker-compose.yml comment). ---

def container_cpu_usage(image_substr: str, window: str = "5m") -> str:
    return (
        f'sum(rate(container_cpu_usage_seconds_total{{image=~".*{image_substr}.*"}}[{window}])) '
        f"by (image)"
    )


def container_memory_usage(image_substr: str) -> str:
    return f'sum(container_memory_usage_bytes{{image=~".*{image_substr}.*"}}) by (image)'


def container_restart_count(image_substr: str) -> str:
    return f'sum(container_start_time_seconds{{image=~".*{image_substr}.*"}}) by (image)'


# --- Host ---

def host_cpu_busy_pct(window: str = "5m") -> str:
    return f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{window}])))'


def host_memory_used_pct() -> str:
    return "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
