"""CloudRescue demo service.

A deliberately tiny API that acts as the "victim" of the lab. It can be broken on demand so
the monitor has something real to detect and repair.

Failure modes (POST /fail?mode=...):
  unhealthy   /health answers 503 until the process is restarted
  hang        /health stalls (the monitor sees a timeout) until the process is restarted
  crash       the process exits with code 1 (the container stops)

Security notes:
  * The chaos endpoint is disabled (404) unless CHAOS_TOKEN is set, and it requires that token
    in the X-Chaos-Token header. It is never an anonymous kill switch.
  * The failure state lives in memory only, so a restart always returns the service to "ok".
    That is exactly the property the recovery logic relies on.
"""

from __future__ import annotations

import hmac
import os
import threading
import time

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

VERSION = "1.0.0"
CHAOS_TOKEN = os.environ.get("CHAOS_TOKEN", "")
HANG_SECONDS = float(os.environ.get("HANG_SECONDS", "30"))
MODES = ("unhealthy", "hang", "crash")
_KNOWN_PATHS = {"/", "/health", "/fail", "/metrics"}

app = FastAPI(title="demo-service", docs_url=None, redoc_url=None, openapi_url=None)

registry = CollectorRegistry()
REQUESTS = Counter(
    "demo_service_requests_total",
    "HTTP requests handled by the demo service",
    ["path", "status"],
    registry=registry,
)
HEALTHY = Gauge(
    "demo_service_healthy", "1 if /health last answered 200", registry=registry
)
STARTED = Gauge(
    "demo_service_start_time_seconds", "Unix time the process started", registry=registry
)
STARTED.set(time.time())
HEALTHY.set(1)


class _Chaos:
    """Thread-safe holder for the current failure mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = "ok"

    def get(self) -> str:
        with self._lock:
            return self._mode

    def set(self, mode: str) -> None:
        with self._lock:
            self._mode = mode


chaos = _Chaos()


@app.middleware("http")
async def count_requests(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path if request.url.path in _KNOWN_PATHS else "other"
    REQUESTS.labels(path=path, status=str(response.status_code)).inc()
    return response


@app.get("/")
def index() -> dict:
    return {"service": "demo-service", "version": VERSION}


@app.get("/health")
def health() -> Response:
    # Sync endpoint on purpose: FastAPI runs it in a worker thread, so a simulated hang
    # does not block the event loop (and /metrics keeps answering).
    mode = chaos.get()
    if mode == "hang":
        time.sleep(HANG_SECONDS)
    if mode in ("unhealthy", "hang"):
        HEALTHY.set(0)
        return JSONResponse({"status": "unhealthy", "mode": mode}, status_code=503)
    HEALTHY.set(1)
    return JSONResponse({"status": "ok"})


@app.post("/fail", status_code=202)
def fail(
    mode: str = Query("unhealthy"),
    x_chaos_token: str | None = Header(default=None),
) -> dict:
    if not CHAOS_TOKEN:
        raise HTTPException(status_code=404, detail="Not Found")
    supplied = (x_chaos_token or "").encode()
    if not hmac.compare_digest(supplied, CHAOS_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {', '.join(MODES)}")
    if mode == "crash":
        # Exit after the response is flushed so the caller gets its 202.
        threading.Timer(0.2, os._exit, args=(1,)).start()
    else:
        chaos.set(mode)
    return {"accepted": True, "mode": mode}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
