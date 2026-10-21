# Deployment

## 1. Record the baseline

Use the existing authorized SSH session. Record time, OS, memory, root filesystem capacity, listening sockets and the health of existing services. Do not install a second service on an occupied port. On WHM, do not change CSF, Apache, PHP, MySQL or cPanel configuration to install this monitor.

Use a new source directory. Keep code root-owned, secrets in root-owned `0600` files, and application data in a separate state directory. Do not copy any real tokens, host inventories, credentials or backups into the Git repository.

## 2. Install Uptime Kuma on the monitoring host

Read `deploy/install-kuma.sh` before executing it. It is a **fresh-install script**, not an upgrade or repair script. It refuses an existing install; after a failure inspect its log and the partial state before retrying.

```bash
sudo bash deploy/install-kuma.sh
sudo systemctl status sysmonitor-kuma --no-pager
sudo journalctl -u sysmonitor-kuma -n 40 --no-pager
```

The script creates:

| Path | Role |
| --- | --- |
| `/opt/uptime-kuma` | Upstream application, root-owned after installation |
| `/opt/sysmonitor-node-v22.23.3` | Separate Node runtime |
| `/var/lib/sysmonitor-kuma` | Private Kuma state, owned by `syskuma` |
| `/etc/systemd/system/sysmonitor-kuma.service` | Restricted runtime service |
| `/root/sysmonitor-install-TIMESTAMP` | Baseline, checksums and installation log |

It does not install Docker, upgrade system Node, edit a firewall, or reuse the VPN's 443 port. The service is limited to 640 MiB and 75% of one CPU. Those are caps, not predicted usage; verify actual resident memory after installation.

## 3. Open the web console privately

Prefer an SSH tunnel through the existing host connection:

```bash
ssh -N -L 127.0.0.1:13001:127.0.0.1:3001 your-existing-monitor-host
```

Alternatively in Termius create a **Local** forwarding rule: local bind `127.0.0.1`, local port `13001`, intermediate host the existing monitoring host, destination `127.0.0.1:3001`. Use **Connect** and verify the rule stays connected.

Open `http://127.0.0.1:13001`. The browser-to-local endpoint is HTTP; the remote leg is encrypted by SSH. It is not a public plaintext dashboard.

Choose SQLite for this small installation, create a strong owner account, and keep authentication enabled. Enable 2FA from the owner's account when enrolling it. Do not expose the first-run setup page to the Internet.

Configure actual, verified target addresses in Kuma. Start with HTTPS for the main web endpoint, WHM login endpoint and cPanel login endpoint; retain TLS verification. A login page being reachable does not prove authenticated administration, database correctness, checkout functionality or all customer websites work.

Recommended initial policy: 60-second interval, 15-second request timeout, 2 retries, 60-second retry interval, no repeated reminder spam. Detection is roughly a few minutes, not instantaneous. Test the exact timing; response delays and scheduling matter. Use maintenance windows before planned work.

## 4. Create and connect the Telegram bot

Create a dedicated bot in BotFather. Verify the owner's **numeric** Telegram user ID through a trusted account surface. Do not bind the bot to whichever user sends `/start` first.

Keep the token private. Only one process may use `getUpdates` for that bot. Before enabling long polling, inspect `getWebhookInfo`; remove a webhook only if it belongs to this project and is not required by another integration. Do not drop pending updates casually.

The owner must open the bot chat and send `/start` before the bot can send private alerts.

Copy the source to `/opt/sysmonitor`, root-owned and readable by the service. Copy `examples/bridge.env.example` to `/etc/sysmonitor/bridge.env`, set owner/token values, and generate independent webhook and telemetry secrets. Create a read-only metrics API key in Kuma settings; use an expiry date and record its renewal date privately.

```bash
sudo install -d -m 700 /etc/sysmonitor
# Populate /etc/sysmonitor/bridge.env through a secure local file workflow.
sudo chmod 600 /etc/sysmonitor/bridge.env
sudo install -m 644 deploy/sysmonitor.service /etc/systemd/system/sysmonitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now sysmonitor
curl --fail http://127.0.0.1:9081/healthz
```

