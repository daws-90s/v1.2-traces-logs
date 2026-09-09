"""Playbook interface (#15-#17) and the `safe()` helper every playbook uses
to call a datasource: enforces the tool-call budget, catches that specific
datasource being down, and records it as reduced confidence (#60) rather
than crashing the whole investigation.
"""
import logging
from typing import Callable, TypeVar

from app.investigation.context import InvestigationContext, ToolCallBudgetExceeded
from app.investigation.evidence import EvidenceBundle

logger = logging.getLogger(__name__)

T = TypeVar("T")


def safe(ctx: InvestigationContext, bundle: EvidenceBundle, source: str, tag: str, fn: Callable[[], T]) -> T | None:
    with ctx.tool_call(tag):
        try:
            return fn()
        except ToolCallBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 — any datasource client's own error type
            logger.warning("datasource %s unavailable during %s: %s", source, tag, exc)
            bundle.mark_unavailable(source, str(exc))
            return None


class Playbook:
    name: str = "generic"

    def run(self, ctx: InvestigationContext) -> tuple[EvidenceBundle, list]:
        """Returns (evidence_bundle, hypotheses) — see app/playbooks/mysql_down.py
        etc. for the concrete shape."""
        raise NotImplementedError
