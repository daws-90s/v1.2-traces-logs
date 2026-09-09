import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("RCA_STATE_DB_PATH", path)
    # app.config.settings is built once at import time; reload the module
    # under test so it picks up the isolated path.
    import app.config as config_module

    monkeypatch.setattr(config_module, "settings", config_module.Settings())
    import app.state.store as store_module

    monkeypatch.setattr(store_module, "settings", config_module.settings)
    store_module.init_db()
    yield
    os.remove(path)


def test_second_firing_for_same_fingerprint_reuses_incident():
    from app.state import store

    incident = store.create("fp-123", "MySQLDown", {"severity": "critical"}, "2026-09-09T08:32:00Z")
    again = store.get_active_by_fingerprint("fp-123")
    assert again is not None
    assert again.incident_id == incident.incident_id


def test_resolved_incident_is_not_reused():
    from app.state import store

    incident = store.create("fp-456", "MySQLDown", {}, "2026-09-09T08:32:00Z")
    incident.status = "RESOLVED"
    store.save(incident)

    assert store.get_active_by_fingerprint("fp-456") is None
