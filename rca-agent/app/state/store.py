"""SQLite-backed incident state (#44). One agent instance is the assumption
for this stage — matches the spec's "SQLite is acceptable if only one agent
instance is expected."

This is the only place alert deduplication logic lives (#30): a fingerprint
still firing maps to the same incident row instead of a new investigation
every polling cycle.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id         TEXT PRIMARY KEY,
    alert_fingerprint    TEXT NOT NULL,
    alert_name           TEXT NOT NULL,
    labels_json           TEXT NOT NULL,
    start_time            TEXT NOT NULL,
    last_update            TEXT NOT NULL,
    status                 TEXT NOT NULL,
    investigation_state    TEXT NOT NULL,
    hypotheses_json        TEXT,
    evidence_json           TEXT,
    root_cause              TEXT,
    confidence               TEXT,
    slack_thread_ts           TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(alert_fingerprint);
"""

_lock = threading.Lock()


@dataclass
class Incident:
    incident_id: str
    alert_fingerprint: str
    alert_name: str
    labels: dict
    start_time: str
    last_update: str
    status: str = "ALERT_RECEIVED"
    investigation_state: str = "ALERT_RECEIVED"
    hypotheses: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    root_cause: Optional[str] = None
    confidence: Optional[str] = None
    slack_thread_ts: Optional[str] = None


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.state_db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.state_db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript(_SCHEMA)


def _row_to_incident(row: sqlite3.Row) -> Incident:
    return Incident(
        incident_id=row["incident_id"],
        alert_fingerprint=row["alert_fingerprint"],
        alert_name=row["alert_name"],
        labels=json.loads(row["labels_json"]),
        start_time=row["start_time"],
        last_update=row["last_update"],
        status=row["status"],
        investigation_state=row["investigation_state"],
        hypotheses=json.loads(row["hypotheses_json"] or "[]"),
        evidence=json.loads(row["evidence_json"] or "[]"),
        root_cause=row["root_cause"],
        confidence=row["confidence"],
        slack_thread_ts=row["slack_thread_ts"],
    )


def get_active_by_fingerprint(fingerprint: str) -> Optional[Incident]:
    """An incident counts as "still the same investigation" while it hasn't
    reached RESOLVED — a fresh `firing` webhook for the same fingerprint
    after resolution starts a new incident on purpose."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE alert_fingerprint = ? AND status != 'RESOLVED' "
            "ORDER BY start_time DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return _row_to_incident(row) if row else None


def get(incident_id: str) -> Optional[Incident]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        return _row_to_incident(row) if row else None


def create(alert_fingerprint: str, alert_name: str, labels: dict, start_time: str) -> Incident:
    now = _now()
    incident = Incident(
        incident_id=str(uuid.uuid4()),
        alert_fingerprint=alert_fingerprint,
        alert_name=alert_name,
        labels=labels,
        start_time=start_time,
        last_update=now,
    )
    with _db() as conn:
        conn.execute(
            "INSERT INTO incidents (incident_id, alert_fingerprint, alert_name, labels_json, "
            "start_time, last_update, status, investigation_state, hypotheses_json, evidence_json, "
            "root_cause, confidence, slack_thread_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                incident.incident_id, incident.alert_fingerprint, incident.alert_name,
                json.dumps(labels), incident.start_time, incident.last_update,
                incident.status, incident.investigation_state, "[]", "[]", None, None, None,
            ),
        )
    return incident


def save(incident: Incident) -> None:
    incident.last_update = _now()
    with _db() as conn:
        conn.execute(
            "UPDATE incidents SET status=?, investigation_state=?, hypotheses_json=?, "
            "evidence_json=?, root_cause=?, confidence=?, slack_thread_ts=?, last_update=? "
            "WHERE incident_id=?",
            (
                incident.status, incident.investigation_state,
                json.dumps(incident.hypotheses), json.dumps(incident.evidence),
                incident.root_cause, incident.confidence, incident.slack_thread_ts,
                incident.last_update, incident.incident_id,
            ),
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
