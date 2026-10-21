# Security

## Supported scope

SysMonitor 0.1.x is an initial release under deployment validation. It is not a promise of independent audit, high availability or guaranteed alert delivery. Keep the host operating system and upstream Kuma supported and patched.

## Boundaries

- Telegram authorization requires an exact numeric sender ID, the same exact private chat ID and private chat type. There is no first-user enrollment, username-based authorization, group access or arbitrary command execution.
- The bridge binds only to a numeric loopback address and accepts webhooks and telemetry with independent, constant-time-compared bearer tokens.
- The bridge's metrics key is separate from the Kuma admin password. It reads metrics over a local connection.
- The optional collector sends a fixed allowlist of measurements to a direct HTTPS URL with default certificate verification. Redirects are rejected. No listener is installed on production.
- The collector runs a fixed argv list for `systemctl is-active`; it accepts no remotely selected units or shell fragments.
- Runtime units drop capabilities, forbid privilege escalation, limit memory/tasks/CPU and restrict filesystem writes.
- User-facing reports escape HTML and omit raw probe errors and URLs. Logs omit Telegram API paths because they contain the bot token.

## Secrets and data

Place runtime environment files in `/etc/sysmonitor`, root-owned with mode 0600. Use independent, random 32+ character secrets. The bot token, collector token, metrics key, databases, backups and private inventory must never be committed. Example files contain placeholders only. Review the complete staged diff before publishing.

The state database contains event names, status, receipt timestamps, the current telemetry sample, authorization-independent update offsets and pending rendered messages. It is private operational data. Incidents are pruned after 90 days. Pending messages remain until delivered, with a 2,000-message queue cap. Monitor an extended delivery failure before the queue fills.

Tokens are in process memory. Root or a compromised service context can access its own secrets. `protect_content` is a Telegram convenience, not a confidentiality guarantee. Telegram receives the selected operational summaries sent to the owner's chat.

## Threats not solved here

Compromise of either operating system, compromise of the owner Telegram account, a malicious Kuma administrator, physical host failure, a regional network outage and Telegram availability require additional controls. A stolen bot token permits impersonating the bot even if server-side owner checks still reject unauthorized commands. Revoke a leaked token promptly.

Loopback is not a substitute for TLS on a public endpoint. Do not publish `/kuma`, the first-run setup screen or the bridge health endpoint. If a public TLS proxy is added, review authentication, source restrictions, limits, logs and certificate renewal as part of that deployment.

## Reporting

For a suspected vulnerability, use this repository's GitHub **Report a vulnerability** feature if enabled. Otherwise contact the maintainer privately through the established project contact channel. Do not post secrets, live server addresses or exploit details in a public issue. For vulnerabilities in Kuma, use [upstream's security reporting process](https://github.com/louislam/uptime-kuma/security).
