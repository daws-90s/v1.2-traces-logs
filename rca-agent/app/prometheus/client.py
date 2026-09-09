"""Read-only Prometheus HTTP API client. Only /api/v1/query, /query_range
and /api/v1/targets are ever called — nothing under Prometheus's admin API
(/-/reload, /api/v1/admin/*) is wired up anywhere in this codebase."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class DatasourceUnavailableError(Exception):
    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"{source} unavailable: {detail}")


class PrometheusClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")

    def _get(self, path: str, params: dict) -> dict:
        try:
            resp = httpx.get(
                f"{self.base_url}{path}", params=params, timeout=settings.query_timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise DatasourceUnavailableError("prometheus", str(exc)) from exc

    def instant_query(self, expr: str, time_: str | None = None) -> list[dict]:
        params = {"query": expr}
        if time_:
            params["time"] = time_
        data = self._get("/api/v1/query", params)
        return data.get("data", {}).get("result", [])

    def range_query(self, expr: str, start: str, end: str, step: str = "30s") -> list[dict]:
        params = {"query": expr, "start": start, "end": end, "step": step}
        data = self._get("/api/v1/query_range", params)
        return data.get("data", {}).get("result", [])

    def target_health(self, job: str | None = None) -> list[dict]:
        data = self._get("/api/v1/targets", {})
        active = data.get("data", {}).get("activeTargets", [])
        if job:
            active = [t for t in active if t.get("labels", {}).get("job") == job]
        return active
