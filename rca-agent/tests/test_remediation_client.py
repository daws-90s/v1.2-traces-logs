import pytest

import app.remediation.client as client


def test_restart_container_refuses_when_disabled(monkeypatch):
    monkeypatch.setattr(client.settings, "auto_remediation_enabled", False)
    with pytest.raises(client.RemediationError):
        client.restart_container("backend")


def test_restart_container_wraps_http_errors(monkeypatch):
    monkeypatch.setattr(client.settings, "auto_remediation_enabled", True)
    monkeypatch.setattr(client.settings, "backend_url", "http://backend:4000")

    def _boom(*args, **kwargs):
        import httpx

        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client.httpx, "post", _boom)
    with pytest.raises(client.RemediationError):
        client.restart_container("backend")


def test_restart_container_posts_fixed_body_to_fixed_url(monkeypatch):
    """The POST body/URL are never assembled from caller input — always
    exactly {"container": <name>} to <backend_url>/agent-remediation/restart."""
    monkeypatch.setattr(client.settings, "auto_remediation_enabled", True)
    monkeypatch.setattr(client.settings, "backend_url", "http://backend:4000")

    calls = []

    class _Resp:
        def raise_for_status(self):
            pass

    def _fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _Resp()

    monkeypatch.setattr(client.httpx, "post", _fake_post)
    client.restart_container("backend")

    assert calls == [("http://backend:4000/agent-remediation/restart", {"container": "backend"})]
