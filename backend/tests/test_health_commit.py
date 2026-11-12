"""Regression tests: /api/v1/admin/health reports the commit that is deployed.

The field exists so a deploy can be identified from outside the dashboard:
Render injects RENDER_GIT_COMMIT for Git-backed services, and the endpoint
reports null rather than guessing when the host gives it nothing.
"""
from __future__ import annotations

COMMIT = "2cbf23deb5af0ede15ca0c35ccd8a7b549f25fee"


def _health(client) -> dict:
    resp = client.get("/api/v1/admin/health")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_reports_render_git_commit(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", COMMIT)
    body = _health(client)
    assert body["commit"] == COMMIT
    assert body["status"] == "ok"          # the extra field does not change health


def test_health_commit_is_null_when_the_host_sets_nothing(client, monkeypatch):
    for name in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        monkeypatch.delenv(name, raising=False)
    body = _health(client)
    assert "commit" in body                # always present, so absence is detectable
    assert body["commit"] is None          # never inferred from the filesystem


def test_health_commit_trims_and_falls_back_to_git_commit(client, monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("GIT_COMMIT", f"  {COMMIT}\n")
    assert _health(client)["commit"] == COMMIT
