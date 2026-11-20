"""Prometheus metrics exposed by the monitor (the data behind the Grafana dashboard)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

STATES = ("healthy", "suspect", "failed", "recovering", "escalated")


class Metrics:
    """Uses a private registry so tests (and multiple instances) never collide."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self._up = Gauge("cloudrescue_service_up", "1 if the last health check passed", ["target"], registry=r)
        self._checks = Counter(
            "cloudrescue_health_checks", "Health checks performed", ["target", "result"], registry=r
        )
        self._latency = Histogram(
            "cloudrescue_health_check_latency_seconds",
            "Health check latency",
            ["target"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
            registry=r,
        )
        self._state = Gauge("cloudrescue_state", "One-hot current state", ["target", "state"], registry=r)
        self._state_code = Gauge(
            "cloudrescue_state_code",
            "0 healthy, 1 suspect, 2 failed, 3 recovering, 4 escalated",
            ["target"],
            registry=r,
        )
        self._attempts = Counter(
            "cloudrescue_recovery_attempts", "Recovery attempts by result", ["target", "result"], registry=r
        )
        self._incidents = Counter(
            "cloudrescue_incidents", "Closed incidents by outcome", ["target", "outcome"], registry=r
        )
        self._open = Gauge("cloudrescue_open_incidents", "Incidents currently open", ["target"], registry=r)
        self._mttr = Histogram(
            "cloudrescue_recovery_duration_seconds",
            "Seconds from first failed check to confirmed recovery",
            ["target"],
            buckets=(5, 10, 15, 20, 30, 45, 60, 90, 120, 300, 600),
            registry=r,
        )
        self._escalations = Counter(
            "cloudrescue_escalations", "Escalations", ["target", "action", "performed"], registry=r
        )

    def init_target(self, target: str) -> None:
        self.set_state(target, "healthy", 0)
        self._open.labels(target=target).set(0)
        self._up.labels(target=target).set(1)

    def observe_check(self, target: str, ok: bool, latency_s: float) -> None:
        self._up.labels(target=target).set(1 if ok else 0)
        self._checks.labels(target=target, result="ok" if ok else "fail").inc()
        self._latency.labels(target=target).observe(latency_s)

    def set_state(self, target: str, state: str, code: int) -> None:
        for s in STATES:
            self._state.labels(target=target, state=s).set(1 if s == state else 0)
        self._state_code.labels(target=target).set(code)

    def incident_opened(self, target: str) -> None:
        self._open.labels(target=target).set(1)

    def recovery_attempt(self, target: str, result: str) -> None:
        self._attempts.labels(target=target, result=result).inc()

    def incident_closed(self, target: str, outcome: str, mttr_s: float) -> None:
        self._incidents.labels(target=target, outcome=outcome).inc()
        self._open.labels(target=target).set(0)
        if outcome == "recovered":
            self._mttr.labels(target=target).observe(mttr_s)

    def escalation(self, target: str, action: str, performed: bool) -> None:
        self._escalations.labels(target=target, action=action, performed=str(performed).lower()).inc()
