# Contributing

Keep the production server's safety boundary intact. Small, testable changes are preferred.

1. Explain the operational problem and the behavior after the change.
2. Add a regression test for authorization, alert-state or delivery changes.
3. Run `python3 -m unittest discover -s tests -v` and `bash -n deploy/install-kuma.sh`.
4. Update `CHANGELOG.md` and any affected operations or security documentation.
5. Include observed tests and untested deployment assumptions in the pull request.

Do not add a remote shell, service restart command, Telegram first-user binding, disabled TLS verification, public unauthenticated dashboard, or production credentials. Do not introduce third-party runtime dependencies without a concrete need and a maintenance plan.

Deployment tests must use isolated fixtures or a dedicated test monitor. Do not stop a production web server to prove an alert works. Preserve upstream attribution and licenses.
