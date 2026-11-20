"""Test doubles shared by the unit tests."""

from __future__ import annotations

from config import RecoveryConfig, TargetConfig
from docker_client import GuardError
from escalation import EscalationResult
from health_check import CheckResult
from incident_logger import IncidentLogger
from metrics import Metrics
from recovery import TargetMonitor


class FakeClock:
    """Deterministic time: sleeping just moves the clock forward."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeService:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy


class ServiceChecker:
    def __init__(self, service: FakeService) -> None:
        self.service = service

    def check(self, target: TargetConfig) -> CheckResult:
        if self.service.healthy:
            return CheckResult(True, 200, 0.01)
        return CheckResult(False, 503, 0.01, "unexpected status 503")


class FakeRuntime:
    def __init__(self, service: FakeService, clock: FakeClock, *, heals: bool = True) -> None:
        self.service = service
        self.clock = clock
        self.heals = heals
        self.fail_with: str | None = None
        self.restarts: list[float] = []

    def restart(self, name: str, stop_timeout_s: int = 10) -> None:
        self.restarts.append(self.clock())
        if self.fail_with:
            raise GuardError(self.fail_with)
        if self.heals:
            self.service.healthy = True


class RecordingEscalator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def escalate(self, target: str, reason: str) -> EscalationResult:
        self.calls.append((target, reason))
        return EscalationResult("alert", True, "recorded")


TARGET = TargetConfig(name="svc", container="svc", url="http://svc/health", interval_s=5, failure_threshold=3)


def build(tmp_path, *, heals=True, recovery: RecoveryConfig | None = None, healthy=False):
    clock = FakeClock()
    service = FakeService(healthy=healthy)
    runtime = FakeRuntime(service, clock, heals=heals)
    incidents = IncidentLogger(str(tmp_path / "inc.db"), wall=clock)
    metrics = Metrics()
    escalator = RecordingEscalator()
    cfg = recovery or RecoveryConfig(
        max_restarts=3, window_s=600, cooldown_s=60, stop_timeout_s=1, verify_timeout_s=10, verify_interval_s=2
    )
    monitor = TargetMonitor(
        TARGET,
        cfg,
        checker=ServiceChecker(service),
        runtime=runtime,
        incidents=incidents,
        metrics=metrics,
        escalator=escalator,
        clock=clock,
        wall=clock,
        sleep=clock.sleep,
    )
    return monitor, service, runtime, incidents, metrics, escalator, clock


def run_ticks(monitor, clock, n: int, interval: float = 5.0) -> None:
    for _ in range(n):
        monitor.tick()
        clock.sleep(interval)
