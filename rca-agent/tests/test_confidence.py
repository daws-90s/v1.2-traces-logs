from app.investigation.confidence import most_conservative, score_to_level
from app.investigation.hypotheses import Hypothesis


def test_no_evidence_is_unknown():
    h = Hypothesis("no evidence")
    assert score_to_level(h, []) == "Unknown"


def test_three_independent_sources_no_contradiction_is_high():
    h = Hypothesis("db saturation")
    h.add_supporting("prometheus: connections up")
    h.add_supporting("tempo: db spans dominate")
    h.add_supporting("loki: timeout errors")
    assert score_to_level(h, []) == "High"


def test_contradicting_evidence_prevents_high():
    h = Hypothesis("db saturation")
    h.add_supporting("prometheus: connections up")
    h.add_supporting("tempo: db spans dominate")
    h.add_supporting("loki: timeout errors")
    h.add_contradicting("prometheus: cpu normal")
    assert score_to_level(h, []) != "High"


def test_degraded_sources_prevent_high():
    h = Hypothesis("db saturation")
    h.add_supporting("prometheus: connections up")
    h.add_supporting("tempo: db spans dominate")
    h.add_supporting("loki: timeout errors")
    assert score_to_level(h, ["tempo: unavailable", "loki: unavailable"]) != "High"


def test_most_conservative_picks_lower():
    assert most_conservative("High", "Low") == "Low"
    assert most_conservative("Medium", "High") == "Medium"
    assert most_conservative("Low", None) == "Low"
    assert most_conservative("High", "not-a-level") == "High"
