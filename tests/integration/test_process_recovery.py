"""Real monitor loop + real demo-service process + real HTTP, without Docker.

The only fake is the runtime: "restarting the container" is replaced by killing and
re-spawning the uvicorn process. Everything else (health checks, thresholds, state machine,
incident log, metrics) is the production code path.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from config import RecoveryConfig, TargetConfig
from escalation import AlertOnlyEscalator
from health_check import HealthChecker
from incident_logger import IncidentLogger
from metrics import Metrics
from recovery import TargetMonitor

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "integration-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProcessRuntime:
    """Stands in for docker-guard: restart == kill the process and start a fresh one."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.restarts = 0

    def start(self) -> None:
        env = {**os.environ, "CHAOS_TOKEN": TOKEN}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "demo_app:app", "--app-dir", str(ROOT / "app/demo-service"),
             "--port", str(self.port), "--log-level", "warning"],
            env=env,
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        if self.proc:
            self.proc.wait(timeout=10)

    def restart(self, name: str, stop_timeout_s: int = 10) -> None:
        self.restarts += 1
        self.stop()
        self.start()


def wait_for(predicate, timeout, interval=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.mark.parametrize("mode", ["unhealthy", "hang", "crash"])
def test_failure_modes_are_detected_and_repaired(tmp_path, mode):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    runtime = ProcessRuntime(port)
    runtime.start()
    stop = threading.Event()
    try:
        assert wait_for(lambda: _ok(f"{url}/health"), 20), "demo-service did not start"

        target = TargetConfig(name="demo", container="demo", url=f"{url}/health",
                              interval_s=0.3, timeout_s=0.5, failure_threshold=3)
        cfg = RecoveryConfig(max_restarts=3, window_s=600, cooldown_s=0, stop_timeout_s=1,
                             verify_timeout_s=20, verify_interval_s=0.5)
        incidents = IncidentLogger(str(tmp_path / "i.db"))
        metrics = Metrics()
        monitor = TargetMonitor(target, cfg, checker=HealthChecker(), runtime=runtime, incidents=incidents,
                                metrics=metrics, escalator=AlertOnlyEscalator(),
                                sleep=stop.wait, should_stop=stop.is_set)
        thread = threading.Thread(target=monitor.run, args=(stop,), daemon=True)
        thread.start()

        time.sleep(1)  # a few healthy checks first
        assert incidents.list_incidents() == []
        resp = requests.post(f"{url}/fail?mode={mode}", headers={"X-Chaos-Token": TOKEN}, timeout=2)
        assert resp.status_code == 202

        def recovered():
            rows = incidents.list_incidents()
            return bool(rows) and rows[0]["outcome"] == "recovered"

        assert wait_for(recovered, 40), f"not recovered from {mode}; incidents={incidents.list_incidents()}"
        assert runtime.restarts == 1
        incident = incidents.list_incidents()[0]
        assert incident["attempts"] == 1 and incident["mttr_s"] > 0
        assert _ok(f"{url}/health")
    finally:
        stop.set()
        runtime.stop()


def _ok(url: str) -> bool:
    try:
        return requests.get(url, timeout=1).status_code == 200
    except requests.RequestException:
        return False
