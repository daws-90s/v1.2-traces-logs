"""Hypothesis model (#25). Deterministic code creates and scores
hypotheses from evidence the playbooks gathered; the LLM (app/llm/client.py)
ranks and explains them in prose — it does not invent new evidence."""
from dataclasses import dataclass, field


@dataclass
class Hypothesis:
    name: str
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    score: float = 0.0

    def add_supporting(self, description: str, weight: float = 1.0):
        self.supporting.append(description)
        self.score += weight

    def add_contradicting(self, description: str, weight: float = 1.0):
        self.contradicting.append(description)
        self.score -= weight

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "supporting": self.supporting,
            "contradicting": self.contradicting,
            "score": round(self.score, 2),
        }


def rank(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(hypotheses, key=lambda h: h.score, reverse=True)
