import pytest

from config import RecoveryConfig
from fakes import build, run_ticks
from recovery import RestartBudget, State


def test_healthy_service_does_nothing(tmp_path):
    monitor, _, runtime, incidents, *_rest, clock = build(tmp_path, healthy=True)
    run_ticks(monitor, clock, 10)
    assert monitor.state is State.HEALTHY
    assert runtime.restarts == []
    assert incidents.list_incidents() == []


def test_transient_blip_below_threshold_causes_no_action(tmp_path):
    monitor, service, runtime, incidents, *_rest, clock = build(tmp_path, healthy=True)
    service.healthy = False
    run_ticks(monitor, clock, 2)  # threshold is 3
    assert monitor.state is State.SUSPECT
    service.healthy = True
    run_ticks(monitor, clock, 1)
    assert monitor.state is State.HEALTHY
    assert runtime.restarts == []
    assert incidents.list_incidents() == []


def test_failure_is_detected_restarted_verified_and_logged(tmp_path):
    monitor, _, runtime, incidents, metrics, escalator, clock = build(tmp_path)
    run_ticks(monitor, clock, 3)  # third failure confirms the outage

    assert len(runtime.restarts) == 1
    assert monitor.state is State.HEALTHY
    (incident,) = incidents.list_incidents()
    assert incident["outcome"] == "recovered"
    assert incident["attempts"] == 1
    # first failure at t0, confirmed 10s later, verified 2s after the restart
    assert incident["mttr_s"] == pytest.approx(12.0)
    kinds = [e["kind"] for e in incidents.get_events(incident["id"])]
    assert kinds == ["restart", "verified"]
    assert escalator.calls == []
    assert metrics.registry.get_sample_value(
        "cloudrescue_recovery_attempts_total", {"target": "svc", "result": "success"}
    ) == 1


def test_restart_that_does_not_fix_the_service_is_retried_then_escalated_once(tmp_path):
    cfg = RecoveryConfig(max_restarts=2, window_s=600, cooldown_s=20, stop_timeout_s=1,
                         verify_timeout_s=10, verify_interval_s=2)
    monitor, _, runtime, incidents, _, escalator, clock = build(tmp_path, heals=False, recovery=cfg)
    run_ticks(monitor, clock, 60)

    assert len(runtime.restarts) == 2  # never more than the budget
    assert monitor.state is State.ESCALATED
    assert len(escalator.calls) == 1  # escalated exactly once per incident
    (incident,) = incidents.list_incidents()
    assert incident["outcome"] is None  # still open
    kinds = [e["kind"] for e in incidents.get_events(incident["id"])]
    assert "escalation" in kinds and "verify_failed" in kinds


def test_cooldown_spaces_restarts_apart(tmp_path):
    monitor, _, runtime, _, _, _, clock = build(tmp_path, heals=False)
    run_ticks(monitor, clock, 60)
    gaps = [b - a for a, b in zip(runtime.restarts, runtime.restarts[1:], strict=False)]
    assert gaps and all(g >= 60 for g in gaps)


def test_guard_error_does_not_crash_and_is_recorded(tmp_path):
    monitor, _, runtime, incidents, metrics, _, clock = build(tmp_path)
    runtime.fail_with = "docker-guard refused POST: HTTP 403"
    run_ticks(monitor, clock, 3)
    assert monitor.state is State.FAILED
    (incident,) = incidents.list_incidents()
    kinds = [e["kind"] for e in incidents.get_events(incident["id"])]
    assert "restart_failed" in kinds
    assert metrics.registry.get_sample_value(
        "cloudrescue_recovery_attempts_total", {"target": "svc", "result": "restart_error"}
    ) == 1


def test_service_fixed_by_someone_else_closes_the_incident(tmp_path):
    cfg = RecoveryConfig(max_restarts=1, window_s=600, cooldown_s=0, stop_timeout_s=1,
                         verify_timeout_s=4, verify_interval_s=2)
    monitor, service, _, incidents, _, _, clock = build(tmp_path, heals=False, recovery=cfg)
    run_ticks(monitor, clock, 10)
    assert monitor.state is State.ESCALATED
    service.healthy = True  # an operator fixed it manually
    run_ticks(monitor, clock, 1)
    assert monitor.state is State.HEALTHY
    (incident,) = incidents.list_incidents()
    assert incident["outcome"] == "recovered_externally"


def test_flapping_service_is_still_limited_by_the_budget(tmp_path):
    """Recover, fail, recover, fail ... must not restart forever: success does not reset the budget."""
    cfg = RecoveryConfig(max_restarts=2, window_s=600, cooldown_s=0, stop_timeout_s=1,
                         verify_timeout_s=4, verify_interval_s=2)
    monitor, service, runtime, _, _, escalator, clock = build(tmp_path, heals=True, recovery=cfg)
    for _ in range(4):
        service.healthy = False
        run_ticks(monitor, clock, 3)
    assert len(runtime.restarts) == 2
    assert len(escalator.calls) == 1


def test_budget_window_slides():
    b = RestartBudget(max_restarts=2, window_s=100, cooldown_s=0)
    assert b.check(0) == (True, "ok")
    b.record(0)
    b.record(10)
    assert b.check(20) == (False, "budget_exhausted")
    assert b.check(101) == (True, "ok")  # the first restart has left the window
