"""Grafana is used the way #20 recommends — as a link generator for humans,
not as an intermediary the agent queries through (the agent talks to
Prometheus/Loki/Tempo directly, see their own clients). Datasource UIDs
below match ../grafana/provisioning's own provisioned datasource names."""
import urllib.parse

from app.config import settings

_DATASOURCES = {"prometheus": "Prometheus", "loki": "Loki", "tempo": "Tempo"}


def explore_url(datasource: str, query: str, from_ms: int, to_ms: int) -> str:
    ds_name = _DATASOURCES.get(datasource, datasource)
    pane = {
        "datasource": ds_name,
        "queries": [{"refId": "A", "expr": query, "query": query}],
        "range": {"from": str(from_ms), "to": str(to_ms)},
    }
    left = urllib.parse.quote(_to_json(pane))
    return f"{settings.grafana_url}/explore?left={left}"


def dashboard_url(uid: str, panel_id: int | None = None, from_ms: int | None = None, to_ms: int | None = None) -> str:
    url = f"{settings.grafana_url}/d/{uid}"
    params = {}
    if panel_id is not None:
        params["viewPanel"] = panel_id
    if from_ms is not None:
        params["from"] = from_ms
    if to_ms is not None:
        params["to"] = to_ms
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def trace_url(trace_id: str) -> str:
    return f"{settings.grafana_url}/explore?left=" + urllib.parse.quote(
        _to_json({"datasource": "Tempo", "queries": [{"refId": "A", "query": trace_id}]})
    )


def _to_json(obj) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))
