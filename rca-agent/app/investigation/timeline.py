"""Builds the incident timeline (#33 "Timeline" section) from timestamped
evidence — a plain sort, no inference. Entries without a timestamp
(aggregate/summary evidence) are excluded; they still appear in the RCA's
evidence sections, just not on the timeline."""
from app.investigation.evidence import EvidenceBundle


def build_timeline(bundle: EvidenceBundle) -> list[dict]:
    dated = [e for e in bundle.items if e.observed_at]
    dated.sort(key=lambda e: e.observed_at)
    return [{"time": e.observed_at, "source": e.source, "event": e.description} for e in dated]
