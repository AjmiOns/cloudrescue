# CloudRescue

**A self-healing infrastructure lab.** CloudRescue watches a service, confirms when it is really
down, repairs it under strict limits, verifies the repair, records the incident and shows the
result on a dashboard. It then deploys the same stack to AWS with Terraform.

```
 detect ──> diagnose ──> recover ──> verify ──> log ──> observe
 /health    N failures    restart     healthy?   SQLite   Prometheus + Grafana
            in a row      (guarded)   or retry   + JSON   + alert rules
                              └── budget exhausted ──> escalate (alert, optional EC2 reboot)
```

> "Self-healing" here means **detect, restart, verify, escalate**, with bounded actions and a full
> audit trail. It does not diagnose root causes; the incident log and metrics help a human do that.

Architecture diagram: `docs/architecture.drawio` (open it in [diagrams.net](https://app.diagrams.net) and export a PNG for this page).

## Quick start (local, free)

Requirements: Linux/WSL2, Docker with Compose v2, `make`, Python 3.10+ (tests only).

```bash
make init        # creates .env with random secrets
make up          # builds and starts the stack
make demo        # breaks the service, waits for the repair, prints the incident
```

Open Grafana at <http://127.0.0.1:3000> (`admin`; password in `.env`). See `docs/runbook.md`.

## What is in the box

| Path | Purpose |
|---|---|
| `app/demo-service/` | FastAPI service with `/health`, `/metrics` and a token-protected failure injector (`unhealthy`, `hang`, `crash`). |
| `monitor/` | The core. `health_check.py` (detect), `recovery.py` (state machine, budget, verify), `incident_logger.py` (SQLite audit trail), `escalation.py`, `metrics.py`, `config.py` (validated config), `cli.py`. |
| `guard/` | `docker-guard`: the only component that touches `docker.sock`. Allowlist-only, two operations. |
| `monitoring/` | Prometheus config and alert rules; Grafana datasource and dashboard provisioned as code. |
| `infrastructure/terraform/` | VPC, public subnet, IGW, locked default SG, least-privilege SG, encrypted EC2 with IMDSv2, Elastic IP, hardened bootstrap. |
| `scripts/` | `init-env.sh`, `chaos.sh`, `demo.sh`. |
| `tests/` | Unit tests, a process-level integration test, and an opt-in Docker end-to-end test. |
| `.github/` | CI (lint, tests, compose build + e2e, terraform fmt/validate/Checkov, gitleaks) and Dependabot. |
| `docs/` | Architecture diagram, `security.md` (threat model), `runbook.md`. |

## How recovery works

1. **Detect.** One HTTP probe per `interval_s`. A refused connection, a timeout or an unexpected
   status counts as a failure. Redirects are not followed.
2. **Confirm.** Nothing happens until `failure_threshold` *consecutive* failures (default 3). Blips
   are visible (state `suspect`) but cause no action.
3. **Recover.** The monitor asks `docker-guard` to restart the container.
4. **Verify.** A restart counts only if the health check passes within `verify_timeout_s`.
5. **Bound.** At most `max_restarts` per `window_s` (default 3 per 10 min), `cooldown_s` apart. The
   budget is **not** reset by success, so a flapping service is stopped too.
6. **Escalate.** When the budget is exhausted: critical alert, and optionally an EC2 reboot (level 2,
   off by default). Once per incident.
7. **Record.** Every step lands in the incident timeline; every transition is a metric.

## Design decisions

| Decision | Why |
|---|---|
| Custom `docker-guard` instead of an off-the-shelf socket proxy | Common proxies that let a caller `POST /containers` also allow container *creation*, which is root on the host. We need two operations, so we implement exactly two and test the refusals. |
| `control` network is `internal`, shared only by monitor and guard | A compromised demo-service cannot reach the component that holds the socket. |
| Allowlist in the guard's environment, not in `config.yaml` | The monitor cannot widen its own privileges. |
| `restart: "no"` on demo-service | A Docker restart policy would silently heal failures and hide them from CloudRescue. A systemd unit starts the stack on boot. |
| Restart budget survives success | Detects flapping instead of restarting forever. |
| Logic is pure and injectable (clock, runtime, checker) | The state machine is tested deterministically, in milliseconds, without Docker. |
| Secrets generated on the VM at first boot | They never enter Git, `user_data` or Terraform state. |
| Level 2 (EC2 reboot) is opt-in | It needs IMDS hop limit 2, which weakens credential isolation. See `docs/security.md`. |
| Prometheus + Grafana self-hosted | Matches the roadmap and works within Learner Lab limits. |

## Tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
make test                                   # unit + process-level integration (no Docker)
CLOUDRESCUE_E2E=1 python -m pytest tests/integration/test_e2e_recovery.py -m e2e -s   # real containers
make lint
```

What the tests prove: the state machine (blips ignored, restart, verify, cooldown, budget, flapping,
escalate-once, external recovery), the guard (every forbidden operation is refused and never reaches
Docker, `Env` is never leaked), the config validator, the incident store (including SQL injection
payloads), the demo service (chaos endpoint protected), and a real monitor loop repairing a real
process in the three failure modes.

## Deploy to AWS (Learner Lab)

See `docs/runbook.md`. In short: `cp terraform.tfvars.example terraform.tfvars`, set `repo_url` and
`admin_cidr`, then `make tf-init tf-plan tf-apply`, and **`make tf-destroy` when you are done**.

## Verification status

| Item | Status |
|---|---|
| Python unit and process-level integration tests, ruff | Run and passing when this repository was generated |
| `docker compose up`, Docker images, docker-guard against a real daemon | **Not run in the generation environment** (no Docker there). CI runs a real-container end-to-end test. Run `make up && make demo` locally to confirm on your machine. |
| Terraform (`fmt`, `validate`, `plan`, `apply`) | **Not run** (no Terraform there). Run `terraform fmt -recursive` once before your first commit (CI checks it), then `validate`, and read `terraform plan` before applying. |
| Grafana dashboard JSON | Valid JSON, provisioned by file. Not rendered. Expect to tweak panels once you see real data. |

## Limitations and roadmap

- Single host, single instance of each component; no HA.
- Plain-HTTP Grafana (restricted by SG / SSH tunnel). Add TLS for anything real.
- No Alertmanager: alerts are visible in Prometheus and Grafana but not pushed anywhere.
- Roadmap: Alertmanager + Slack, S3 remote state, SSM Session Manager instead of SSH, log shipping,
  multiple targets and dependency-aware recovery, ECS/Kubernetes runtime backend.

MIT licensed.
