# Runbook

## Start / stop (local)

```bash
make init      # once: creates .env with random secrets
make up        # build + start
make ps
make down
```

| UI | URL |
|---|---|
| Grafana | http://127.0.0.1:3000 (user `admin`, password: `grep GRAFANA_ADMIN_PASSWORD .env`) |
| Prometheus | http://127.0.0.1:9090 |
| Demo service | http://127.0.0.1:8000/health |

## Demonstrate self-healing

```bash
make demo                 # inject 503s, wait for recovery, print the incident
make chaos-hang           # /health times out
make chaos-crash          # process exits
make chaos-stop           # container stopped (connection refused)
make logs                 # watch detection > restart > verification live
make incidents            # history with time-to-recover
docker compose exec -T monitor python cli.py events 1    # timeline of incident 1
```

Expected timeline with default settings (derived from the config, not a benchmark): failure at t=0, confirmed at about 10-15 s (3 checks every
5 s), restart, verified healthy about 2-6 s later. Time-to-recover is roughly 15-25 s.

## Reading the signals

| Signal | Meaning |
|---|---|
| `cloudrescue_state_code` 0 / 1 / 2 / 3 / 4 | healthy / suspect / failed / recovering / escalated |
| `recovery_attempts{result="success"}` | restarted and verified healthy |
| `recovery_attempts{result="verify_failed"}` | restarted but still unhealthy: look at the service logs |
| `recovery_attempts{result="restart_error"}` | docker-guard refused or failed: check `docker compose logs docker-guard` |
| Alert `RecoveryEscalated` | restart budget exhausted: a human must investigate |
| Alert `MonitorDown` | the watchdog is gone: nothing will self-heal |

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `docker compose up` fails with "run `make init`" | `.env` missing: run `make init`. |
| monitor logs `docker-guard refused ... HTTP 401` | `GUARD_TOKEN` differs between services: recreate with `make down && make up`. |
| docker-guard cannot open the socket | Wrong `DOCKER_GID`: `getent group docker \| cut -d: -f3`, fix `.env`, recreate. |
| `refused ... HTTP 403` | Container not in `ALLOWED_CONTAINERS` (guard) or name mismatch with `container:` in `monitor/config.yaml`. |
| Incident stays open, state `escalated` | Restarts did not fix it. Inspect `docker compose logs demo-service`; fix the root cause. The incident closes itself (`recovered_externally`) once health returns. |

## Deploy to AWS Learner Lab

1. Start the lab and copy the temporary credentials (access key, secret **and session token**) into
   `~/.aws/credentials` (never into the repo).
2. `ssh-keygen -t ed25519 -f ~/.ssh/cloudrescue`
3. `cd infrastructure/terraform && cp terraform.tfvars.example terraform.tfvars`, then set `repo_url`
   (a public repo) and `admin_cidr` (`curl -s https://checkip.amazonaws.com` + `/32`).
4. `make tf-init tf-plan` and **read the plan**, then `make tf-apply`.
5. Wait about 5 minutes. Follow the bootstrap: `sudo tail -f /var/log/cloudrescue-bootstrap.log`.
6. Grafana password: `sudo grep GRAFANA_ADMIN_PASSWORD /opt/cloudrescue/.env`.
7. Break it on the VM: `cd /opt/cloudrescue && sudo scripts/chaos.sh stop`, then `sudo docker compose exec monitor python cli.py incidents`.
8. **When finished:** `make tf-destroy` (the lab has a credit budget).

After a lab restart, the instance boots, `cloudrescue.service` starts the stack, and the Elastic IP is
unchanged. Credentials are new each session, so update `~/.aws/credentials` before running Terraform again.
