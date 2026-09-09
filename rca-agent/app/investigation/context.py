"""Per-investigation context: the alert, its time windows (#12), the
read-only clients, and the tool-call budget (#49) every playbook shares.
One instance is built per incident by the orchestrator and threaded through
every playbook/evidence-gathering call — nothing here is global state.
"""
import logging
import time
from dataclasses import dataclass, field

from app.config import settings
from app.docker.readonly_client import DockerReadOnlyClient
from app.github.client import GitHubClient
from app.loki.client import LokiClient
from app.mysql.readonly_client import MySQLReadOnlyClient
from app.prometheus.client import DatasourceUnavailableError as PromUnavailable
from app.prometheus.client import PrometheusClient
from app.state.store import Incident
from app.tempo.client import TempoClient

logger = logging.getLogger(__name__)


class ToolCallBudgetExceeded(Exception):
    pass


@dataclass
class TimeWindow:
    start_s: int  # unix seconds
    end_s: int

    @property
    def start_ns(self) -> int:
        return self.start_s * 1_000_000_000

    @property
    def end_ns(self) -> int:
        return self.end_s * 1_000_000_000

    @property
    def start_rfc3339(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.start_s))

    @property
    def end_rfc3339(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.end_s))


@dataclass
class InvestigationContext:
    incident: Incident
    alert_name: str
    labels: dict
    annotations: dict
    t_alert_s: int  # alert start, unix seconds

    prometheus: PrometheusClient = field(default_factory=PrometheusClient)
    loki: LokiClient = field(default_factory=LokiClient)
    tempo: TempoClient = field(default_factory=TempoClient)
    mysql: MySQLReadOnlyClient = field(default_factory=MySQLReadOnlyClient)
    docker: DockerReadOnlyClient = field(default_factory=DockerReadOnlyClient)
    github: GitHubClient = field(default_factory=GitHubClient)

    _tool_calls: int = 0

    def tool_call(self, tag: str):
        """Every playbook wraps a client call with `with ctx.tool_call("..."):`
        — enforces MAX_TOOL_CALLS_PER_INVESTIGATION (#49) in one place
        instead of every playbook re-implementing the counter."""
        self._tool_calls += 1
        if self._tool_calls > settings.max_tool_calls:
            raise ToolCallBudgetExceeded(
                f"investigation exceeded {settings.max_tool_calls} tool calls (at {tag!r})"
            )
        return _ToolCallGuard()

    @property
    def now_s(self) -> int:
        return int(time.time())

    def baseline_window(self) -> TimeWindow:
        return TimeWindow(self.t_alert_s - 30 * 60, self.t_alert_s - 10 * 60)

    def immediate_window(self) -> TimeWindow:
        return TimeWindow(self.t_alert_s - 10 * 60, self.now_s)

    def extended_window(self) -> TimeWindow:
        return TimeWindow(self.t_alert_s - 60 * 60, self.now_s)


class _ToolCallGuard:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


__all__ = ["InvestigationContext", "TimeWindow", "ToolCallBudgetExceeded", "PromUnavailable"]
