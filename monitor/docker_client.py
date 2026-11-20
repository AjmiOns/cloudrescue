"""Client for docker-guard (never talks to the Docker socket directly)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import quote

import requests


class GuardError(RuntimeError):
    """docker-guard refused, failed, or could not be reached."""


@dataclass(frozen=True)
class ContainerState:
    name: str
    status: str | None
    running: bool
    health: str | None
    started_at: str | None
    restart_count: int


class DockerGuardClient:
    """Allowlist check on the client as well as on the guard: defence in depth."""

    def __init__(self, base_url: str, token: str, allowed: Collection[str], timeout_s: float = 10.0):
        self._base = base_url.rstrip("/")
        self._token = token
        self._allowed = frozenset(allowed)
        self._timeout = timeout_s

    def _call(self, method: str, path: str, timeout: float) -> requests.Response:
        try:
            response = requests.request(
                method,
                f"{self._base}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise GuardError(f"docker-guard unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise GuardError(f"docker-guard refused {method} {path.split('?')[0]}: HTTP {response.status_code}")
        return response

    def _require_allowed(self, name: str) -> None:
        if name not in self._allowed:
            raise GuardError(f"container {name!r} is not in the monitor allowlist")

    def inspect(self, name: str) -> ContainerState:
        self._require_allowed(name)
        data = self._call("GET", f"/containers/{quote(name, safe='')}/json", self._timeout).json()
        return ContainerState(
            name=data["name"],
            status=data.get("status"),
            running=bool(data.get("running")),
            health=data.get("health"),
            started_at=data.get("started_at"),
            restart_count=int(data.get("restart_count", 0)),
        )

    def restart(self, name: str, stop_timeout_s: int = 10) -> None:
        self._require_allowed(name)
        path = f"/containers/{quote(name, safe='')}/restart?t={int(stop_timeout_s)}"
        self._call("POST", path, self._timeout + stop_timeout_s)
