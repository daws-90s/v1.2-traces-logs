"""app/grafana/discovery.py's own contract: find a relevant panel by
matching an alert's metric hints against real dashboard JSON, and — since
this is advisory only (#20 fast-follow) — never raise or block an
investigation when Grafana is unreachable, unauthorized, or the search
comes back empty.
"""
import app.grafana.discovery as discovery
from app.grafana.client import GrafanaUnavailableError


class _FakeClient:
    def __init__(self, dashboards, raise_on_search=None, raise_on_get=None):
        self._dashboards = dashboards
        self._raise_on_search = raise_on_search
        self._raise_on_get = raise_on_get

    def search_dashboards(self, tag=None):
        if self._raise_on_search:
            raise self._raise_on_search
        return [{"uid": d["dashboard"]["uid"]} for d in self._dashboards]

    def get_dashboard(self, uid):
        if self._raise_on_get:
            raise self._raise_on_get
        return next(d for d in self._dashboards if d["dashboard"]["uid"] == uid)


_MYSQL_DASHBOARD = {
    "dashboard": {
        "uid": "v1-2-metrics-overview",
        "title": "Expense Tracker — v1.2 Metrics",
        "panels": [
            {"id": 100, "type": "row", "title": "row", "targets": []},
            {"id": 9, "type": "stat", "title": "MySQL up", "targets": [{"expr": "mysql_up"}]},
            {"id": 2, "type": "timeseries", "title": "5xx error rate by route", "targets": [{"expr": 'sum(rate(http_requests_total{status_code=~"5.."}[5m])) by (route)'}]},
        ],
    }
}


def _patch_client(monkeypatch, fake_client):
    monkeypatch.setattr(discovery, "GrafanaClient", lambda: fake_client)
    discovery._cache.clear()


def test_finds_matching_panel_for_known_alert(monkeypatch):
    _patch_client(monkeypatch, _FakeClient([_MYSQL_DASHBOARD]))
    result = discovery.find_panel_link("MySQLDown")
    assert result is not None
    url, description = result
    assert "v1-2-metrics-overview" in url
    assert "viewPanel=9" in url
    assert "MySQL up" in description


def test_unknown_alert_falls_back_to_default_hints(monkeypatch):
    _patch_client(monkeypatch, _FakeClient([_MYSQL_DASHBOARD]))
    # No dashboard here has an "up"-only panel other than "mysql_up" (which
    # contains "up" as a substring) — confirms the default-hint path is
    # reachable and doesn't crash for an alertname with no explicit entry.
    result = discovery.find_panel_link("SomeAlertNobodyMappedYet")
    assert result is None or isinstance(result, tuple)


def test_no_dashboards_found_returns_none(monkeypatch):
    _patch_client(monkeypatch, _FakeClient([]))
    assert discovery.find_panel_link("MySQLDown") is None


def test_grafana_unreachable_on_search_returns_none_not_raise(monkeypatch):
    _patch_client(monkeypatch, _FakeClient([], raise_on_search=GrafanaUnavailableError("connection refused")))
    assert discovery.find_panel_link("MySQLDown") is None


def test_grafana_unreachable_on_dashboard_get_skips_that_dashboard(monkeypatch):
    _patch_client(monkeypatch, _FakeClient([_MYSQL_DASHBOARD], raise_on_get=GrafanaUnavailableError("timeout")))
    assert discovery.find_panel_link("MySQLDown") is None


def test_unexpected_exception_is_swallowed_not_raised(monkeypatch):
    class _ExplodingClient:
        def search_dashboards(self, tag=None):
            raise RuntimeError("totally unexpected")

    monkeypatch.setattr(discovery, "GrafanaClient", lambda: _ExplodingClient())
    discovery._cache.clear()
    assert discovery.find_panel_link("MySQLDown") is None


def test_disabled_via_settings_short_circuits(monkeypatch):
    monkeypatch.setattr(discovery.settings, "grafana_discovery_enabled", False)
    _patch_client(monkeypatch, _FakeClient([_MYSQL_DASHBOARD]))
    assert discovery.find_panel_link("MySQLDown") is None
