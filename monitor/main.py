"""CloudRescue monitor entry point."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone

import yaml
from prometheus_client import start_http_server

from config import ConfigError, load_config
from docker_client import DockerGuardClient
from escalation import Ec2RebootEscalator, build_escalator
from health_check import HealthChecker
from incident_logger import IncidentLogger
from metrics import Metrics
from recovery import TargetMonitor

log = logging.getLogger("cloudrescue.main")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy to grep, ship and audit."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler], force=True)


def main() -> int:
    setup_logging()
    try:
        cfg = load_config(os.environ.get("CONFIG_PATH", "/app/config.yaml"))
    except (ConfigError, OSError, yaml.YAMLError) as exc:
        log.error("invalid_configuration", extra={"detail": str(exc)})
        return 2

    token = os.environ.get("GUARD_TOKEN", "")
    if len(token) < 16:
        log.error("GUARD_TOKEN is missing or shorter than 16 characters; refusing to start")
        return 2

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    metrics = Metrics()
    start_http_server(cfg.metrics_port, addr="0.0.0.0", registry=metrics.registry)  # noqa: S104

    incidents = IncidentLogger(cfg.db_path)
    stale = incidents.close_stale("monitor restarted while this incident was open")
    if stale:
        log.warning("closed_stale_incidents", extra={"count": stale})

    runtime = DockerGuardClient(cfg.guard_url, token, allowed={t.container for t in cfg.targets})
    escalator = build_escalator(cfg.escalation)
    if isinstance(escalator, Ec2RebootEscalator):
        ok, detail = escalator.preflight()
        (log.info if ok else log.warning)("level2_preflight", extra={"permitted": ok, "detail": detail})

    checker = HealthChecker()
    threads = []
    for target in cfg.targets:
        metrics.init_target(target.name)
        monitor = TargetMonitor(
            target,
            cfg.recovery,
            checker=checker,
            runtime=runtime,
            incidents=incidents,
            metrics=metrics,
            escalator=escalator,
            sleep=stop.wait,
            should_stop=stop.is_set,
        )
        thread = threading.Thread(target=monitor.run, args=(stop,), name=f"monitor-{target.name}", daemon=True)
        thread.start()
        threads.append(thread)
        log.info("monitoring_started", extra={"target": target.name, "url": target.url, "container": target.container})

    stop.wait()
    log.info("shutting_down")
    for thread in threads:
        thread.join(timeout=15)
    incidents.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
