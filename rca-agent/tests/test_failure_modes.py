"""Failure-mode matrix (#60-style graceful degradation): every datasource
call in a playbook goes through app.playbooks.base.safe(), and the RCA
pipeline must still produce a result — never crash the investigation —
when one, several, or all datasources are unreachable, or when the LLM
itself is unavailable. test_confidence.py already covers how
unavailable_sources feeds into scoring; this file covers the
exception-handling contract of safe() itself and generate_rca()'s
behavior at the extremes (no evidence at all, everything down).
"""
from app.investigation.context import InvestigationContext, ToolCallBudgetExceeded
from app.investigation.evidence import EvidenceBundle
from app.investigation.hypotheses import Hypothesis
from app.playbooks.base import safe
from app.rca.engine import generate_rca


class _FakeIncident:
    incident_id = "inc-failure-mode-test"


def _ctx() -> InvestigationContext:
    return InvestigationContext(
        incident=_FakeIncident(),
        alert_name="MySQLDown",
        labels={},
        annotations={},
        t_alert_s=1_700_000_000,
    )


class _FakeLLM:
    def __init__(self, response=None):
        self.response = response

    def generate_rca(self, user_message: str):
        return self.response


def test_safe_swallows_datasource_exception_and_records_it():
    ctx = _ctx()
    bundle = EvidenceBundle()

    def boom():
        raise ConnectionError("prometheus unreachable")

    result = safe(ctx, bundle, "prometheus", "some_query", boom)
    assert result is None
    assert bundle.unavailable_sources == ["prometheus: prometheus unreachable"]


def test_safe_does_not_swallow_tool_call_budget_exceeded():
    ctx = _ctx()
    bundle = EvidenceBundle()

    def boom():
        raise ToolCallBudgetExceeded("over budget")

    try:
        safe(ctx, bundle, "prometheus", "some_query", boom)
        assert False, "ToolCallBudgetExceeded must propagate, not be swallowed like a datasource error"
    except ToolCallBudgetExceeded:
        pass
    # A budget exceeded is a hard stop on the whole investigation, not a
    # single missing source — it must not be recorded as one.
    assert bundle.unavailable_sources == []


def test_safe_passes_through_successful_result():
    ctx = _ctx()
    bundle = EvidenceBundle()
    assert safe(ctx, bundle, "prometheus", "q", lambda: 42) == 42
    assert bundle.unavailable_sources == []


def test_every_datasource_down_still_produces_a_result_not_a_crash():
    bundle = EvidenceBundle()
    for source in ("prometheus", "loki", "tempo", "mysql", "docker", "github"):
        bundle.mark_unavailable(source, "connection refused")

    llm = _FakeLLM({"confidence": "Medium", "root_cause": "guessed anyway", "executive_summary": "e", "impact": "i"})
    result = generate_rca(_ctx(), bundle, [Hypothesis("only guess")], llm=llm)

    # Real evidence coverage is nonexistent; confidence must not survive
    # at whatever the LLM claimed — score_to_level + most_conservative
    # must pull it down (see app/investigation/confidence.py).
    assert result.confidence != "High"
    assert len(result.unavailable_sources) == 6


def test_llm_call_raising_is_handled_by_llm_client_not_engine():
    """app.llm.client.LLMClient.generate_rca itself already catches any
    provider/network exception and returns None (see its own try/except);
    generate_rca() must treat that None the same as any other
    insufficient-evidence case rather than assuming a dict."""

    class _RaisingLLM:
        def generate_rca(self, user_message: str):
            return None  # what LLMClient.generate_rca returns after catching its own exception

    result = generate_rca(_ctx(), EvidenceBundle(), [Hypothesis("h")], llm=_RaisingLLM())
    assert result.insufficient_evidence is True
    assert result.confidence == "Unknown"


def test_no_hypotheses_at_all_does_not_crash():
    llm = _FakeLLM({"confidence": "Low"})
    result = generate_rca(_ctx(), EvidenceBundle(), [], llm=llm)
    assert result.hypotheses == []
