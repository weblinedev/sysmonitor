# Verification and acceptance

## Local implementation checks

On 2026-10-21, the local Python unittest suite passed **12 tests**. Coverage includes:

- private owner authorization and rejection of foreign/group messages;
- callback authorization and persistent update offsets;
- webhook incident deduplication across a new database connection;
- failed Telegram delivery retained and delivered by a restarted application;
- preserving down-before-recovery delivery order;
- certificate notifications without a heartbeat;
- parsing escaped Prometheus labels and rejecting non-finite values;
- explicitly marking stale status and telemetry;
- telemetry schema restrictions, type/range validation and fixed unit names;
- resource-warning debounce and recovery hysteresis;
- refusing public receiver binds, remote Kuma origins and shared secrets;
- a real local HTTP receiver tested for bearer authentication, token separation, payload-size bounds and persisted events.

`bash -n deploy/install-kuma.sh` and Python compile checks also passed. These tests use a fake Telegram API and temporary databases. They do **not** prove real Telegram delivery, real systemd enforcement, production collector correctness or recovery from a host outage.

## Deployment observations

The monitoring host received a fresh native Kuma 2.5.6 installation with a dedicated Node 22.23.3 runtime. `sysmonitor-kuma` and the pre-existing x-ui service were observed active after installation. The selected Kuma port was loopback-only.

A private local SSH forwarding rule was created in Termius, and the browser loaded the real status page through that tunnel. Three HTTPS monitors for the main endpoint, WHM and cPanel were observed UP. TLS verification stayed enabled. This is a short initial observation window, not a historical availability claim.

At the initial checkpoint the production host was inspected read-only; no WHM service, firewall or package configuration had been changed by this task.

The private deployment handoff outside the public repository records current installation progress and any remaining acceptance items. Do not infer that the remaining checks below passed unless that handoff explicitly records the result.

## End-to-end acceptance checklist

| Check | Required evidence |
| --- | --- |
| Owner enrollment | Trusted numeric owner ID, owner's actual `/start`, expected bot identity from `getMe` |
| Real status command | Owner sends `/status`; current three monitor states appear |
| Buttons | Owner clicks a callback button and receives the corresponding report |
| Unauthorized use | A separate test user/group cannot receive operational data |
| Notification test | Kuma test notification reaches the owner's chat |
| Down and recovery | A dedicated harmless test target fails and recovers; both messages arrive in order |
| Persistent retry | A controlled delivery failure queues a message; after recovery it is delivered |
| Telemetry | Actual Linux sample received over verified TLS with the collector running non-root |
| Freshness | Stop only the collector timer temporarily; stale warning appears and recovery follows |
| Resource warning | Isolated synthetic input proves threshold/hysteresis; do not load-test production |
| Web | The owner can open the actual console/status page via the documented access route |
| Existing services | x-ui and WHM services remain healthy; old listeners stay present |
| Backups | SQLite-consistent backup, integrity check, isolated restore and offsite copy verified separately |
| Monitor outage | Independent observer detects monitoring-host loss; otherwise document the gap |

Never stop the production web server or edit a firewall merely to simulate a failure. Use a named test monitor against a disposable loopback target on the monitoring host. Label drill messages clearly and leave actual server monitoring in place.

## Recovery semantics

The bridge retries a failed Telegram send with increasing delays capped at five minutes and retains pending messages across a normal restart. It processes the oldest queue record first. Incoming heartbeat events are deduplicated by monitor ID, status and Kuma timestamp. Test and non-heartbeat certificate notifications are not deduplicated as incidents.

The outbox is at-least-once. A crash after Telegram accepts a message but before the local row is deleted can produce a duplicate. A full 2,000-row outbox returns a receiver error instead of silently discarding an alert; investigate extended delivery failures well before that cap.

## Before calling this production-ready

Complete the actual owner-chat tests, safe down/recovery drill, secret-file and unit-permission checks, resource measurements under the runtime limits, and backup/restore checks. Record exact observed results and limits. This document deliberately does not convert implementation tests into claims of production readiness.


## Continuation evidence — 2026-10-22

Version 0.2.0's 16 tests passed locally, on the monitoring host and in GitHub Actions (Python 3.9 and 3.12). Added cases cover elapsed-time CPU confirmation, stale-gap reset, disk escalation/hysteresis, service-state confirmation, contributor schema restrictions and daily-report persistence independent of alert policy.

The owner's actual `/start`, `/status` and `/resources` were observed in Telegram, with real replies. The owner pressed the test callback and its success reply was observed. Real numeric resources and fixed process-group summaries from the production host reached the owner's chat. The initial 83.5% root disk sample produced the yellow warning. All five sampled service units were active in the observed report.

A separate loopback-only target and monitor named `DRILL | Safe local target` returned 503 and then 204. Both down and recovery notifications were observed in order in the owner chat. The test monitor was subsequently paused and its temporary target stopped. Production services were not stopped for this drill. The live outbox was observed empty after delivery.

A local online backup of both SQLite databases passed checksum verification and an isolated restore with `PRAGMA integrity_check=ok`. Offsite transfer is a separate acceptance item; this statement does not claim it.

The scoped Caddy receiver passed configuration/certificate validation. A port already permitted by the production outbound policy was selected after the initial port was refused; neither production firewall rules nor existing VPN listeners were changed. The collector ran successfully with `DynamicUser=yes` and a root-owned 0600 environment file inside a 0700 directory. A timer was activated. Subsequent request-body timeouts were detected by the real stale-data warning and were being corrected with a bounded body buffer and longer finite network deadline. Require successful consecutive fresh samples before recording sustained telemetry acceptance.

Still track live acceptance separately for daily on/off/preview, stable telemetry after timeout repair, final permissions/listener comparison and offsite backup. Independent detection of the monitoring host's own outage remains outside this two-host installation.

## Transport repair and recovery copy — 2026-10-22

The final bounded collector transport uses the existing system curl with HTTP/1.1, verified HTTPS, no proxy, no redirects and credentials supplied on stdin. One actual non-root production collection completed successfully after this repair. Consecutive scheduled runs and the corresponding stale-recovery message still require live confirmation; a single successful request is not sustained acceptance. All 17 local tests passed again, including rejection of non-202 transport responses and keeping the token off argv.

The owner switched daily reports off and back on and both acknowledgments were observed; the final observed setting was enabled at 23:00 Asia/Tehran. A preview command was sent, but its reply has not yet been verified.

A newer online recovery archive was copied to the owner's Mac. Its SHA-256 matched the source, authenticated AES-256-GCM encryption and decrypt/readback succeeded, and both restored SQLite databases returned integrity_check=ok. The key is stored separately from the encrypted artifact in a private local directory. This is a verified manual offsite copy, not automatic offsite backup or protection from compromise of that Mac. VPN certificate private keys are not included.

The proposed agent/witness architecture, bounded evidence retention, per-server ownership, observers and Bale adapter are documented in [RFC-001](architecture/RFC-001-agent-witness.fa.md). They are not implemented by the current release. Before a WAL-based expansion, verify the actual SQLite version/vendor backport against the upstream WAL-reset fix; do not upgrade WHM system libraries implicitly.
