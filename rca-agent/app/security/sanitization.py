"""Redacts secrets/PII before evidence reaches the LLM or Slack (#37, #51).

Applied at the evidence-collection boundary (investigation/evidence.py),
so nothing downstream — the LLM prompt, the Slack message, the persisted
incident record — ever sees an unredacted value in the first place.
"""
import re

_REDACTED = "[REDACTED]"

_PATTERNS = [
    # Authorization headers / bearer tokens
    (re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"), f"authorization: Bearer {_REDACTED}"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"), f"Bearer {_REDACTED}"),
    # JWTs (three dot-separated base64url segments)
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), _REDACTED),
    # Cookie headers
    (re.compile(r"(?i)cookie\s*:\s*[^\r\n]+"), f"cookie: {_REDACTED}"),
    (re.compile(r"(?i)set-cookie\s*:\s*[^\r\n]+"), f"set-cookie: {_REDACTED}"),
    # key=value / "key": "value" secrets by field name
    (
        re.compile(
            r'(?i)("?(?:password|passwd|secret|token|api[_-]?key|jwt_secret|'
            r'db_password|mysql_password)"?\s*[:=]\s*)"?[^\s,"}\]]+'
        ),
        rf"\1{_REDACTED}",
    ),
    # Slack webhook / incoming-webhook URLs
    (re.compile(r"https://hooks\.slack\.com/services/\S+"), _REDACTED),
    # GitHub tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),
    # Generic connection strings with embedded credentials
    (re.compile(r"(?i)(mysql|postgres|redis)://[^:\s]+:[^@\s]+@"), rf"\1://{_REDACTED}@"),
    # Email addresses — kept only where a source explicitly needs one
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), _REDACTED),
]

# Prompt-injection guard (#50): evidence text is wrapped, never concatenated
# directly into the instruction portion of a prompt. This marker is what
# llm/client.py uses to visibly fence untrusted content in the prompt.
UNTRUSTED_DATA_OPEN = "<untrusted-observability-data>"
UNTRUSTED_DATA_CLOSE = "</untrusted-observability-data>"


def sanitize_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def sanitize_value(value):
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: sanitize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(v) for v in value]
    return value


def wrap_untrusted(text: str) -> str:
    """Fences evidence text as data, not instructions, per #50 — the LLM
    system prompt (prompts/rca_system_prompt.md) tells the model explicitly
    to never follow directives found between these markers."""
    return f"{UNTRUSTED_DATA_OPEN}\n{sanitize_text(text)}\n{UNTRUSTED_DATA_CLOSE}"
