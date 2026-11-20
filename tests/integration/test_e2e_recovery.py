"""End-to-end test against the real stack. Needs Docker; skipped unless CLOUDRESCUE_E2E=1.

    make init && CLOUDRESCUE_E2E=1 python -m pytest tests/integration -m e2e -s
"""

import json
import os
import subprocess
import time

import pytest
import requests

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("CLOUDRESCUE_E2E") != "1", reason="set CLOUDRESCUE_E2E=1 (needs docker)"),
]

BASE = os.environ.get("DEMO_URL", "http://127.0.0.1:8000")


def sh(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=180)


def healthy() -> bool:
    try:
        return requests.get(f"{BASE}/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def wait_until(predicate, timeout, interval=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope="module", autouse=True)
def stack():
    sh("docker", "compose", "up", "-d", "--build")
    assert wait_until(healthy, 120), "stack did not become healthy"
    yield


def incidents():
    out = sh("docker", "compose", "exec", "-T", "monitor", "python", "cli.py", "--json", "incidents").stdout
    return json.loads(out)


@pytest.mark.parametrize("mode", ["unhealthy", "hang", "stop"])
def test_service_is_healed_automatically(mode):
    before = len(incidents())
    sh("scripts/chaos.sh", mode)
    assert wait_until(lambda: not healthy(), 20, 1), "failure was not injected"
    assert wait_until(healthy, 120), f"service did not recover from '{mode}'"
    assert wait_until(lambda: len(incidents()) > before, 20)
    newest = incidents()[0]
    assert newest["outcome"] == "recovered" and newest["mttr_s"] > 0
