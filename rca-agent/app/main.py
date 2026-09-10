"""FastAPI entrypoint: POST /webhook/alertmanager (#9), POST /investigate
(manual trigger — not in the spec, added so an alert doesn't have to
actually fire in Prometheus to run/demo a playbook), POST /ask (free-text
question, also not in the spec — "investigate a recent high latency
request and tell me the reason" — answered synchronously in the HTTP
response), GET /health, /ready (#41), GET /metrics (#42, this agent's own
self-monitoring). Every route here only ever starts a read-only
investigation or reads agent state — none of them accept arbitrary
commands or SQL from a caller.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import metrics
from app.alertmanager.models import AlertmanagerAlert, AlertmanagerWebhookPayload
from app.config import settings
from app.investigation.ask import handle_ask
from app.investigation.orchestrator import handle_webhook_alert
from app.playbooks.registry import get_playbook
from app.security.ratelimit import RateLimitExceeded, SlidingWindowRateLimiter
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

# Shared across /investigate and /ask (not one limiter per route) — a
# caller hammering both endpoints alternately should still be capped by
# one combined budget, not get 2x the allowance by switching routes.
_manual_trigger_limiter = SlidingWindowRateLimiter(
    max_requests=settings.manual_trigger_rate_limit,
    window_seconds=settings.manual_trigger_rate_limit_window_seconds,
)


def _enforce_rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    try:
        _manual_trigger_limiter.check(key)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({settings.manual_trigger_rate_limit} requests per "
            f"{settings.manual_trigger_rate_limit_window_seconds}s) — retry after {exc.retry_after_seconds:.0f}s",
        )


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


class ManualInvestigationRequest(BaseModel):
    # Matches an existing playbook name in app/playbooks/registry.py
    # (MySQLDown, HighAPIp99Latency, HTTP500High, ...) to run that
    # playbook; anything else falls through to DefaultPlaybook, same as a
    # real, unrecognized Alertmanager alertname would.
    alert_name: str
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    severity: str = "warning"
    # Left unset for a fresh incident each call; pass the same value twice
    # to exercise dedup (#30) against an in-progress investigation instead
    # — or to "resolve" a manually-triggered incident (status="resolved")
    # and see the resolution Slack message (#31).
    fingerprint: str | None = None
    status: str = "firing"  # "firing" | "resolved"


@app.post("/investigate")
def manual_investigate(req: ManualInvestigationRequest, request: Request, x_rca_trigger_token: str | None = Header(default=None)):
    """Runs the investigation pipeline exactly as the real webhook would,
    without waiting for Prometheus to actually breach a threshold — useful
    for demoing a specific playbook (MySQLDown, HighAPIp99Latency with a
    `route` label, HTTP500High, ...) on demand. See rca-agent/README.md
    for curl examples per playbook."""
    if settings.manual_trigger_token and x_rca_trigger_token != settings.manual_trigger_token:
        raise HTTPException(status_code=403, detail="missing or invalid X-RCA-Trigger-Token")
    _enforce_rate_limit(request)
    if req.status not in ("firing", "resolved"):
        raise HTTPException(status_code=400, detail='status must be "firing" or "resolved"')
    if req.status == "resolved" and not req.fingerprint:
        raise HTTPException(status_code=400, detail="resolving requires the fingerprint returned by the earlier firing call")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fingerprint = req.fingerprint or f"manual-{uuid.uuid4()}"
    alert = AlertmanagerAlert(
        status=req.status,
        labels={"alertname": req.alert_name, "severity": req.severity, **req.labels},
        annotations=req.annotations,
        startsAt=now,
        endsAt=now if req.status == "resolved" else None,
        generatorURL=None,
        fingerprint=fingerprint,
    )
    # get_active_by_fingerprint excludes RESOLVED incidents, so for a
    # resolve call the incident must be looked up *before* handling it
    # (while it's still active) — right after, it's already RESOLVED.
    incident_id = None
    if req.status == "resolved":
        existing = store.get_active_by_fingerprint(fingerprint)
        incident_id = existing.incident_id if existing else None
        handle_webhook_alert(alert)
    else:
        handle_webhook_alert(alert)
        incident = store.get_active_by_fingerprint(fingerprint)
        incident_id = incident.incident_id if incident else None

    return {
        "incident_id": incident_id,
        "fingerprint": fingerprint,
        "playbook": get_playbook(req.alert_name).name,
        "note": "investigation runs in the background — watch Slack, or `docker compose logs -f rca-agent`",
    }


class AskRequest(BaseModel):
    question: str
    # Overrides settings.ask_max_investigation_seconds; always capped at
    # settings.max_investigation_seconds regardless of what's requested.
    timeout_seconds: int | None = None


@app.post("/ask")
def ask(req: AskRequest, request: Request, x_rca_trigger_token: str | None = Header(default=None)):
    """Free-text investigation, e.g. {"question": "investigate a recent
    high latency request and tell me the reason"}. Unlike /investigate,
    this blocks until the investigation finishes (or times out) and
    returns the answer directly in the response — no need to go check
    Slack. See rca-agent/README.md for more examples and how question
    text gets classified into a playbook."""
    if settings.manual_trigger_token and x_rca_trigger_token != settings.manual_trigger_token:
        raise HTTPException(status_code=403, detail="missing or invalid X-RCA-Trigger-Token")
    _enforce_rate_limit(request)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    timeout_seconds = min(req.timeout_seconds or settings.ask_max_investigation_seconds, settings.max_investigation_seconds)
    return handle_ask(req.question, timeout_seconds=timeout_seconds)
