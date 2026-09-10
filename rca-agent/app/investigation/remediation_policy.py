"""Deterministic (never LLM-driven — see app/remediation/client.py's own
docstring) decision on whether a completed investigation qualifies for
auto-remediation, plus the in-process cooldown that stops it from
restarting the same container on every re-delivery of the same firing
alert.

The (playbook, hypothesis-keyword) -> container map below is intentionally
tiny and hardcoded, not data-driven and not extendable by request input or
LLM output. Restarting "backend" only makes sense when the deterministic
top-ranked hypothesis is specifically backend resource exhaustion or an
unexplained application-level error — not MySQLDown (restarting backend
wouldn't fix MySQL, and the backend's own ALLOWED_CONTAINERS wouldn't let
"mysql" through anyway — see agentRemediation.js) and not a plain traffic
spike (a restart there would just cause a pointless outage).

Restarting a process is a mitigation, not a fix — this buys time/clears a
stuck state while the RCA above documents the actual root cause; it is
deliberately not offered for hypotheses that describe a code bug or a
downstream dependency issue a restart cannot address.
"""
import logging
import time

from app.config import settings
from app.investigation.hypotheses import Hypothesis
from app.rca.engine import RCAResult

logger = logging.getLogger(__name__)

_LEVELS = ("Unknown", "Low", "Medium", "High")

# alertname -> (container to restart, hypothesis-name keywords the
# top-ranked hypothesis must contain for remediation to even be
# considered)
_REMEDIABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "HighAPIp99Latency": ("backend", ("resource exhaustion",)),
    "BackendLatencyP95High": ("backend", ("resource exhaustion",)),
    "HTTP500High": ("backend", ("application-level error", "unhandled exception")),
    "Backend5xxRateHigh": ("backend", ("application-level error", "unhandled exception")),
}

_last_restart: dict[str, float] = {}


def decide(alert_name: str, result: RCAResult, top_hypothesis: Hypothesis | None) -> str | None:
    """Returns the container to restart, or None if this investigation
    doesn't qualify. Never raises — a bug here must degrade to "don't
    remediate," never crash or block the investigation."""
    if not settings.auto_remediation_enabled:
        return None
    try:
        entry = _REMEDIABLE.get(alert_name)
        if not entry or top_hypothesis is None:
            return None
        container, keywords = entry
        if not any(k in top_hypothesis.name.lower() for k in keywords):
            return None
        if _LEVELS.index(result.confidence) < _LEVELS.index(settings.auto_remediation_min_confidence):
            return None
        if top_hypothesis.contradicting:
            # Belt-and-suspenders: score_to_level already can't reach High
            # confidence alongside any contradicting evidence, but this
            # check stays explicit rather than relying solely on that.
            return None
        last = _last_restart.get(container, 0.0)
        if time.monotonic() - last < settings.auto_remediation_cooldown_seconds:
            logger.info("auto-remediation for %r skipped — still within the cooldown window", container)
            return None
        return container
    except Exception:  # noqa: BLE001 — must never block/crash the investigation
        logger.exception("remediation policy check failed for alert %r", alert_name)
        return None


def record_restart(container: str) -> None:
    _last_restart[container] = time.monotonic()
