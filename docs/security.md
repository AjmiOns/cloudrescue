# CloudRescue security notes

CloudRescue is a system that is **allowed to change production state automatically**. That makes
the automation itself a privileged target, so the design starts from one question: *what is the
worst thing this component can do if it is compromised or buggy?*

## Trust boundaries

```
 Internet ──(SSH 22, Grafana 3000: admin_cidr only)──> Security Group ──> EC2 host
                                                                           │
   ┌───────────────────────────── cloudrescue-net ─────────────────────────┘
   │  demo-service   prometheus   grafana   monitor ──────────────┐
   │                                                              │
   └──────────────────────────────── control (internal) ──────────┼──> docker-guard ──> docker.sock
                                                                  (monitor and guard only)
```

| Boundary | Control |
|---|---|
| Internet → VM | Security group allows only 22 (and optionally 3000) from `admin_cidr`, validated to be /24 or narrower. `0.0.0.0/0` is rejected by Terraform. Egress limited to 80/443/123. |
| Container → Docker API | Nothing mounts `docker.sock` except `docker-guard`. |
| demo-service → control plane | `demo-service` is not on the `control` network, so even a fully compromised app cannot reach `docker-guard`. |
| monitor → docker-guard | Bearer token (`GUARD_TOKEN`) + allowlist enforced on both sides. |
| VM → AWS API | Only the pre-provisioned `LabInstanceProfile`; no IAM objects are created. |

## Threat model (STRIDE-lite)

| # | Threat | Mitigation | Residual risk |
|---|---|---|---|
| 1 | Compromised container escapes to host through the Docker socket | Only `docker-guard` has the socket. It exposes two operations (inspect-filtered, restart) for allowlisted names. It is stdlib-only and its behaviour is covered by tests (`tests/unit/test_guard.py`), including refusal of `create`, `exec`, `stop`, `kill`, path traversal and bodies. | A bug in `guard.py` or the Docker daemon itself. The guard process still holds socket access. |
| 2 | Monitor abused to restart arbitrary containers | Allowlist lives in the guard's environment, not in `monitor/config.yaml`. The monitor cannot widen it. | Compromise of the compose file or `.env` on the host. |
| 3 | Restart loop (DoS-by-automation) | Hysteresis, sliding-window budget (3 per 10 min), cooldown, and escalation instead of retrying forever. The budget is not reset by success, so flapping is also bounded. | Intentionally bounded: after escalation a human is needed. |
| 4 | Anonymous kill switch (`/fail`) | Disabled unless `CHAOS_TOKEN` is set; constant-time token comparison; port 8000 published to localhost only. | Anyone with the token and local access. This is a lab feature: remove or disable it in real services. |
| 5 | Secrets leak (Git, Terraform state, user_data) | `.env` and `*.tfvars` are git-ignored; secrets are generated **on the VM** at first boot, so they appear neither in user_data nor in Terraform state; gitleaks runs in CI. | Secrets sit in `/opt/cloudrescue/.env` (root, mode 600). Use SSM Parameter Store or Secrets Manager if your lab allows it. |
| 6 | SSRF to instance credentials | IMDSv2 required; hop limit 1 so containers cannot reach IMDS (unless level-2 is enabled, see below). | See level-2. |
| 7 | Information disclosure through inspect | The guard returns 6 fields; the real inspect output (env vars, mounts) is never passed through. | None known. |
| 8 | Log tampering / repudiation | Structured JSON logs from monitor and guard; incident timeline in SQLite; Docker log rotation. | Logs live on the same host as the system they describe; ship them off-box for real use. |
| 9 | Plain-text Grafana | Exposed only to `admin_cidr`, or use the SSH tunnel (`expose_grafana = false`). | HTTP without TLS. Put a reverse proxy with TLS in front for anything beyond a lab. |
| 10 | Supply chain (images, packages, actions) | Pinned versions, Dependabot, non-root images, read-only root filesystems, `cap_drop: ALL`, `no-new-privileges`, resource limits, Checkov and ruff-bandit in CI. | Tags are pinned, not digests. Pin digests for production. |
| 11 | The watchdog itself fails | `MonitorDown` alert, `restart: unless-stopped`, container healthcheck, stale incidents closed on start-up. | Prometheus has no Alertmanager here, so alerts are visible but not pushed. |

## Level-2 escalation (EC2 reboot): read before enabling

Level 2 lets the monitor reboot the whole VM when container restarts are not enough. It is **off by
default** because it needs:

* `ec2:RebootInstances` on the instance profile (check with the built-in start-up DryRun; Learner Lab
  roles may deny it);
* an IMDS hop limit of 2 so a container can obtain instance credentials. That setting is
  instance-wide, so **other containers on the host could also reach the credentials**, which weakens
  threat #6. Mitigate by keeping the role minimal and using a host firewall rule if you need both.

A reboot is also disruptive. The 30-minute cooldown prevents reboot loops.

## Learner Lab specifics

* No IAM role/user creation, so Terraform attaches `LabInstanceProfile`.
* Credentials include a session token and expire; they must never enter Git or Terraform files.
* The lab stops EC2 instances when the session ends. The Elastic IP persists and the
  `cloudrescue.service` systemd unit restarts the stack on boot (`demo-service` has `restart: "no"`
  by design, so it is started by that unit, not by Docker).

## Hardening checklist for going beyond a lab

- [ ] TLS in front of Grafana; SSO instead of a local admin password
- [ ] Remote Terraform state (S3 + locking) and `terraform plan` in CI
- [ ] Off-box log shipping and Alertmanager with a real receiver
- [ ] Image digests pinned and images scanned (Trivy) in CI
- [ ] Private subnet + SSM Session Manager instead of public SSH
- [ ] Remove the chaos endpoint from the service image
