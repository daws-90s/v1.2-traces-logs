"""The RCA agent's own Prometheus metrics (#42) — same self-monitoring
pattern every other piece of this stack already follows (see
prometheus/prometheus.yml's job list), extended to this agent's own
scrape job."""
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()

alerts_received_total = Counter(
    "rca_agent_alerts_received_total", "Alertmanager webhook deliveries received", ["status"], registry=registry
)
investigations_started_total = Counter(
    "rca_agent_investigations_started_total", "Investigations started", registry=registry
)
investigations_completed_total = Counter(
    "rca_agent_investigations_completed_total", "Investigations completed", ["confidence"], registry=registry
)
investigations_failed_total = Counter(
    "rca_agent_investigations_failed_total", "Investigations that raised an unhandled error", registry=registry
)
investigation_duration_seconds = Histogram(
    "rca_agent_investigation_duration_seconds", "Investigation wall-clock duration", registry=registry
)
tool_calls_total = Counter(
    "rca_agent_tool_calls_total", "Datasource calls made during investigations", ["source"], registry=registry
)
tool_errors_total = Counter(
    "rca_agent_tool_errors_total", "Datasource calls that failed/timed out", ["source"], registry=registry
)
rca_confidence = Gauge(
    "rca_agent_rca_confidence", "Most recent RCA confidence (0=Unknown,1=Low,2=Medium,3=High)", ["alert_name"], registry=registry
)
slack_messages_total = Counter(
    "rca_agent_slack_messages_total", "Slack messages sent", ["kind"], registry=registry
)

_CONFIDENCE_VALUE = {"Unknown": 0, "Low": 1, "Medium": 2, "High": 3}


def record_confidence(alert_name: str, confidence: str) -> None:
    rca_confidence.labels(alert_name=alert_name).set(_CONFIDENCE_VALUE.get(confidence, 0))


def render() -> bytes:
    return generate_latest(registry)
