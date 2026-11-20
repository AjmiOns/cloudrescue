import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from config import TargetConfig
from health_check import HealthChecker


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(1.0)
        status = {"/ok": 200, "/bad": 503, "/redirect": 302}.get(self.path, 200)
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def target(url, timeout=1.0):
    return TargetConfig(name="t", container="t", url=url, timeout_s=timeout)


def test_ok(base_url):
    r = HealthChecker().check(target(base_url + "/ok"))
    assert r.ok and r.status_code == 200 and r.error is None


def test_unexpected_status_is_a_failure(base_url):
    r = HealthChecker().check(target(base_url + "/bad"))
    assert not r.ok and r.status_code == 503 and "503" in r.error


def test_redirect_is_not_followed_and_is_a_failure(base_url):
    r = HealthChecker().check(target(base_url + "/redirect"))
    assert not r.ok and r.status_code == 302


def test_timeout_is_a_failure(base_url):
    r = HealthChecker().check(target(base_url + "/slow", timeout=0.2))
    assert not r.ok and r.status_code is None and "timeout" in r.error


def test_connection_refused_is_a_failure():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = HealthChecker().check(target(f"http://127.0.0.1:{port}/health"))
    assert not r.ok and r.error == "connection failed"
