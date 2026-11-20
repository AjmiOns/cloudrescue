import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

from docker_client import DockerGuardClient, GuardError
from guard import make_handler, parse_allowlist

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INSPECT_BODY = {
    "Id": "abc",
    "Config": {"Env": ["SECRET=leak-me"]},  # must never be passed through
    "RestartCount": 2,
    "State": {"Status": "running", "Running": True, "StartedAt": "2026-01-01T00:00:00Z",
              "Health": {"Status": "healthy"}},
}


@pytest.fixture
def guard():
    calls = []

    def upstream(method, path):
        calls.append((method, path))
        if path.endswith("/json"):
            return 200, json.dumps(INSPECT_BODY).encode()
        return 204, b""

    handler = make_handler(frozenset({"demo-service"}), TOKEN, upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()
    server.server_close()


def test_ping_needs_no_auth(guard):
    url, _ = guard
    assert requests.get(f"{url}/_ping", timeout=2).status_code == 200


def test_requests_without_valid_token_are_rejected(guard):
    url, calls = guard
    assert requests.get(f"{url}/containers/demo-service/json", timeout=2).status_code == 401
    bad = {"Authorization": "Bearer wrong" + "x" * 30}
    assert requests.post(f"{url}/containers/demo-service/restart", headers=bad, timeout=2).status_code == 401
    assert calls == []


def test_inspect_is_filtered_and_never_leaks_environment(guard):
    url, calls = guard
    r = requests.get(f"{url}/containers/demo-service/json", headers=AUTH, timeout=2)
    assert r.status_code == 200
    body = r.json()
    assert body == {"name": "demo-service", "status": "running", "running": True, "health": "healthy",
                    "started_at": "2026-01-01T00:00:00Z", "restart_count": 2}
    assert "leak-me" not in r.text
    assert calls == [("GET", "/containers/demo-service/json")]


def test_restart_allowed_for_allowlisted_container(guard):
    url, calls = guard
    r = requests.post(f"{url}/containers/demo-service/restart?t=5", headers=AUTH, timeout=2)
    assert r.status_code == 200 and r.json() == {"restarted": "demo-service"}
    assert calls == [("POST", "/containers/demo-service/restart?t=5")]


def test_default_stop_timeout_is_applied(guard):
    url, calls = guard
    requests.post(f"{url}/containers/demo-service/restart", headers=AUTH, timeout=2)
    assert calls[-1] == ("POST", "/containers/demo-service/restart?t=10")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/containers/other/json"),                    # not allowlisted
        ("POST", "/containers/other/restart"),
        ("POST", "/containers/create"),                       # the classic escalation path
        ("POST", "/containers/demo-service/exec"),
        ("POST", "/containers/demo-service/stop"),
        ("POST", "/containers/demo-service/kill"),
        ("GET", "/containers/json"),                          # list everything
        ("GET", "/containers/demo-service/logs"),
        ("GET", "/images/json"),
        ("GET", "/version"),
        ("POST", "/containers/../images/create"),
        ("GET", "/containers/%2e%2e/json"),
        ("GET", "/containers/demo-service/json?size=1"),
    ],
)
def test_everything_else_is_refused_and_never_reaches_docker(guard, method, path):
    url, calls = guard
    r = requests.request(method, f"{url}{path}", headers=AUTH, timeout=2)
    assert r.status_code in (400, 403)
    assert calls == []


@pytest.mark.parametrize("query", ["t=999", "t=-1", "t=abc", "t=5&force=1"])
def test_bad_restart_parameters_are_rejected(guard, query):
    url, calls = guard
    r = requests.post(f"{url}/containers/demo-service/restart?{query}", headers=AUTH, timeout=2)
    assert r.status_code == 400 and calls == []


def test_bodies_and_other_verbs_are_rejected(guard):
    url, calls = guard
    restart = f"{url}/containers/demo-service/restart"
    assert requests.post(restart, headers=AUTH, data=b"{}", timeout=2).status_code == 400
    for verb in ("PUT", "DELETE", "PATCH"):
        r = requests.request(verb, f"{url}/containers/demo-service/json", headers=AUTH, timeout=2)
        assert r.status_code == 405
    assert calls == []


def test_client_end_to_end_and_client_side_allowlist(guard):
    url, calls = guard
    client = DockerGuardClient(url, TOKEN, allowed={"demo-service", "other"})
    state = client.inspect("demo-service")
    assert state.running and state.health == "healthy" and state.restart_count == 2
    client.restart("demo-service", stop_timeout_s=3)
    assert calls[-1] == ("POST", "/containers/demo-service/restart?t=3")
    with pytest.raises(GuardError, match="HTTP 403"):  # allowed by the client, refused by the guard
        client.restart("other")
    with pytest.raises(GuardError, match="allowlist"):  # refused by the client before any request
        DockerGuardClient(url, TOKEN, allowed={"demo-service"}).restart("other")


def test_client_reports_unreachable_guard():
    client = DockerGuardClient("http://127.0.0.1:9", TOKEN, allowed={"x"}, timeout_s=0.5)
    with pytest.raises(GuardError, match="unreachable"):
        client.restart("x")


def test_handler_refuses_weak_configuration():
    with pytest.raises(ValueError):
        make_handler(frozenset(), TOKEN, lambda *_: (200, b""))
    with pytest.raises(ValueError):
        make_handler(frozenset({"a"}), "short", lambda *_: (200, b""))


def test_allowlist_parsing():
    assert parse_allowlist("a, b ,,c") == frozenset({"a", "b", "c"})
    with pytest.raises(ValueError):
        parse_allowlist("ok,bad name")
