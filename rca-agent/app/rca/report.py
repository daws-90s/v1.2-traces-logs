"""Detailed markdown RCA report — the section list from #57."""
from app.rca.engine import RCAResult


def format_detailed_report(
    result: RCAResult,
    severity: str,
    start_time: str,
    end_time: str | None,
    affected_services: list[str],
) -> str:
    lines = ["# Incident RCA", ""]
    lines += [f"## Alert\n{result.alert_name}", ""]
    lines += [f"## Severity\n{severity}", ""]
    lines += [f"## Incident Window\n{start_time} — {end_time or 'ongoing'}", ""]
    lines += [f"## Affected Services\n{', '.join(affected_services) or 'unknown'}", ""]
    lines += [f"## Executive Summary\n{result.executive_summary or 'Not available.'}", ""]
    lines += [f"## Impact\n{result.impact or 'Not determined.'}", ""]

    if result.dashboard_url:
        lines.append("## Relevant Dashboard")
        lines.append(f"[{result.dashboard_description or 'View in Grafana'}]({result.dashboard_url})")
        lines.append("")

    lines.append("## Timeline")
    if result.timeline:
        for t in result.timeline:
            lines.append(f"- {t['time']} — [{t['source']}] {t['event']}")
    else:
        lines.append("No timestamped evidence was collected.")
    lines.append("")

    for source, title in (
        ("prometheus", "Metrics Evidence"),
        ("loki", "Logs Evidence"),
        ("tempo", "Trace Evidence"),
        ("mysql", "Database Evidence"),
        ("docker", "Infrastructure Evidence"),
        ("github", "Application Code Evidence"),
    ):
        items = [e for e in result.evidence if e["source"] == source]
        lines.append(f"## {title}")
        if items:
            for e in items:
                lines.append(f"- {e['description']}")
        else:
            lines.append("No evidence collected from this source for this incident.")
        lines.append("")

    lines.append("## Root Cause")
    lines.append(result.root_cause or "Not established — evidence was insufficient to identify a specific root cause.")
    lines.append("")

    lines.append("## Contributing Factors")
    lines += [f"- {f}" for f in result.contributing_factors] or ["None identified."]
    lines.append("")

    lines.append("## Rejected Hypotheses")
    if result.rejected_hypotheses:
        for r in result.rejected_hypotheses:
            lines.append(f"- **{r.get('hypothesis')}** — rejected: {r.get('reason')}")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append("## Confidence")
    lines.append(result.confidence)
    lines.append("")

    lines.append("## Recommended Remediation")
    if result.recommended_remediation:
        lines += [f"- {r}" for r in result.recommended_remediation]
    else:
        lines.append("No specific remediation recommended.")
    lines.append("")
    lines.append("_Recommendations only — this agent does not execute them._")
    lines.append("")

    lines.append("## Observability Gaps")
    if result.observability_gaps:
        lines += [f"- {g}" for g in result.observability_gaps]
    else:
        lines.append("None identified.")
    lines.append("")

    lines.append("## Investigation Limitations")
    if result.unavailable_sources:
        lines += [f"- {u}" for u in result.unavailable_sources]
    else:
        lines.append("All configured datasources were reachable during this investigation.")
    if not result.llm_used:
        lines.append("- LLM reasoning step did not complete — this report reflects deterministic evidence only.")

    return "\n".join(lines)
