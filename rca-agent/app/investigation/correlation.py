"""Correlation helpers (#28) — small, deterministic utilities the
playbooks use to tie evidence from different sources to the same request/
incident, rather than leaving that entirely to the LLM's judgment."""
import re

_TRACE_ID_RE = re.compile(r'"trace_id"\s*:\s*"([0-9a-f]{16,32})"')


def extract_trace_ids(log_lines: list[dict]) -> list[str]:
    """Pulls trace_id out of pino/instrumentation-pino JSON log lines —
    same field Loki's derivedFields regex uses for the View Trace button."""
    ids = set()
    for entry in log_lines:
        match = _TRACE_ID_RE.search(entry.get("line", ""))
        if match:
            ids.add(match.group(1))
    return list(ids)


def within_window(timestamp_iso: str, start_iso: str, end_iso: str) -> bool:
    return start_iso <= timestamp_iso <= end_iso


def percent_change(baseline: float | None, current: float | None) -> float | None:
    if baseline is None or current is None or baseline == 0:
        return None
    return ((current - baseline) / baseline) * 100
