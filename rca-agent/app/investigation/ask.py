"""Free-text entry point (POST /ask, app/main.py) — not in the spec, added
so a human can ask directly ("investigate a recent high latency request
and tell me the reason") instead of only reacting to a real Alertmanager
alert.

Intent classification here is a fixed keyword match into an existing
playbook name, not an LLM call: routing to a known, allow-listed playbook
is a classification decision, not the "reasoning over evidence" the LLM is
used for elsewhere (#54). Keeping it deterministic also means a free-text
question can't talk an LLM into an unexpected code path — though there is
no tool for it to reach anyway (#70), so this is about correctness, not
just safety: a keyword match is instant and free, and this question is
typed by the person operating the agent, not adversarial data pulled from
logs/traces.

The rest of the pipeline (evidence collection, hypothesis scoring, LLM
narrative, Slack posting) is identical to the real alert-driven path —
this module only builds a synthetic AlertmanagerAlert and calls straight
into app.investigation.orchestrator.
"""
import logging
import time
import uuid
from datetime import datetime, timezone

from app.alertmanager.models import AlertmanagerAlert
from app.investigation.orchestrator import create_incident_and_notify, run_investigation
from app.prometheus import queries as prom_q
from app.prometheus.client import DatasourceUnavailableError, PrometheusClient
from app.prometheus.models import parse_instant_result
from app.security.sanitization import sanitize_text

logger = logging.getLogger(__name__)

# Order matters: checked top to bottom, first match wins. A question
# mentioning both "mysql" and "slow" (e.g. "is mysql making things slow?")
# intentionally routes to MySQLDown first — the more specific playbook —
# rather than HighAPIp99Latency.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("MySQLDown", ("mysql", "database down", "db down", "db is down", "db unreachable")),
    ("HTTP500High", ("500", "error rate", "errors", "failing", "failures", "exception", "crash")),
    ("HighAPIp99Latency", ("latency", "slow", "p99", "p95", "performance", "response time", "lag", "timeout", "timing out")),
]


def classify_question(question: str) -> tuple[str, dict]:
    q = question.lower()
    for alert_name, keywords in _KEYWORDS:
        if any(k in q for k in keywords):
            labels = {}
            if alert_name == "HighAPIp99Latency":
                route = _detect_worst_latency_route()
                if route:
                    labels["route"] = route
            return alert_name, labels
    return "GeneralInvestigation", {}


def _detect_worst_latency_route() -> str | None:
    """"A recent high latency request" doesn't name a route — find the
    worst-performing one right now (p99 over the last 5m, grouped by
    route) so the playbook has something concrete to investigate instead
    of averaging across every endpoint."""
    try:
        samples = parse_instant_result(PrometheusClient().instant_query(prom_q.http_latency_quantile(0.99)))
    except DatasourceUnavailableError as exc:
        logger.warning("could not auto-detect worst-latency route: %s", exc)
        return None
    if not samples:
        return None
    worst = max(samples, key=lambda s: s.value)
    return worst.labels.get("route")


def _sources_for(alert_name: str) -> list[str]:
    sources = ["Prometheus metrics", "Loki logs", "Container health"]
    if alert_name in ("HighAPIp99Latency", "HTTP500High", "GeneralInvestigation"):
        sources.insert(1, "Tempo traces")
    if alert_name in ("MySQLDown", "HighAPIp99Latency"):
        sources.append("MySQL diagnostics")
    return sources


def handle_ask(question: str, timeout_seconds: int) -> dict:
    question = question.strip()
    alert_name, extra_labels = classify_question(question)
    logger.info("ask: classified %r as alert_name=%s labels=%s", question[:100], alert_name, extra_labels)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fingerprint = f"ask-{uuid.uuid4()}"
    alert = AlertmanagerAlert(
        status="firing",
        labels={"alertname": alert_name, "severity": "info", "source": "ask", **extra_labels},
        annotations={"question": sanitize_text(question)[:500]},
        startsAt=now,
        endsAt=None,
        generatorURL=None,
        fingerprint=fingerprint,
    )

    sources = [f'Ad-hoc question: "{sanitize_text(question)[:200]}"'] + _sources_for(alert_name)
    incident = create_incident_and_notify(alert, sources=sources)

    start = time.monotonic()
    result = run_investigation(incident.incident_id, alert, timeout_seconds=timeout_seconds)
    elapsed = round(time.monotonic() - start, 1)

    if result is None:
        return {
            "incident_id": incident.incident_id,
            "playbook": alert_name,
            "confidence": "Unknown",
            "answer": (
                f"The investigation did not complete within {timeout_seconds}s. "
                "Check Slack (if configured) or `docker compose logs -f rca-agent` for partial findings, "
                "or retry with a longer timeout_seconds."
            ),
            "elapsed_seconds": elapsed,
        }

    return {
        "incident_id": incident.incident_id,
        "playbook": alert_name,
        "confidence": result.confidence,
        "root_cause": result.root_cause,
        "answer": result.executive_summary or result.slack_summary or "No summary produced.",
        "impact": result.impact,
        "evidence": result.evidence[:8],
        "recommended_remediation": result.recommended_remediation,
        "dashboard_url": result.dashboard_url,
        "elapsed_seconds": elapsed,
        "note": "Full detailed RCA also posted to Slack." if incident.slack_thread_ts else "Slack not configured — this response is the only copy of the RCA.",
    }
