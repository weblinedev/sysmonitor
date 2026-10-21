# Changelog

All notable project changes are documented here. Versions use semantic versioning. Dates are ISO 8601.

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
