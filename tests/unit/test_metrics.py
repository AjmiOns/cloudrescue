from metrics import Metrics


def test_state_is_one_hot_and_counters_move():
    m = Metrics()
    m.init_target("svc")
    m.set_state("svc", "failed", 2)
    get = m.registry.get_sample_value
    assert get("cloudrescue_state", {"target": "svc", "state": "failed"}) == 1
    assert get("cloudrescue_state", {"target": "svc", "state": "healthy"}) == 0
    assert get("cloudrescue_state_code", {"target": "svc"}) == 2

    m.observe_check("svc", False, 0.2)
    assert get("cloudrescue_service_up", {"target": "svc"}) == 0
    assert get("cloudrescue_health_checks_total", {"target": "svc", "result": "fail"}) == 1

    m.incident_opened("svc")
    m.incident_closed("svc", "recovered", 12.0)
    assert get("cloudrescue_open_incidents", {"target": "svc"}) == 0
    assert get("cloudrescue_recovery_duration_seconds_count", {"target": "svc"}) == 1
