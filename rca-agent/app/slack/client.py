"""The one write-capable client in this codebase (#6, #35) — Slack Web API
(bot token, chat:write scope), not the incoming-webhook URL
alertmanager/secrets/slack_webhook_url uses, because threading (#45) needs
chat.postMessage's thread_ts, which an incoming webhook has no equivalent
for.

Every public method here maps to exactly one of the four allowed Slack
writes in #6: investigation started, investigation update, RCA
summary/report, resolution. There is no generic "send arbitrary message"
escape hatch used anywhere else in this codebase.
"""
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"


class SlackUnavailableError(Exception):
    pass


class SlackClient:
    def __init__(self):
        self._headers = {"Authorization": f"Bearer {settings.slack_bot_token}"}
        self._last_update_sent: dict[str, float] = {}  # incident_id -> monotonic time, for throttling (#46)

    @property
    def configured(self) -> bool:
        return bool(settings.slack_bot_token and settings.slack_channel_id)

    def _post_message(self, text: str, thread_ts: str | None = None) -> dict:
        if not self.configured:
            raise SlackUnavailableError("SLACK_BOT_TOKEN/SLACK_CHANNEL_ID not configured")
        payload = {"channel": settings.slack_channel_id, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            resp = httpx.post(
                f"{_API_BASE}/chat.postMessage",
                headers=self._headers,
                json=payload,
                timeout=settings.query_timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise SlackUnavailableError(str(exc)) from exc
        if not data.get("ok"):
            raise SlackUnavailableError(data.get("error", "unknown Slack API error"))
        return data

    def send_investigation_started(self, alert_name: str, severity: str, instance: str, started_at: str, sources: list[str]) -> str:
        """The parent message every later update/RCA/resolution threads
        under (#45). Returns the thread_ts to persist on the incident."""
        source_lines = "\n".join(f"• {s}" for s in sources)
        text = (
            f"🚨 RCA Agent — Investigation Started\n\n"
            f"Alert: {alert_name}\n"
            f"Severity: {severity}\n"
            f"Instance: {instance}\n"
            f"Started: {started_at}\n\n"
            f"🔎 Investigation is in progress.\n\n"
            f"The RCA agent is analyzing:\n{source_lines}\n\n"
            f"A detailed RCA will be posted when the investigation completes."
        )
        data = self._post_message(text)
        return data["ts"]

    def send_update(self, incident_id: str, thread_ts: str, text: str, min_interval_seconds: int = 60) -> None:
        """Throttled per #46 — callers may propose an update after every
        playbook step, but this drops one silently if the last update for
        this incident was less than min_interval_seconds ago."""
        now = time.monotonic()
        last = self._last_update_sent.get(incident_id, 0)
        if now - last < min_interval_seconds:
            logger.debug("throttling Slack update for incident %s", incident_id)
            return
        self._last_update_sent[incident_id] = now
        self._post_message(f"🔎 RCA Agent Update\n\n{text}", thread_ts=thread_ts)

    def send_rca_summary(self, thread_ts: str, summary_text: str) -> None:
        self._post_message(summary_text, thread_ts=thread_ts)

    def send_detailed_rca(self, thread_ts: str, report_markdown: str) -> None:
        """Slack messages have a practical length limit; a long detailed
        report is split into a small number of threaded replies rather than
        truncated silently."""
        chunk_size = 3500
        chunks = [report_markdown[i : i + chunk_size] for i in range(0, len(report_markdown), chunk_size)] or [""]
        for i, chunk in enumerate(chunks):
            prefix = "📄 Detailed RCA Report\n\n" if i == 0 else ""
            self._post_message(prefix + chunk, thread_ts=thread_ts)

    def send_resolution(self, thread_ts: str, alert_name: str, started_at: str, resolved_at: str, duration_str: str, root_cause: str | None, confidence: str) -> None:
        text = (
            f"✅ Incident Resolved\n\n"
            f"Alert: {alert_name}\n\n"
            f"Started: {started_at}\n"
            f"Resolved: {resolved_at}\n"
            f"Duration: {duration_str}\n\n"
            f"RCA:\n{root_cause or 'Not established.'}\n\n"
            f"Confidence: {confidence}"
        )
        self._post_message(text, thread_ts=thread_ts)
