"""Deterministic confidence scoring (#27). The LLM also states its own
confidence in the structured RCA it returns (prompts/rca_system_prompt.md);
the final confidence reported to Slack/the incident record is the more
conservative of the two, so a confident-sounding LLM narrative can't
override what the evidence itself actually supports.
"""
from app.investigation.hypotheses import Hypothesis

LEVELS = ("Unknown", "Low", "Medium", "High")


def score_to_level(top: Hypothesis | None, unavailable_sources: list[str]) -> str:
    if top is None or (not top.supporting and not top.contradicting):
        return "Unknown"

    independent_supporting_sources = len({_source_of(s) for s in top.supporting})
    has_contradictions = len(top.contradicting) > 0
    degraded = len(unavailable_sources) >= 2

    if independent_supporting_sources >= 3 and not has_contradictions and not degraded:
        return "High"
    if independent_supporting_sources >= 2 and not degraded:
        return "Medium"
    if top.supporting:
        return "Low"
    return "Unknown"


def _source_of(evidence_description: str) -> str:
    # Evidence descriptions are always prefixed "<source>: ..." by the
    # playbooks (see app/playbooks/base.py's EvidenceBundle usage) —
    # cheap enough that a real per-source list isn't worth threading
    # through the Hypothesis dataclass just for this.
    return evidence_description.split(":", 1)[0].strip().lower()


def most_conservative(deterministic_level: str, llm_level: str | None) -> str:
    if not llm_level or llm_level not in LEVELS:
        return deterministic_level
    return min(deterministic_level, llm_level, key=LEVELS.index)
