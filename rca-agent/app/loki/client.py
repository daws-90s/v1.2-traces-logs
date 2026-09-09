"""Read-only Loki HTTP API client. Only /loki/api/v1/{query_range,labels,
label/<name>/values} are called — no /loki/api/v1/push, no config/ruler
endpoints."""
import logging

import httpx

from app.config import settings
from app.prometheus.client import DatasourceUnavailableError

logger = logging.getLogger(__name__)


class LokiClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.loki_url).rstrip("/")

    def _get(self, path: str, params: dict) -> dict:
        try:
            resp = httpx.get(
                f"{self.base_url}{path}", params=params, timeout=settings.query_timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise DatasourceUnavailableError("loki", str(exc)) from exc

    def label_names(self) -> list[str]:
        data = self._get("/loki/api/v1/labels", {})
        return data.get("data", [])

    def label_values(self, label: str) -> list[str]:
        data = self._get(f"/loki/api/v1/label/{label}/values", {})
        return data.get("data", [])

    def query_range(
        self, logql: str, start_ns: int, end_ns: int, limit: int | None = None
    ) -> list[dict]:
        """Returns a flat list of {timestamp, line, stream} dicts, newest
        Loki API nesting already unwrapped — callers never touch the raw
        `data.result[].values[][]` shape."""
        params = {
            "query": logql,
            "start": start_ns,
            "end": end_ns,
            "limit": min(limit or settings.max_log_lines, settings.max_log_lines),
            "direction": "backward",
        }
        data = self._get("/loki/api/v1/query_range", params)
        lines = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                lines.append({"timestamp": ts, "line": line, "labels": labels})
        lines.sort(key=lambda x: x["timestamp"])
        return lines[: settings.max_log_lines]
