"""Short Slack summary formatter — matches the shape in #32."""
from app.rca.engine import RCAResult


def format_summary(result: RCAResult, severity: str, duration_str: str, service: str, endpoint: str | None) -> str:
    header = "🚨 RCA Completed" if not result.insufficient_evidence else "⚠️ RCA Investigation Incomplete"
    lines = [f"{header} — {result.alert_name}", ""]
    lines.append(f"Severity: {severity.capitalize()}")
    lines.append(f"Duration: {duration_str}")
    lines.append(f"Affected service: {service}")
    if endpoint:
        lines.append(f"Affected endpoint: {endpoint}")
    lines.append("")

    if result.root_cause:
        lines.append("Root Cause:")
        lines.append(result.root_cause)
    else:
        lines.append("Root Cause: not established — see confidence and evidence below.")
    lines.append("")
    lines.append(f"Confidence: {result.confidence.upper()}")
    lines.append("")

    if result.evidence:
        lines.append("Evidence:")
        for e in result.evidence[:6]:
            lines.append(f"• {e['source'].capitalize()}: {e['description']}")
        lines.append("")

    if result.impact:
        lines.append("Impact:")
        lines.append(result.impact)
        lines.append("")

    if result.recommended_remediation:
        lines.append("Recommended action:")
        for r in result.recommended_remediation[:3]:
            lines.append(f"• {r}")
        lines.append("")

    lines.append(f"Investigation: {'Incomplete' if result.insufficient_evidence else 'Complete'}")
    return "\n".join(lines)
