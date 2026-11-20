"""Typed, validated configuration for the monitor.

Fail fast: a bad value stops the process at start-up with a clear message instead of
producing surprising behaviour (for example a zero-second interval) in the middle of an
incident.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


class ConfigError(ValueError):
    """The configuration file is missing, malformed or contains an unsafe value."""


@dataclass(frozen=True)
class TargetConfig:
    name: str
    container: str
    url: str
    interval_s: float = 5.0
    timeout_s: float = 2.0
    failure_threshold: int = 3
    expected_status: int = 200


@dataclass(frozen=True)
class RecoveryConfig:
    max_restarts: int = 3
    window_s: float = 600.0
    cooldown_s: float = 60.0
    stop_timeout_s: int = 10
    verify_timeout_s: float = 30.0
    verify_interval_s: float = 2.0


@dataclass(frozen=True)
class EscalationConfig:
    ec2_reboot_enabled: bool = False
    ec2_reboot_cooldown_s: float = 1800.0


@dataclass(frozen=True)
class Config:
    targets: tuple[TargetConfig, ...]
    recovery: RecoveryConfig = RecoveryConfig()
    escalation: EscalationConfig = EscalationConfig()
    guard_url: str = "http://docker-guard:2375"
    db_path: str = "/data/incidents.db"
    metrics_port: int = 8001


def _number(data: Mapping, key: str, default, *, kind=float, minimum=None, maximum=None, where=""):
    raw = data.get(key, default)
    if isinstance(raw, bool):
        raise ConfigError(f"{where}{key} must be a number")
    try:
        value = kind(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}{key} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{where}{key} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{where}{key} must be <= {maximum}, got {value}")
    return value


def _http_url(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{label} must be an http(s) URL, got {value!r}")
    return value


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_target(raw: object, index: int) -> TargetConfig:
    where = f"targets[{index}]."
    if not isinstance(raw, Mapping):
        raise ConfigError(f"targets[{index}] must be a mapping")
    name = raw.get("name")
    container = raw.get("container", name)
    for label, value in (("name", name), ("container", container)):
        if not isinstance(value, str) or not _NAME_RE.match(value):
            raise ConfigError(f"{where}{label} must match {_NAME_RE.pattern}, got {value!r}")
    return TargetConfig(
        name=name,
        container=container,
        url=_http_url(raw.get("url"), f"{where}url"),
        interval_s=_number(raw, "interval_s", 5.0, minimum=0.5, maximum=3600, where=where),
        timeout_s=_number(raw, "timeout_s", 2.0, minimum=0.1, maximum=60, where=where),
        failure_threshold=_number(
            raw, "failure_threshold", 3, kind=int, minimum=1, maximum=100, where=where
        ),
        expected_status=_number(
            raw, "expected_status", 200, kind=int, minimum=100, maximum=599, where=where
        ),
    )


def parse_config(data: object, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    if not isinstance(data, Mapping):
        raise ConfigError("configuration root must be a mapping")

    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError("targets must be a non-empty list")
    targets = tuple(_parse_target(t, i) for i, t in enumerate(raw_targets))
    names = [t.name for t in targets]
    if len(set(names)) != len(names):
        raise ConfigError("target names must be unique")

    rec = data.get("recovery") or {}
    w = "recovery."
    recovery = RecoveryConfig(
        max_restarts=_number(rec, "max_restarts", 3, kind=int, minimum=1, maximum=20, where=w),
        window_s=_number(rec, "window_s", 600.0, minimum=1, where=w),
        cooldown_s=_number(rec, "cooldown_s", 60.0, minimum=0, where=w),
        stop_timeout_s=_number(rec, "stop_timeout_s", 10, kind=int, minimum=0, maximum=60, where=w),
        verify_timeout_s=_number(rec, "verify_timeout_s", 30.0, minimum=1, where=w),
        verify_interval_s=_number(rec, "verify_interval_s", 2.0, minimum=0.1, where=w),
    )

    esc = (data.get("escalation") or {}).get("ec2_reboot") or {}
    reboot_enabled = bool(esc.get("enabled", False))
    if "ESCALATION_EC2_REBOOT" in env:  # the VM bootstrap switches this on, not the repo file
        reboot_enabled = _as_bool(env["ESCALATION_EC2_REBOOT"])
    escalation = EscalationConfig(
        ec2_reboot_enabled=reboot_enabled,
        ec2_reboot_cooldown_s=_number(esc, "cooldown_s", 1800.0, minimum=0, where="escalation.ec2_reboot."),
    )

    return Config(
        targets=targets,
        recovery=recovery,
        escalation=escalation,
        guard_url=_http_url(env.get("GUARD_URL", data.get("guard_url", "http://docker-guard:2375")), "guard_url"),
        db_path=str(env.get("DB_PATH", data.get("db_path", "/data/incidents.db"))),
        metrics_port=_number(
            {"metrics_port": env.get("METRICS_PORT", data.get("metrics_port", 8001))},
            "metrics_port", 8001, kind=int, minimum=1, maximum=65535,
        ),
    )


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> Config:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)  # safe_load: never construct arbitrary Python objects
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return parse_config(data, env)
