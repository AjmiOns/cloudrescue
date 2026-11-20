#!/usr/bin/env python3
"""docker-guard: a deliberately tiny, allowlist-only gateway to the Docker Engine API.

Why this exists
---------------
Mounting /var/run/docker.sock into a container is root on the host. Generic socket proxies
reduce the surface but still let a caller that may POST to /containers create arbitrary
(privileged) containers. CloudRescue needs exactly two operations, so this gateway
implements exactly two and refuses everything else:

  GET  /containers/<name>/json      -> a filtered view (status, health, start time only; the
                                       real inspect output contains env vars and secrets)
  POST /containers/<name>/restart   -> restart, with a bounded stop timeout

<name> must be in ALLOWED_CONTAINERS. Callers must send "Authorization: Bearer <GUARD_TOKEN>".
Every decision is written to stdout as one JSON audit line.

Standard library only, so there is nothing third-party in the component that holds the socket.
"""

from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

_NAME = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}"
_NAME_RE = re.compile(rf"^{_NAME}$")
_INSPECT_RE = re.compile(rf"^/containers/({_NAME})/json$")
_RESTART_RE = re.compile(rf"^/containers/({_NAME})/restart$")
MAX_STOP_TIMEOUT = 60
DEFAULT_STOP_TIMEOUT = 10

Upstream = Callable[[str, str], tuple[int, bytes]]


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def unix_upstream(socket_path: str, timeout: float = 90.0) -> Upstream:
    """Return a function that performs one request against the Docker daemon socket."""

    def call(method: str, path: str) -> tuple[int, bytes]:
        conn = _UnixHTTPConnection(socket_path, timeout)
        try:
            conn.request(method, path, headers={"Host": "docker"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    return call


def make_handler(allowed: frozenset[str], token: str, upstream: Upstream) -> type:
    """Build a request-handler class bound to an allowlist, a token and an upstream."""
    if not allowed:
        raise ValueError("allowlist must not be empty")
    if len(token) < 16:
        raise ValueError("token must be at least 16 characters")

    class GuardHandler(BaseHTTPRequestHandler):
        server_version = "docker-guard"
        sys_version = ""
        protocol_version = "HTTP/1.0"  # one request per connection: no keep-alive state

        # -- plumbing -------------------------------------------------------------------
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return  # replaced by structured audit lines

        def _audit(self, decision: str, status: int, detail: str = "") -> None:
            line = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "event": "guard_request",
                "client": self.client_address[0],
                "method": self.command,
                "path": self.path[:200],
                "decision": decision,
                "status": status,
            }
            if detail:
                line["detail"] = detail
            print(json.dumps(line), flush=True)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _deny(self, status: int, reason: str) -> None:
            self._audit("deny", status, reason)
            self._send(status, {"error": reason})

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            scheme, _, supplied = header.partition(" ")
            if scheme.lower() != "bearer" or not supplied:
                return False
            return hmac.compare_digest(supplied.strip().encode(), token.encode())

        def _call_upstream(self, method: str, path: str) -> tuple[int, bytes] | None:
            try:
                return upstream(method, path)
            except OSError as exc:
                self._audit("error", 502, f"upstream unreachable: {type(exc).__name__}")
                self._send(502, {"error": "docker daemon unreachable"})
                return None

        # -- verbs ----------------------------------------------------------------------
        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path == "/_ping":
                self._send(200, {"status": "ok"})
                return
            if not self._authorized():
                self._deny(401, "unauthorized")
                return
            match = _INSPECT_RE.match(parts.path)
            if not match or parts.query:
                self._deny(403, "operation not permitted")
                return
            name = match.group(1)
            if name not in allowed:
                self._deny(403, "container not permitted")
                return
            result = self._call_upstream("GET", f"/containers/{name}/json")
            if result is None:
                return
            status, body = result
            if status == 404:
                self._audit("allow", 404, "no such container")
                self._send(404, {"error": "no such container"})
                return
            if status != 200:
                self._audit("error", 502, f"upstream status {status}")
                self._send(502, {"error": "upstream error"})
                return
            try:
                info = json.loads(body)
            except ValueError:
                self._audit("error", 502, "invalid upstream JSON")
                self._send(502, {"error": "upstream error"})
                return
            state = info.get("State") or {}
            health = (state.get("Health") or {}).get("Status")
            self._audit("allow", 200, f"inspect {name}")
            self._send(
                200,
                {
                    "name": name,
                    "status": state.get("Status"),
                    "running": bool(state.get("Running")),
                    "health": health,
                    "started_at": state.get("StartedAt"),
                    "restart_count": info.get("RestartCount", 0),
                },
            )

        def do_POST(self) -> None:
            parts = urlsplit(self.path)
            if not self._authorized():
                self._deny(401, "unauthorized")
                return
            if int(self.headers.get("Content-Length") or 0) > 0:
                self._deny(400, "request bodies are not accepted")
                return
            match = _RESTART_RE.match(parts.path)
            if not match:
                self._deny(403, "operation not permitted")
                return
            name = match.group(1)
            if name not in allowed:
                self._deny(403, "container not permitted")
                return
            query = parse_qs(parts.query, keep_blank_values=True)
            if set(query) - {"t"}:
                self._deny(400, "unsupported query parameter")
                return
            try:
                stop_timeout = int(query.get("t", [str(DEFAULT_STOP_TIMEOUT)])[0])
            except ValueError:
                self._deny(400, "t must be an integer")
                return
            if not 0 <= stop_timeout <= MAX_STOP_TIMEOUT:
                self._deny(400, f"t must be between 0 and {MAX_STOP_TIMEOUT}")
                return
            result = self._call_upstream("POST", f"/containers/{name}/restart?t={stop_timeout}")
            if result is None:
                return
            status, _ = result
            if status == 204:
                self._audit("allow", 200, f"restart {name}")
                self._send(200, {"restarted": name})
            elif status == 404:
                self._audit("allow", 404, "no such container")
                self._send(404, {"error": "no such container"})
            else:
                self._audit("error", 502, f"upstream status {status}")
                self._send(502, {"error": "upstream error"})

        def _method_not_allowed(self) -> None:
            self._deny(405, "method not allowed")

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _method_not_allowed

    return GuardHandler


def parse_allowlist(raw: str) -> frozenset[str]:
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    bad = [n for n in names if not _NAME_RE.match(n)]
    if bad:
        raise ValueError(f"invalid container name(s): {', '.join(sorted(bad))}")
    return names


def main() -> int:
    try:
        allowed = parse_allowlist(os.environ.get("ALLOWED_CONTAINERS", ""))
        token = os.environ.get("GUARD_TOKEN", "")
        handler = make_handler(allowed, token, unix_upstream(
            os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
        ))
    except ValueError as exc:
        print(f"docker-guard refuses to start: {exc}", file=sys.stderr)
        return 2
    port = int(os.environ.get("LISTEN_PORT", "2375"))
    # Bound to all interfaces *inside the internal 'control' network only* (see docker-compose.yml).
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)  # noqa: S104
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    print(json.dumps({"event": "guard_started", "port": port, "allowed": sorted(allowed)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