`/healthz` starts as 503 until all workers complete an iteration. It checks worker progress, not guaranteed Telegram delivery or end-user receipt. Check pending outbox records separately if delivery is delayed.

In Kuma add a **Webhook** notification:

- URL: `http://127.0.0.1:9081/kuma`
- HTTP method: POST
- Content type: JSON (the standard Kuma object, not form-data)
- Additional headers: `{"Authorization":"Bearer YOUR_KUMA_WEBHOOK_TOKEN"}`
- Attach it to the intended monitors and run the notification test.

The bridge reads `/metrics` with Basic Auth using an empty username and the metrics API key as the password. It does not need the Kuma administrator password during normal operation.

Initial provisioning can use the exact upstream version's Socket.IO operations once, with careful state inspection. The long-running bridge does not depend on those management operations and never writes directly to Kuma's database.

## 5. Optional resource telemetry

Do this only after the uptime path works. Keep the collector non-root. It reads `/proc`, root filesystem usage and a fixed list of systemd service states. It does not access customer files, passwords, database contents, logs or process command lines.

Provide a **direct HTTPS** endpoint terminating at a separately reviewed reverse proxy on the monitoring host. Use a spare port such as 18443 only after verifying it is free and allowed. Keep the existing VPN listeners in place. Configure the proxy to:

1. Expose only `POST /telemetry/iran`, proxying to `127.0.0.1:9081`.
2. Use a valid certificate and TLS 1.2+; never disable certificate verification.
3. Limit request bodies to 16 KiB, add request timeouts and a small rate limit.
4. Restrict source IP to the production host where possible, preserving the independent bearer check.
5. Avoid logging authorization headers or request bodies.
6. Return 404 for `/kuma`, `/healthz` and unrelated paths on that public listener.

Do not treat an incomplete proxy example as an installed secure endpoint. The repository deliberately does not edit the host's existing proxy or firewall automatically.

On the production host, verify a recoverable checkpoint for any files being replaced. Fresh installs should refuse existing SysMonitor paths. Copy the collector package and unit files to their documented paths, set `/etc/sysmonitor/collector.env` with mode 0600, then install the service and timer:

```bash
sudo install -m 644 deploy/sysmonitor-collector.service /etc/systemd/system/
sudo install -m 644 deploy/sysmonitor-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start sysmonitor-collector.service
sudo journalctl -u sysmonitor-collector.service -n 15 --no-pager
# Verify reception and actual measurement before enabling the recurring timer.
sudo systemctl enable --now sysmonitor-collector.timer
```

Run from `/opt/sysmonitor`. Check the unit's hard resource and execution-time limits. Add a rollback note before activation. No reboot or existing production-service restart is necessary.

## 6. Back up and rehearse recovery

Use SQLite's online backup API or stop only the relevant monitoring service for a consistent snapshot. Do not copy only a live database file while ignoring its WAL. Preserve the Kuma database/config and the bridge database separately, plus the unit files and encrypted secret files. Store a copy outside the monitoring host.

Restore into an isolated test directory, run `PRAGMA quick_check`, and compare monitor counts and pending alerts. Verify permissions. Never point a test restore at production or poll the real Telegram token from both instances. Local backup existence alone is not verified offsite recovery.

## 7. Rollback

On production: disable the collector timer, then stop its oneshot unit if it is running. On the monitor host: stop and disable `sysmonitor` and `sysmonitor-kuma`. Keep their state directories and install record for recovery. Disconnect the Termius local forwarding rule. Existing x-ui and WHM services do not need a restart.

```bash
sudo systemctl disable --now sysmonitor-collector.timer
sudo systemctl stop sysmonitor-collector.service
# Monitoring host only:
sudo systemctl disable --now sysmonitor sysmonitor-kuma
```

Remove any newly added proxy/firewall configuration by restoring its explicit checkpoint, test its configuration, and reload only that proxy if one was added. Archive source/state before deleting anything. Do not delete the pre-existing runtime or shared service files.
