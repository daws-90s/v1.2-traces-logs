"""Pydantic models for the Alertmanager webhook payload this agent
receives at POST /webhook/alertmanager. Shape confirmed against
Alertmanager v0.27's own webhook_config schema (the version pinned in
docker-compose.yml), not guessed.
"""
from typing import Optional

from pydantic import BaseModel


class AlertmanagerAlert(BaseModel):
    status: str  # "firing" | "resolved"
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: str
    endsAt: Optional[str] = None
    generatorURL: Optional[str] = None
    fingerprint: str


class AlertmanagerWebhookPayload(BaseModel):
    version: Optional[str] = None
    groupKey: Optional[str] = None
    status: str  # group-level status
    receiver: Optional[str] = None
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: Optional[str] = None
    alerts: list[AlertmanagerAlert] = []
