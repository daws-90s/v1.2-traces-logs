"""The investigate() pipeline from #53, wired to real playbooks/clients.
Runs on a background thread per incident (webhook handlers must return to
Alertmanager quickly) with a hard wall-clock budget (#47) enforced by
racing the playbook against a nested executor future.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

from app import metrics
from app.alertmanager.models import AlertmanagerAlert
from app.config import settings
from app.investigation.context import InvestigationContext, ToolCallBudgetExceeded
from app.llm.client import LLMClient
from app.playbooks.registry import get_playbook
from app.rca.engine import generate_rca
from app.rca.report import format_detailed_report
from app.rca.summary import format_summary
from app.slack.client import SlackClient, SlackUnavailableError
from app.state import store

logger = logging.getLogger(__name__)

_dispatch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rca-investigation")
_deadline_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rca-deadline")

_slack = SlackClient()


def handle_webhook_alert(alert: AlertmanagerAlert) -> None:
    metrics.alerts_received_total.labels(status=alert.status).inc()
    if alert.status == "firing":
        _handle_firing(alert)
    elif alert.status == "resolved":
        _handle_resolved(alert)
    else:
        logger.warning("unknown alert status %r for fingerprint %s", alert.status, alert.fingerprint)


def _handle_firing(alert: AlertmanagerAlert) -> None:
    existing = store.get_active_by_fingerprint(alert.fingerprint)
    if existing is not None:
        # Same incident still firing (#30) — Alertmanager's own
        # group_interval (3h repeat_interval / 5m group_interval in
        # alertmanager.yml) will re-deliver this; do not start a second
        # investigation.
        logger.info("alert %s (fingerprint=%s) already has an active incident %s — skipping", alert.labels.get("alertname"), alert.fingerprint, existing.incident_id)
        return

    alert_name = alert.labels.get("alertname", "UnknownAlert")
    incident = store.create(alert.fingerprint, alert_name, alert.labels, alert.startsAt)
    incident.status = "INVESTIGATION_STARTED"

    severity = alert.labels.get("severity", "unknown")
    instance = alert.labels.get("instance") or alert.labels.get("job") or "unknown"
    sources = ["Prometheus metrics", "Loki logs", "Tempo traces", "MySQL diagnostics", "Container health", "Application source code"]
    try:
        thread_ts = _slack.send_investigation_started(alert_name, severity, instance, alert.startsAt, sources)
        incident.slack_thread_ts = thread_ts
        metrics.slack_messages_total.labels(kind="investigation_started").inc()
    except SlackUnavailableError as exc:
        logger.error("could not send investigation-started Slack message: %s", exc)
    store.save(incident)

    _dispatch_pool.submit(_run_investigation, incident.incident_id, alert)


def _handle_resolved(alert: AlertmanagerAlert) -> None:
    incident = store.get_active_by_fingerprint(alert.fingerprint)
    if incident is None:
        logger.info("resolved event for fingerprint %s with no active incident on file — nothing to update", alert.fingerprint)
        return

    incident.status = "RESOLVED"
    store.save(incident)

    if incident.slack_thread_ts:
        try:
            duration_str = _duration_str(alert.startsAt, alert.endsAt)
            _slack.send_resolution(
                incident.slack_thread_ts,
                incident.alert_name,
                alert.startsAt,
                alert.endsAt or _now_iso(),
                duration_str,
                incident.root_cause,
                incident.confidence or "Unknown",
            )
            metrics.slack_messages_total.labels(kind="resolution").inc()
        except SlackUnavailableError as exc:
            logger.error("could not send resolution Slack message: %s", exc)


def _run_investigation(incident_id: str, alert: AlertmanagerAlert) -> None:
    incident = store.get(incident_id)
    if incident is None:
        logger.error("incident %s vanished before investigation could start", incident_id)
        return

    start = time.monotonic()
    metrics.investigations_started_total.inc()
    incident.investigation_state = "DATA_COLLECTION"
    store.save(incident)

    t_alert_s = _parse_iso_to_epoch(alert.startsAt)
    ctx = InvestigationContext(
        incident=incident,
        alert_name=incident.alert_name,
        labels=alert.labels,
        annotations=alert.annotations,
        t_alert_s=t_alert_s,
    )
    playbook = get_playbook(incident.alert_name)

    try:
        future = _deadline_pool.submit(playbook.run, ctx)
        bundle, hypotheses = future.result(timeout=settings.max_investigation_seconds)
    except FutureTimeoutError:
        _report_timeout(incident, alert)
        metrics.investigations_failed_total.inc()
        return
    except ToolCallBudgetExceeded as exc:
        logger.error("incident %s: %s", incident_id, exc)
        _report_timeout(incident, alert, reason=str(exc))
        metrics.investigations_failed_total.inc()
        return
    except Exception:  # noqa: BLE001 — an unhandled playbook error must not crash the process
        logger.exception("incident %s: playbook %s raised", incident_id, playbook.name)
        metrics.investigations_failed_total.inc()
        incident.status = "INVESTIGATION_FAILED"
        store.save(incident)
        return

    incident.investigation_state = "HYPOTHESIS_VALIDATION"
    store.save(incident)

    result = generate_rca(ctx, bundle, hypotheses, llm=LLMClient())

    incident.status = "ROOT_CAUSE_IDENTIFIED" if result.root_cause else "INSUFFICIENT_EVIDENCE"
    incident.investigation_state = "RCA_GENERATED"
    incident.hypotheses = result.hypotheses
    incident.evidence = result.evidence
    incident.root_cause = result.root_cause
    incident.confidence = result.confidence
    store.save(incident)

    severity = alert.labels.get("severity", "unknown")
    service = alert.labels.get("job") or alert.labels.get("instance") or "expense-tracker"
    endpoint = alert.labels.get("route")
    duration_str = _duration_str(alert.startsAt, None)

    summary_text = format_summary(result, severity, duration_str, service, endpoint)
    report_text = format_detailed_report(
        result, severity, alert.startsAt, None, [service],
    )

    if incident.slack_thread_ts:
        try:
            _slack.send_rca_summary(incident.slack_thread_ts, summary_text)
            _slack.send_detailed_rca(incident.slack_thread_ts, report_text)
            metrics.slack_messages_total.labels(kind="rca").inc()
        except SlackUnavailableError as exc:
            logger.error("could not post RCA to Slack: %s", exc)
    else:
        logger.warning("Slack not configured — RCA for incident %s was computed but not posted:\n%s", incident_id, summary_text)

    incident.status = "SLACK_REPORTED" if incident.slack_thread_ts else incident.status
    incident.investigation_state = "WAITING_FOR_RESOLUTION"
    store.save(incident)

    metrics.investigations_completed_total.labels(confidence=result.confidence).inc()
    metrics.record_confidence(incident.alert_name, result.confidence)
    metrics.investigation_duration_seconds.observe(time.monotonic() - start)


def _report_timeout(incident, alert: AlertmanagerAlert, reason: str | None = None) -> None:
    incident.status = "INSUFFICIENT_EVIDENCE"
    incident.investigation_state = "TIMED_OUT"
    store.save(incident)
    if not incident.slack_thread_ts:
        return
    text = (
        "⚠️ RCA Investigation Incomplete\n\n"
        "The agent could not establish a high-confidence root cause within the investigation window "
        f"({settings.max_investigation_seconds}s).\n\n"
        f"{('Reason: ' + reason) if reason else ''}\n\n"
        "Additional investigation required."
    )
    try:
        _slack.send_rca_summary(incident.slack_thread_ts, text)
    except SlackUnavailableError as exc:
        logger.error("could not post timeout notice to Slack: %s", exc)


def _parse_iso_to_epoch(iso_str: str) -> int:
    normalized = iso_str.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        logger.warning("could not parse alert startsAt=%r, using current time", iso_str)
        return int(time.time())


def _duration_str(starts_at: str, ends_at: str | None) -> str:
    start_s = _parse_iso_to_epoch(starts_at)
    end_s = _parse_iso_to_epoch(ends_at) if ends_at else int(time.time())
    minutes = max(0, (end_s - start_s) // 60)
    return f"{minutes} min"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
