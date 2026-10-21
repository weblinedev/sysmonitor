# Changelog

All notable project changes are documented here. Versions use semantic versioning. Dates are ISO 8601.

## [0.2.0] - 2026-10-22

### Added

- Three disk alert levels at 80%, 90% and 95%, with two-point recovery hysteresis and no repeated unchanged-level alerts.
- CPU/RAM alerts above 90% for at least five elapsed minutes and five reports, resetting confirmation after a stale gap; recovery below 85%.
- Bounded unprivileged process-group sampling: only numeric CPU/RSS totals for fixed categories, never arguments, environment, usernames or customer paths.
- Core systemd service alerts after three failed/inactive reports over two minutes; recovery notifications, no automatic restart.
- Persistent daily reports at 23:00 Asia/Tehran by default, with owner-only on/off/time controls independent of incident alerts.
- Thirty-five-day daily numeric sample aggregates, explicit sampling limits and stale-data reporting.
- Isolated pinned Caddy 2.11.7 TLS ingress staging, source-IP/path restrictions, independent bearer authentication and credential refresh timer.
- Time-controlled tests for real elapsed alert confirmation, stale gaps, escalation/recovery and durable daily scheduling.

### Changed

- Collector uses the host curl with HTTP/1.1 after route-specific urllib body timeouts and HTTP/2 stream failures were reproduced; credentials/config are supplied over stdin, certificate validation stays enabled, and redirects/proxies are disabled.
- Added daily preview without consuming the scheduled evening report.
- Improved Persian status layout with separate service names and response-time fields.
- Resource alert policy supersedes the initial 0.1.0 three-sample policy.

## [0.1.0] - 2026-10-21

### Added

- Uptime Kuma 2.5.6 as the existing web and uptime engine, with an isolated Node 22.23.3 native-install recipe.
- Private Persian Telegram status, resources, uptime, incident and test commands with inline navigation.
- Exact numeric owner/private-chat authorization and fixed read-only operations.
- Authenticated loopback webhook receiver, sanitized alerts and certificate-notification handling.
- SQLite incident deduplication and an ordered durable notification outbox with bounded exponential retry.
- Read-only Kuma Prometheus ingestion, explicit data freshness and separate monitor-health warnings.
- Optional Linux collector for CPU, RAM, swap, root disk, uptime, load and fixed service states.
- Debounced resource alerts with hysteresis and reset of pending counts after a stale-data gap.
- Restricted systemd units, a one-minute collector timer and secret-file examples.
- Research, deployment, Persian operations, threat boundaries, recovery and acceptance documentation.
- Local unit and HTTP integration coverage for authorization, duplicate events, delivery failure/restart, malformed inputs, thresholds and stale data.

### Known limits

- Initial deployment acceptance is recorded separately in `docs/VERIFICATION.md`.
- No arbitrary remote administration, resource time-series UI or independent dead-man service is included.
- At-least-once Telegram delivery can duplicate a message after a crash between send and acknowledgement.
- Resource service states are reported on request; separate service-state alerts are not implemented.
- A public telemetry TLS endpoint must be configured and verified separately before enabling the collector.
