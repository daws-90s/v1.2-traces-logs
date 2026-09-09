"""Maps an Alertmanager alertname to a playbook instance (#62's initial set:
MySQLDown, HighAPIp99Latency, HTTP500High — the third mapped from this
stack's real Backend5xxRateHigh rule). Anything else falls through to
DefaultPlaybook rather than being skipped."""
from app.playbooks.default import DefaultPlaybook
from app.playbooks.high_latency import HighLatencyPlaybook
from app.playbooks.http_500 import Http500Playbook
from app.playbooks.mysql_down import MySQLDownPlaybook

_REGISTRY = {
    "MySQLDown": MySQLDownPlaybook(),
    "HighAPIp99Latency": HighLatencyPlaybook(),
    "BackendLatencyP95High": HighLatencyPlaybook(),
    "HTTP500High": Http500Playbook(),
    "Backend5xxRateHigh": Http500Playbook(),
}

_DEFAULT = DefaultPlaybook()


def get_playbook(alert_name: str):
    return _REGISTRY.get(alert_name, _DEFAULT)
