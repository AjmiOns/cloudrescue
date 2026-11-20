from escalation import AlertOnlyEscalator, Ec2RebootEscalator


class FakeEc2:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def reboot_instances(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def make(cooldown=100.0, error=None):
    now = [0.0]
    client = FakeEc2(error)
    esc = Ec2RebootEscalator(
        cooldown, clock=lambda: now[0], identity=lambda: ("i-123", "us-east-1"), client_factory=lambda r: client
    )
    return esc, client, now


def test_alert_only():
    r = AlertOnlyEscalator().escalate("svc", "why")
    assert r.action == "alert" and r.performed


def test_reboot_requested_then_cooldown_blocks_second():
    esc, client, now = make()
    assert esc.escalate("svc", "r").performed
    assert client.calls == [{"InstanceIds": ["i-123"]}]
    now[0] = 50
    second = esc.escalate("svc", "r")
    assert not second.performed and "cooldown" in second.detail
    now[0] = 150
    assert esc.escalate("svc", "r").performed
    assert len(client.calls) == 2


def test_failure_is_reported_not_raised():
    esc, _, _ = make(error=RuntimeError("boom"))
    r = esc.escalate("svc", "r")
    assert not r.performed and "RuntimeError" in r.detail


def test_preflight_classifies_dry_run():
    class Denied(Exception):
        response = {"Error": {"Code": "UnauthorizedOperation"}}

    class Allowed(Exception):
        response = {"Error": {"Code": "DryRunOperation"}}

    ok, _ = make(error=Allowed())[0].preflight()
    denied, detail = make(error=Denied())[0].preflight()
    assert ok and not denied and "UnauthorizedOperation" in detail
