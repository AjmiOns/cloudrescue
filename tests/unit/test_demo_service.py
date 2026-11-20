import os
import time

import pytest
from fastapi.testclient import TestClient

import demo_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(demo_app, "CHAOS_TOKEN", "secret-token")
    monkeypatch.setattr(demo_app, "HANG_SECONDS", 0.05)
    demo_app.chaos.set("ok")
    yield TestClient(demo_app.app)
    demo_app.chaos.set("ok")


H = {"X-Chaos-Token": "secret-token"}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_chaos_is_disabled_without_a_token(client, monkeypatch):
    monkeypatch.setattr(demo_app, "CHAOS_TOKEN", "")
    assert client.post("/fail?mode=unhealthy", headers=H).status_code == 404


def test_chaos_requires_the_right_token(client):
    assert client.post("/fail?mode=unhealthy").status_code == 403
    assert client.post("/fail?mode=unhealthy", headers={"X-Chaos-Token": "nope"}).status_code == 403
    assert client.get("/health").status_code == 200


def test_unhealthy_mode_returns_503_until_reset(client):
    assert client.post("/fail?mode=unhealthy", headers=H).status_code == 202
    assert client.get("/health").status_code == 503
    demo_app.chaos.set("ok")  # what a process restart does
    assert client.get("/health").status_code == 200


def test_hang_mode_stalls_then_fails(client):
    client.post("/fail?mode=hang", headers=H)
    started = time.monotonic()
    assert client.get("/health").status_code == 503
    assert time.monotonic() - started >= 0.05


def test_invalid_mode_is_rejected(client):
    assert client.post("/fail?mode=rm-rf", headers=H).status_code == 422


def test_crash_mode_exits_the_process(client, monkeypatch):
    exits = []
    monkeypatch.setattr(os, "_exit", exits.append)
    assert client.post("/fail?mode=crash", headers=H).status_code == 202
    time.sleep(0.5)
    assert exits == [1]


def test_metrics_exposed_and_docs_hidden(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert "demo_service_healthy" in body and "demo_service_requests_total" in body
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
