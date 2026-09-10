"""Converts the plain-Markdown detailed RCA report (app/rca/report.py)
into Slack's own "mrkdwn" dialect before posting. Slack does not render
GitHub-flavored Markdown: `#`/`##` headings show up as literal hash
characters, `**bold**` doesn't bold (Slack uses single asterisks), and
`- ` bullets don't get a real bullet glyph.

report.py stays plain Markdown on purpose — it's the reusable, general
form of the report; this conversion is applied only at the point of
sending to Slack (app/slack/client.py), so a future non-Slack consumer of
format_detailed_report() still gets real Markdown.
"""
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def markdown_to_mrkdwn(text: str) -> str:
    out_lines = []
    for line in text.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading:
            out_lines.append(f"*{heading.group(2).strip()}*")
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            line = f"{bullet.group(1)}• {bullet.group(2)}"

        line = _BOLD_RE.sub(r"*\1*", line)
        line = _LINK_RE.sub(r"<\2|\1>", line)
        out_lines.append(line)
    return "\n".join(out_lines)
