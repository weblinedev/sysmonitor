# Research and architecture decision

Research date: **2026-10-21**. Sources below are official project documentation, source code and release metadata. Version claims are a dated snapshot and must be rechecked before an upgrade.

## Actual problem

Monitor a production WHM/cPanel server from a separate monitoring host, provide uptime and recovery alerts in a private Telegram chat, allow read-only status queries, and provide a web console. The monitor shares its host with an existing VPN service, so port conflicts, memory usage and changes to firewall behavior matter.

The hosting server should not receive a large monitoring stack or an unrestricted remote-control credential merely to report a few measurements.

## Options considered

| Option | Strength | Cost or gap for this task | Decision |
| --- | --- | --- | --- |
| Uptime Kuma | Existing web UI, HTTP/TCP/DNS checks, TLS information, Telegram notifications and metrics | Interactive Telegram commands need a companion; not a full resource-metrics platform | Use as uptime engine and web console |
| Beszel | Lightweight hub/agent resource monitoring, history and alerts | Another agent and hub to manage; chat command experience still requires integration | Good next step if historical resource charts become necessary |
| Netdata parent/child | Detailed resource diagnostics and streaming to a parent | More configuration and telemetry than this initial scope needs | Defer |
| Prometheus + exporters + Alertmanager + Grafana | Flexible metrics, retention and alert routing at scale | Several services and a larger maintenance surface for a two-server need | Defer until scale justifies it |
| New custom uptime engine | Full control of all behavior | Need to own HTTP/DNS/TLS behavior, state, scheduling, maintenance windows, history, migrations and incident semantics | Reject |

This is an engineering judgment for the stated scope, not a benchmark or a universal ranking.

## Chosen boundary

Use Kuma's web UI and uptime checks. Keep the bridge small: documented Prometheus metrics for reads, an authenticated local webhook for alerts, a fixed owner allowlist, Telegram long polling, and a SQLite outbox. Avoid unofficial management APIs and direct writes to Kuma's database.

An optional collector sends only numeric resource measurements and fixed service statuses over verified HTTPS. It runs without root privileges and accepts no remote commands. The production host does not need a new inbound listening socket.

Native installation on the monitoring host avoids introducing Docker and its network/firewall integration solely for this project. Node is installed into a dedicated `/opt` directory rather than upgrading the host's existing runtime.

## Upstream details verified

- GitHub's latest stable release API returned **Kuma 2.5.6**, published 2026-10-09. The tag resolves to `7ca4a3debd76443d8452da95d4040200a1479af4`.
- The current development branch identifies itself as a 3.0 beta. It is not the deployment target.
- Kuma's Prometheus source defines `monitor_status` values: `0` down, `1` up, `2` pending, `3` maintenance; response time is in milliseconds for `monitor_response_time`; uptime ratios are fractions labeled with a window.
- The webhook provider posts JSON containing `heartbeat`, `monitor` and `msg`, and supports additional headers. SysMonitor forwards only a sanitized name, status and timestamp, not the raw URL or diagnostic message.
- Telegram supports long polling with `getUpdates`, update offsets and callback queries. One consumer owns the token's update stream; a second process must not poll the same bot.
- Node's official latest-v22 checksum manifest returned **22.23.3** at the time of research. The installer verifies the archive against that version's checksum manifest retrieved over HTTPS. This checks transfer integrity; it is not a detached-signature verification procedure.

## SysReport review

The owner's `amirkhateri/sysreport` source was inspected at the current public checkout; its package identifies itself as 1.1.3. It is a modular human-readable Bash audit CLI. The system module reads uptime, load, memory and disk; identity performs external IP and location requests; network probes external targets and lists listening ports.

Running the complete report every minute would add unnecessary work and collect information beyond the telemetry requirement. Parsing its prose would also couple the monitor to presentation changes. Keep SysReport as a manual diagnostic tool; use the narrowly scoped collector for scheduled numeric telemetry. No change to SysReport is required.

## Failure and risk analysis

| Failure | Consequence | Mitigation / remaining limitation |
| --- | --- | --- |
| Iran-to-Turkey network issue | HTTP checks and/or telemetry fail | Describe observed reachability, not an unproven server crash; compare public probes with resource freshness |
| Monitoring host outage | Bot and checks stop together | Independent dead-man monitor outside both hosts remains necessary |
| Telegram outage or filtering | Alerts queue up | Durable outbox and bounded exponential retry; no guaranteed delivery time |
| Bot token leak | Attacker can impersonate the bot | Secret files, no URL logging, owner checks; revoke token and redeploy if leaked |
| Unauthorized Telegram user | Attempted information disclosure | Exact sender ID, exact private chat ID, fixed commands; no trust-on-first-use enrollment |
| Public dashboard exposure | Administrative attack surface | Loopback by default; SSH tunnel first, reviewed TLS/auth proxy only if needed |
| Monitoring load affects VPN | Resource contention | CPU/memory/tasks limits; observe real resident memory and service health after installation |
| Production collector defect | Extra resource usage | Non-root, hard resource/time limits, fixed units, once-a-minute schedule, easy timer stop |
| Duplicate or delayed events | Confusing notifications | Webhook deduplication, delivery order preserved; at-least-once delivery caveat remains |

## Primary sources

- [Kuma repository and features](https://github.com/louislam/uptime-kuma)
- [Kuma 2.5.6 release](https://github.com/louislam/uptime-kuma/releases/tag/2.5.6)
- [Kuma release API](https://api.github.com/repos/louislam/uptime-kuma/releases/latest)
- [Kuma Prometheus integration](https://github.com/louislam/uptime-kuma/wiki/Prometheus-Integration)
- [Pinned Prometheus source](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/prometheus.js)
- [Pinned webhook provider](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/notification-providers/webhook.js)
- [Pinned Telegram provider](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/notification-providers/telegram.js)
- [Beszel overview](https://beszel.dev/guide/what-is-beszel)
- [Beszel getting started](https://beszel.dev/guide/getting-started)
- [Beszel notifications](https://beszel.dev/guide/notifications/)
- [Netdata parent/child reference](https://learn.netdata.cloud/docs/netdata-parents/parent-child-configuration-reference)
- [Prometheus alerting overview](https://prometheus.io/docs/alerting/latest/overview/)
- [Alertmanager Telegram configuration](https://prometheus.io/docs/alerting/latest/configuration/#telegram_config)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Telegram Bot FAQ](https://core.telegram.org/bots/faq)
- [Node 22.23.3 official checksum manifest](https://nodejs.org/dist/v22.23.3/SHASUMS256.txt)
- [Owner's SysReport](https://github.com/amirkhateri/sysreport)
