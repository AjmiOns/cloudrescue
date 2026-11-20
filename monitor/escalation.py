"""Escalation when automatic restarts are not enough (restart budget exhausted).

Level 1 (always): raise a critical alert (structured log line and metric).
Level 2 (opt-in): reboot the EC2 instance through the EC2 API using the instance profile.

Level 2 is off by default because it widens the blast radius: it needs ec2:RebootInstances
and, from inside a container, an IMDS hop limit of 2 (which also lets *other* containers reach
instance credentials). docs/security.md explains the trade-off.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from config import EscalationConfig

log = logging.getLogger("cloudrescue.escalation")

_IMDS = "http://169.254.169.254/latest"


@dataclass(frozen=True)
class EscalationResult:
    action: str
    performed: bool
    detail: str


class Escalator(Protocol):
    def escalate(self, target: str, reason: str) -> EscalationResult: ...


class AlertOnlyEscalator:
    def escalate(self, target: str, reason: str) -> EscalationResult:
        log.critical("escalation_required", extra={"target": target, "reason": reason})
        return EscalationResult("alert", True, "critical alert raised; manual intervention required")


def fetch_instance_identity() -> tuple[str, str]:
    """Return (instance_id, region) via IMDSv2 (token-based; IMDSv1 is disabled by Terraform)."""
    token = requests.put(
        f"{_IMDS}/api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}, timeout=2
    ).text
    headers = {"X-aws-ec2-metadata-token": token}
    instance_id = requests.get(f"{_IMDS}/meta-data/instance-id", headers=headers, timeout=2).text
    region = requests.get(f"{_IMDS}/meta-data/placement/region", headers=headers, timeout=2).text
    return instance_id, region


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", type(exc).__name__))
    return type(exc).__name__


class Ec2RebootEscalator:
    def __init__(
        self,
        cooldown_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        identity: Callable[[], tuple[str, str]] = fetch_instance_identity,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._cooldown = cooldown_s
        self._clock = clock
        self._identity = identity
        self._client_factory = client_factory or self._default_client
        self._last: float | None = None

    @staticmethod
    def _default_client(region: str) -> Any:
        import boto3  # imported lazily: only present when built with WITH_AWS=true

        return boto3.client("ec2", region_name=region)

    def preflight(self) -> tuple[bool, str]:
        """Check permissions with a DryRun call so a denied role is known at start-up."""
        try:
            instance_id, region = self._identity()
            self._client_factory(region).reboot_instances(InstanceIds=[instance_id], DryRun=True)
        except Exception as exc:  # noqa: BLE001 - boto raises many types; classify by code
            code = _error_code(exc)
            if code == "DryRunOperation":
                return True, "ec2:RebootInstances permitted"
            return False, f"ec2:RebootInstances not available ({code})"
        return True, "ec2:RebootInstances permitted"

    def escalate(self, target: str, reason: str) -> EscalationResult:
        log.critical("escalation_required", extra={"target": target, "reason": reason})
        now = self._clock()
        if self._last is not None and now - self._last < self._cooldown:
            return EscalationResult("ec2_reboot", False, "skipped: reboot cooldown active")
        try:
            instance_id, region = self._identity()
            self._client_factory(region).reboot_instances(InstanceIds=[instance_id])
        except Exception as exc:  # noqa: BLE001
            return EscalationResult("ec2_reboot", False, f"reboot failed ({_error_code(exc)})")
        self._last = now
        return EscalationResult("ec2_reboot", True, f"reboot requested for {instance_id}")


def build_escalator(cfg: EscalationConfig) -> Escalator:
    if cfg.ec2_reboot_enabled:
        return Ec2RebootEscalator(cfg.ec2_reboot_cooldown_s)
    return AlertOnlyEscalator()
