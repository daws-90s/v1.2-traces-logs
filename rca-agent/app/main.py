"""FastAPI entrypoint: POST /webhook/alertmanager (#9), GET /health, /ready
(#41), GET /metrics (#42, this agent's own self-monitoring). No other
routes exist — in particular, nothing here accepts arbitrary commands or
SQL from a caller.
"""
import json
import logging

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app import metrics
from app.alertmanager.models import AlertmanagerWebhookPayload
from app.config import settings
from app.investigation.orchestrator import handle_webhook_alert
from app.state import store


class _JsonFormatter(logging.Formatter):
    """Structured JSON logs (#43) — never includes raw exception text from
    a place that might carry a secret; standard %-formatted messages only."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), handlers=[_handler])
logger = logging.getLogger("rca_agent")

app = FastAPI(title="Observability RCA Agent", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    logger.info("rca-agent started (max_investigation_seconds=%s, max_tool_calls=%s)", settings.max_investigation_seconds, settings.max_tool_calls)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    """Best-effort reachability check of required dependencies (#41) — a
    slow/unreachable datasource degrades an investigation's confidence
    (#60) rather than blocking readiness entirely, so this stays a
    lightweight signal, not a full connectivity test."""
    checks = {"database": True}
    try:
        store.init_db()
    except Exception as exc:  # noqa: BLE001
        checks["database"] = False
        logger.error("readiness check: state db unavailable: %s", exc)
    checks["slack_configured"] = bool(settings.slack_bot_token and settings.slack_channel_id)
    checks["llm_configured"] = bool(settings.llm_api_key)
    overall = checks["database"]
    return JSONResponse(status_code=200 if overall else 503, content={"status": "ready" if overall else "not_ready", "checks": checks})


@app.get("/metrics")
def metrics_endpoint():
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


@app.post("/webhook/alertmanager")
def alertmanager_webhook(payload: AlertmanagerWebhookPayload):
    """Alertmanager (alertmanager/alertmanager.yml's webhook_configs entry)
    posts here for every group of firing/resolved alerts. Each alert in the
    group is dispatched independently — Alertmanager's own grouping
    (group_by: [alertname]) can bundle unrelated instances together."""
    for alert in payload.alerts:
        try:
            handle_webhook_alert(alert)
        except Exception:  # noqa: BLE001 — one malformed alert must not break the rest of the group
            logger.exception("failed to handle alert fingerprint=%s", alert.fingerprint)
    return {"received": len(payload.alerts)}
