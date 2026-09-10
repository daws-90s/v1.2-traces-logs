"""The RCA agent's one write action beyond Slack: POST to the backend's
own allow-listed /agent-remediation/restart endpoint
(expense-backend-v1.2/src/routes/agentRemediation.js), gated behind
ENABLE_AGENT_REMEDIATION there and RCA_AUTO_REMEDIATION_ENABLED here —
both must be on for a restart to actually happen.

This client never touches the Docker socket itself: app/docker/
readonly_client.py's mount stays read-only, unchanged. The backend owns
the socket access needed to actually restart a container, and enforces
its own hardcoded ALLOWED_CONTAINERS allow-list server-side — independent
of, and not overridable by, anything this agent sends.

Also independent of the LLM: this client is not a tool the LLM is ever
given (#70 still holds — see app/llm/client.py). Whether to call this at
all is decided entirely in app.investigation.remediation_policy from the
already-computed, deterministic RCAResult; this module only performs the
HTTP call once that decision has already been made in code.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class RemediationError(Exception):
    pass


def restart_container(container: str) -> None:
    if not settings.auto_remediation_enabled:
        raise RemediationError("RCA_AUTO_REMEDIATION_ENABLED is not set")
    url = f"{settings.backend_url.rstrip('/')}/agent-remediation/restart"
    try:
        resp = httpx.post(url, json={"container": container}, timeout=settings.query_timeout_seconds)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RemediationError(str(exc)) from exc
    logger.warning("auto-remediation: restarted container %r via %s", container, url)
