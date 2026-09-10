"""Grafana is used the way #20 recommends — as a link generator for humans,
not as an intermediary the agent queries through (the agent talks to
Prometheus/Loki/Tempo directly, see their own clients). Datasource UIDs
below match ../grafana/provisioning's own provisioned datasource names.

GrafanaClient below is the one exception: read-only /api/search and
/api/dashboards/uid/{uid} GETs, used by app/grafana/discovery.py to find
which panel to link to instead of a hardcoded UID. Same read-only
boundary as every other datasource client (#6/#35) — no dashboard,
datasource, or alert-rule write call exists here.
"""
import logging
import urllib.parse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DATASOURCES = {"prometheus": "Prometheus", "loki": "Loki", "tempo": "Tempo"}


class GrafanaUnavailableError(Exception):
    pass


class GrafanaClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.grafana_url).rstrip("/")
        self._auth = (settings.grafana_api_user, settings.grafana_api_password)

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = httpx.get(
                f"{self.base_url}{path}", params=params or {}, auth=self._auth, timeout=settings.query_timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise GrafanaUnavailableError(str(exc)) from exc

    def search_dashboards(self, tag: str | None = None) -> list[dict]:
        params = {"type": "dash-db"}
        if tag:
            params["tag"] = tag
        return self._get("/api/search", params)

    def get_dashboard(self, uid: str) -> dict:
        return self._get(f"/api/dashboards/uid/{uid}")


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
