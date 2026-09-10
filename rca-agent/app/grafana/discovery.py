"""Dynamic dashboard/panel discovery (#20 fast-follow). Rather than
hardcoding a dashboard UID and panel ID per alert in this codebase — which
would silently go stale the moment someone edits
../grafana/dashboards/*.json — this searches Grafana's own /api/search +
/api/dashboards/uid/{uid} at investigation time and matches panels by the
metric names their PromQL targets actually reference. Same idea as
app/investigation/ask.py's keyword classifier, applied to panel exprs
instead of free-text questions.

Purely advisory: Grafana being slow, unreachable, or rejecting
settings.grafana_api_user/password never blocks or degrades an
investigation (#60-style graceful degradation) — it just means no
dashboard link gets attached to the RCA. Every failure path here returns
None rather than raising.
"""
import logging
import time

from app.config import settings
from app.grafana.client import GrafanaClient, GrafanaUnavailableError
from app.grafana.client import dashboard_url

logger = logging.getLogger(__name__)

# alertname -> substrings to look for in a panel's PromQL target exprs.
# First panel (across all dashboards, in search order) whose combined
# target exprs contain any of these wins. Matches this stack's real alert
# names (../prometheus/alert-rules.yml) on both sides of the rename in
# ../prometheus/slo-rules.yml.
_METRIC_HINTS: dict[str, tuple[str, ...]] = {
    "MySQLDown": ("mysql_up", "mysql_global_status_threads_connected"),
    "HighAPIp99Latency": ("http_request_duration_seconds", "0.99"),
    "BackendLatencyP95High": ("http_request_duration_seconds", "0.95"),
    "HTTP500High": ('status_code=~"5', "http_requests_total"),
    "Backend5xxRateHigh": ('status_code=~"5', "http_requests_total"),
    "HostMemoryHigh": ("node_memory_MemAvailable_bytes",),
    "HostCPUHigh": ("node_cpu_seconds_total",),
}
_DEFAULT_HINTS = ("up",)  # generic fallback: scrape/target health

_CACHE_TTL_S = 300
_cache: dict[str, tuple[float, list[dict]]] = {}


def _cached_dashboards() -> list[dict]:
    now = time.monotonic()
    hit = _cache.get("dashboards")
    if hit and now - hit[0] < _CACHE_TTL_S:
        return hit[1]

    client = GrafanaClient()
    found = client.search_dashboards(tag=settings.grafana_dashboard_tag)
    dashboards = []
    for entry in found:
        uid = entry.get("uid")
        if not uid:
            continue
        try:
            dashboards.append(client.get_dashboard(uid))
        except GrafanaUnavailableError as exc:
            logger.warning("could not fetch dashboard %s during discovery: %s", uid, exc)
    _cache["dashboards"] = (now, dashboards)
    return dashboards


def find_panel_link(alert_name: str, from_ms: int | None = None, to_ms: int | None = None) -> tuple[str, str] | None:
    """Returns (url, description) for the best-matching panel, or None if
    Grafana is unreachable/unauthorized or nothing matched. Callers must
    treat None as "no link available," never as an error."""
    if not settings.grafana_discovery_enabled:
        return None
    hints = _METRIC_HINTS.get(alert_name, _DEFAULT_HINTS)
    try:
        dashboards = _cached_dashboards()
        for dash in dashboards:
            model = dash.get("dashboard", {})
            uid = model.get("uid")
            title = model.get("title", uid)
            if not uid:
                continue
            for panel in model.get("panels", []):
                if panel.get("type") == "row":
                    continue
                exprs = " ".join(t.get("expr", "") for t in panel.get("targets", []) if isinstance(t, dict))
                if any(hint in exprs for hint in hints):
                    url = dashboard_url(uid, panel_id=panel.get("id"), from_ms=from_ms, to_ms=to_ms)
                    return url, f"{title} — {panel.get('title')}"
    except Exception as exc:  # noqa: BLE001 — discovery is advisory, must never fail an investigation
        logger.warning("Grafana dashboard discovery failed for alert %r: %s", alert_name, exc)
        return None
    return None
