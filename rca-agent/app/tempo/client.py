"""Read-only Tempo HTTP API client (search + fetch by trace ID). Tempo's
query surface is entirely read — there is no write endpoint to
accidentally reach here in the first place."""
import logging

import httpx

from app.config import settings
from app.prometheus.client import DatasourceUnavailableError

logger = logging.getLogger(__name__)


class TempoClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.tempo_url).rstrip("/")

    def _get(self, path: str, params: dict) -> dict:
        try:
            resp = httpx.get(
                f"{self.base_url}{path}", params=params, timeout=settings.query_timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise DatasourceUnavailableError("tempo", str(exc)) from exc

    def search(
        self,
        traceql: str,
        start_epoch_s: int,
        end_epoch_s: int,
        limit: int | None = None,
    ) -> list[dict]:
        params = {
            "q": traceql,
            "start": start_epoch_s,
            "end": end_epoch_s,
            "limit": min(limit or settings.max_trace_results, settings.max_trace_results),
        }
        data = self._get("/api/search", params)
        return data.get("traces", [])[: settings.max_trace_results]

    def get_trace(self, trace_id: str) -> dict:
        return self._get(f"/api/traces/{trace_id}", {})
