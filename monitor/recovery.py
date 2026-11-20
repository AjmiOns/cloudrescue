"""Failure detection, controlled recovery and verification (the heart of CloudRescue).

State machine per target
------------------------
    HEALTHY --fail--> SUSPECT --(threshold consecutive fails)--> FAILED
    FAILED --restart allowed--> RECOVERING --healthy within verify window--> HEALTHY (resolved)
                                           \\--still unhealthy--> FAILED (retry after cooldown)
    FAILED --restart budget exhausted--> ESCALATED (alert / optional EC2 reboot, once per incident)
    FAILED / ESCALATED --healthy again (fixed by someone else)--> HEALTHY (recovered_externally)

Safety properties
-----------------
* Hysteresis: nothing happens until `failure_threshold` consecutive failures.
* Bounded actions: at most `max_restarts` per sliding `window_s`, and `cooldown_s` between
  restarts. The budget is *not* reset by a success, so a flapping service (recover, fail,
  recover, ...) is still stopped and escalated instead of restarted forever.
* Verification: a restart only counts as success if the health check passes afterwards.
* The monitor can only restart through docker-guard, and only allowlisted containers.
* No exception can kill the loop (see `run`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from enum import Enum
from typing import Protocol

from config import RecoveryConfig, TargetConfig
from docker_client import GuardError
from escalation import Escalator
from health_check import CheckResult

log = logging.getLogger("cloudrescue.recovery")


class State(str, Enum):
    HEALTHY = "healthy"
    SUSPECT = "suspect"
    FAILED = "failed"
    RECOVERING = "recovering"
    ESCALATED = "escalated"


STATE_CODE = {
    State.HEALTHY: 0,
    State.SUSPECT: 1,
    State.FAILED: 2,
    State.RECOVERING: 3,
    State.ESCALATED: 4,
}


class Checker(Protocol):
    def check(self, target: TargetConfig) -> CheckResult: ...


class Runtime(Protocol):
    def restart(self, name: str, stop_timeout_s: int = ...) -> None: ...


class RestartBudget:
    """Sliding-window restart limiter with a minimum gap between restarts."""

    def __init__(self, max_restarts: int, window_s: float, cooldown_s: float) -> None:
        self._max = max_restarts
        self._window = window_s
        self._cooldown = cooldown_s
        self._times: deque[float] = deque()

    def check(self, now: float) -> tuple[bool, str]:
        while self._times and now - self._times[0] >= self._window:
            self._times.popleft()
        if len(self._times) >= self._max:
            return False, "budget_exhausted"
        if self._times and now - self._times[-1] < self._cooldown:
            return False, "cooldown"
        return True, "ok"

    def record(self, now: float) -> None:
        self._times.append(now)


class TargetMonitor:
    def __init__(
        self,
        target: TargetConfig,
        recovery: RecoveryConfig,
        *,
        checker: Checker,
        runtime: Runtime,
        incidents,
        metrics,
        escalator: Escalator,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], object] = time.sleep,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self._t = target
        self._r = recovery
        self._checker = checker
        self._runtime = runtime
        self._incidents = incidents
        self._metrics = metrics
        self._escalator = escalator
        self._clock = clock
        self._wall = wall
        self._sleep = sleep
        self._should_stop = should_stop
        self._budget = RestartBudget(recovery.max_restarts, recovery.window_s, recovery.cooldown_s)

        self.state = State.HEALTHY
        self._failures = 0
        self._first_failure_mono = 0.0
        self._first_failure_wall = 0.0
        self._incident_id: int | None = None
        self._attempts = 0
        self._escalated = False
        self._cooldown_noted = False

    # -- public -------------------------------------------------------------------------
    def tick(self) -> CheckResult:
        result = self._checker.check(self._t)
        self._metrics.observe_check(self._t.name, result.ok, result.latency_s)
        if result.ok:
            self._on_ok()
        else:
            self._on_failure(result)
        return result

    def run(self, stop: threading.Event) -> None:
        """Monitoring loop. Any unexpected error is logged and the loop carries on."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("unexpected_error_in_monitor_loop", extra={"target": self._t.name})
            stop.wait(self._t.interval_s)

    # -- transitions --------------------------------------------------------------------
    def _set_state(self, new: State) -> None:
        if new is self.state:
            return
        log.info("state_change", extra={"target": self._t.name, "from_state": self.state.value, "to_state": new.value})
        self.state = new
        self._metrics.set_state(self._t.name, new.value, STATE_CODE[new])

    def _on_ok(self) -> None:
        self._failures = 0
        if self._incident_id is not None:
            self._incidents.add_event(self._incident_id, "recovered_externally", "health restored without our action")
            self._close_incident("recovered_externally")
        self._set_state(State.HEALTHY)

    def _on_failure(self, result: CheckResult) -> None:
        now = self._clock()
        if self._failures == 0:
            self._first_failure_mono = now
            self._first_failure_wall = self._wall()
        self._failures += 1

        if self._failures < self._t.failure_threshold:
            self._set_state(State.SUSPECT)
            return

        if self._incident_id is None:
            self._incident_id = self._incidents.open_incident(
                self._t.name,
                f"{self._failures} consecutive failed health checks ({result.error})",
                detected_at=self._first_failure_wall,
            )
            self._metrics.incident_opened(self._t.name)
            self._attempts = 0
            self._escalated = False
            self._cooldown_noted = False
            self._set_state(State.FAILED)

        allowed, why = self._budget.check(now)
        if allowed:
            self._attempt_recovery()
        elif why == "cooldown":
            if not self._cooldown_noted:
                self._incidents.add_event(
                    self._incident_id, "cooldown", f"waiting {self._r.cooldown_s:g}s between restarts"
                )
                self._cooldown_noted = True
        else:
            self._escalate()

    def _attempt_recovery(self) -> None:
        assert self._incident_id is not None
        self._cooldown_noted = False
        self._attempts += 1
        self._budget.record(self._clock())
        self._set_state(State.RECOVERING)
        self._incidents.add_event(
            self._incident_id, "restart", f"attempt {self._attempts}: restarting container {self._t.container}"
        )
        try:
            self._runtime.restart(self._t.container, self._r.stop_timeout_s)
        except GuardError as exc:
            self._incidents.add_event(self._incident_id, "restart_failed", str(exc))
            self._metrics.recovery_attempt(self._t.name, "restart_error")
            self._set_state(State.FAILED)
            return

        if self._verify():
            self._incidents.add_event(self._incident_id, "verified", "health check passing after restart")
            self._metrics.recovery_attempt(self._t.name, "success")
            self._close_incident("recovered")
            self._set_state(State.HEALTHY)
        else:
            self._incidents.add_event(
                self._incident_id, "verify_failed", f"still unhealthy {self._r.verify_timeout_s:g}s after restart"
            )
            self._metrics.recovery_attempt(self._t.name, "verify_failed")
            self._set_state(State.FAILED)

    def _verify(self) -> bool:
        deadline = self._clock() + self._r.verify_timeout_s
        while True:
            self._sleep(self._r.verify_interval_s)
            if self._should_stop():
                return False
            result = self._checker.check(self._t)
            self._metrics.observe_check(self._t.name, result.ok, result.latency_s)
            if result.ok:
                return True
            if self._clock() >= deadline:
                return False

    def _escalate(self) -> None:
        assert self._incident_id is not None
        self._set_state(State.ESCALATED)
        if self._escalated:
            return
        self._escalated = True
        reason = (
            f"restart budget exhausted ({self._r.max_restarts} restarts in {self._r.window_s:g}s); "
            f"{self._t.name} is still failing"
        )
        self._incidents.add_event(self._incident_id, "escalation", reason)
        try:
            outcome = self._escalator.escalate(self._t.name, reason)
        except Exception as exc:  # noqa: BLE001 - escalation must never break the loop
            self._incidents.add_event(self._incident_id, "escalation_error", type(exc).__name__)
            self._metrics.escalation(self._t.name, "error", False)
            return
        self._incidents.add_event(
            self._incident_id, "escalation_result", f"{outcome.action} performed={outcome.performed}: {outcome.detail}"
        )
        self._metrics.escalation(self._t.name, outcome.action, outcome.performed)

    def _close_incident(self, outcome: str) -> None:
        assert self._incident_id is not None
        mttr = self._clock() - self._first_failure_mono
        self._incidents.close_incident(self._incident_id, outcome, mttr, self._attempts)
        self._metrics.incident_closed(self._t.name, outcome, mttr)
        self._incident_id = None
        self._failures = 0
        self._attempts = 0
        self._escalated = False
        self._cooldown_noted = False
