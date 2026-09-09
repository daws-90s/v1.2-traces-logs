"""Lightweight value objects for a Prometheus query result, used to compare
`current value vs historical baseline` (#13) without callers re-parsing the
raw /api/v1/query JSON shape everywhere."""
from dataclasses import dataclass


@dataclass
class MetricSample:
    labels: dict
    value: float
    timestamp: float


def parse_instant_result(result: list[dict]) -> list[MetricSample]:
    samples = []
    for series in result:
        ts, val = series.get("value", [None, None])
        if ts is None:
            continue
        try:
            samples.append(MetricSample(labels=series.get("metric", {}), value=float(val), timestamp=float(ts)))
        except (TypeError, ValueError):
            continue
    return samples


def single_value(result: list[dict]) -> float | None:
    samples = parse_instant_result(result)
    return samples[0].value if samples else None
