"""The Evidence model from #26 — every RCA claim traces back to one or more
of these. Sanitization (#51) is applied at construction time, here, so
nothing downstream (LLM prompt, Slack, persisted incident) ever handles an
unredacted value.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.security.sanitization import sanitize_text


@dataclass
class Evidence:
    source: str  # "prometheus" | "loki" | "tempo" | "mysql" | "docker" | "github" | "alertmanager"
    description: str
    observed_at: Optional[str] = None
    raw: Optional[dict] = None

    def __post_init__(self):
        self.description = sanitize_text(self.description)


@dataclass
class EvidenceBundle:
    items: list[Evidence] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)  # missing telemetry, per #58
    unavailable_sources: list[str] = field(default_factory=list)  # per #60

    def add(self, source: str, description: str, observed_at: Optional[str] = None, raw: Optional[dict] = None):
        self.items.append(Evidence(source=source, description=description, observed_at=observed_at, raw=raw))

    def add_gap(self, description: str):
        self.gaps.append(sanitize_text(description))

    def mark_unavailable(self, source: str, detail: str):
        self.unavailable_sources.append(f"{source}: {sanitize_text(detail)}")

    def by_source(self, source: str) -> list[Evidence]:
        return [e for e in self.items if e.source == source]

    def independent_source_count(self) -> int:
        return len({e.source for e in self.items})

    def to_dicts(self) -> list[dict]:
        return [
            {"source": e.source, "description": e.description, "observed_at": e.observed_at}
            for e in self.items
        ]
