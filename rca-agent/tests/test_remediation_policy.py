"""app/investigation/remediation_policy.py's decide(): the one place this
agent decides whether to take its one write action beyond Slack. Never
LLM-driven (see app/remediation/client.py's docstring) — these tests
exercise the deterministic gates directly: alert/hypothesis allow-list,
confidence threshold, contradicting evidence, and the cooldown.
"""
import app.investigation.remediation_policy as policy
from app.investigation.hypotheses import Hypothesis
from app.rca.engine import RCAResult


def _result(confidence: str) -> RCAResult:
    return RCAResult(
        alert_name="HighAPIp99Latency",
        incident_id="inc-1",
        confidence=confidence,
        root_cause="backend resource exhaustion",
        executive_summary="",
        impact="",
    )


def _setup(monkeypatch, enabled=True, min_confidence="High", cooldown=900):
    monkeypatch.setattr(policy.settings, "auto_remediation_enabled", enabled)
    monkeypatch.setattr(policy.settings, "auto_remediation_min_confidence", min_confidence)
    monkeypatch.setattr(policy.settings, "auto_remediation_cooldown_seconds", cooldown)
    policy._last_restart.clear()


def test_disabled_by_default_returns_none(monkeypatch):
    _setup(monkeypatch, enabled=False)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) is None


def test_matching_alert_and_hypothesis_at_high_confidence_qualifies(monkeypatch):
    _setup(monkeypatch)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) == "backend"


def test_unmapped_alert_never_qualifies(monkeypatch):
    _setup(monkeypatch)
    h = Hypothesis("MySQL container/process is actually down or unreachable")
    h.add_supporting("x")
    assert policy.decide("MySQLDown", _result("High"), h) is None


def test_wrong_hypothesis_for_a_mapped_alert_does_not_qualify(monkeypatch):
    _setup(monkeypatch)
    # Plain traffic spike, not resource exhaustion — restarting backend
    # would not help and must not happen.
    h = Hypothesis("Plain traffic spike, no backend degradation")
    h.add_supporting("x")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) is None


def test_below_min_confidence_does_not_qualify(monkeypatch):
    _setup(monkeypatch)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    assert policy.decide("HighAPIp99Latency", _result("Medium"), h) is None


def test_contradicting_evidence_blocks_even_at_high_confidence(monkeypatch):
    _setup(monkeypatch)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    h.add_contradicting("y")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) is None


def test_no_top_hypothesis_does_not_qualify(monkeypatch):
    _setup(monkeypatch)
    assert policy.decide("HighAPIp99Latency", _result("High"), None) is None


def test_cooldown_blocks_repeat_restart(monkeypatch):
    _setup(monkeypatch, cooldown=900)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) == "backend"
    policy.record_restart("backend")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) is None


def test_cooldown_expires(monkeypatch):
    _setup(monkeypatch, cooldown=900)
    h = Hypothesis("Backend container resource exhaustion (CPU/memory)")
    h.add_supporting("x")
    policy.record_restart("backend")
    policy._last_restart["backend"] -= 901
    assert policy.decide("HighAPIp99Latency", _result("High"), h) == "backend"


def test_lower_min_confidence_setting_allows_medium(monkeypatch):
    _setup(monkeypatch, min_confidence="Medium")
    h = Hypothesis("Application-level error (unhandled exception / validation) unrelated to MySQL")
    h.add_supporting("x")
    assert policy.decide("HTTP500High", _result("Medium"), h) == "backend"


def test_policy_check_never_raises_on_unexpected_input(monkeypatch):
    _setup(monkeypatch)
    # top_hypothesis.name being something unexpected (e.g. empty) must
    # degrade to "don't remediate," not throw.
    h = Hypothesis("")
    assert policy.decide("HighAPIp99Latency", _result("High"), h) is None
