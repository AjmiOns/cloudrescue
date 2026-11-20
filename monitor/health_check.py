"""Active health probing."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from config import TargetConfig


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    status_code: int | None
    latency_s: float
    error: str | None = None


class HealthChecker:
    """Performs one HTTP probe and classifies it. Never raises.

    A probe fails when the connection is refused, the request times out, or the status code
    differs from the expected one. Redirects are not followed, so a misconfigured proxy that
    answers 302 cannot masquerade as "healthy".
    """

    def check(self, target: TargetConfig) -> CheckResult:
        started = time.monotonic()
        try:
            response = requests.get(target.url, timeout=target.timeout_s, allow_redirects=False)
        except requests.Timeout:
            return CheckResult(False, None, time.monotonic() - started, f"timeout after {target.timeout_s:g}s")
        except requests.ConnectionError:
            return CheckResult(False, None, time.monotonic() - started, "connection failed")
        except requests.RequestException as exc:
            return CheckResult(False, None, time.monotonic() - started, type(exc).__name__)

        latency = time.monotonic() - started
        if response.status_code == target.expected_status:
            return CheckResult(True, response.status_code, latency)
        return CheckResult(False, response.status_code, latency, f"unexpected status {response.status_code}")
