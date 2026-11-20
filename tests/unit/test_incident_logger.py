import pytest

from incident_logger import IncidentLogger


@pytest.fixture
def db(tmp_path):
    logger = IncidentLogger(str(tmp_path / "i.db"), wall=iter(range(1000, 2000)).__next__)
    yield logger
    logger.close()


def test_lifecycle(db):
    iid = db.open_incident("svc", "3 failed checks")
    db.add_event(iid, "restart", "attempt 1")
    db.close_incident(iid, "recovered", mttr_s=12.5, attempts=1)
    (row,) = db.list_incidents()
    assert (row["target"], row["outcome"], row["attempts"], row["mttr_s"]) == ("svc", "recovered", 1, 12.5)
    assert [e["kind"] for e in db.get_events(iid)] == ["restart"]


def test_sql_injection_is_inert(db):
    nasty = "x'); DROP TABLE incidents;--"
    iid = db.open_incident(nasty, nasty)
    db.add_event(iid, "k", nasty)
    assert db.list_incidents(target=nasty)[0]["target"] == nasty
    assert len(db.list_incidents()) == 1


def test_close_stale_marks_open_incidents_abandoned(db):
    db.open_incident("a", "r")
    closed = db.open_incident("b", "r")
    db.close_incident(closed, "recovered", 1, 1)
    assert db.close_stale("restart") == 1
    outcomes = {r["target"]: r["outcome"] for r in db.list_incidents()}
    assert outcomes == {"a": "abandoned", "b": "recovered"}


def test_readonly_connection_cannot_write(tmp_path):
    path = str(tmp_path / "ro.db")
    IncidentLogger(path).close()
    ro = IncidentLogger(path, readonly=True)
    with pytest.raises(Exception, match="readonly"):
        ro.open_incident("x", "y")
