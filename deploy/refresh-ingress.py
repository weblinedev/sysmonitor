#!/usr/bin/env python3
"""Reload only this project's TLS ingress when its external certificate changes."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    os.umask(0o077)
    unit = Path("/etc/systemd/system/sysmonitor-ingress.service").read_text()
    paths = {}
    for line in unit.splitlines():
        if line.startswith("LoadCredential=tls."):
            name, path = line.removeprefix("LoadCredential=").split(":", 1)
            paths[name] = Path(path)
    if set(paths) != {"tls.crt", "tls.key"}:
        raise SystemExit("Unexpected credential configuration")
    content = {name: path.read_bytes() for name, path in paths.items()}
    fingerprint = hashlib.sha256(content["tls.crt"] + b"\0" + content["tls.key"]).hexdigest()
    state = Path("/var/lib/sysmonitor-ingress-refresh")
    state.mkdir(mode=0o700, exist_ok=True)
    marker = state / "fingerprint"
    if marker.exists() and marker.read_text() == fingerprint:
        return
    with tempfile.TemporaryDirectory(prefix="sysmonitor-cert-check-") as directory:
        for name, value in content.items():
            (Path(directory)/name).write_bytes(value)
        result = subprocess.run(["/opt/sysmonitor-caddy-2.11.7/caddy", "validate", "--config",
            "/etc/sysmonitor/Caddyfile", "--adapter", "caddyfile"],
            env={**os.environ, "CREDENTIALS_DIRECTORY": directory}, capture_output=True)
        if result.returncode:
            raise SystemExit("Updated certificate/config validation failed; running ingress left untouched")
    subprocess.run(["systemctl", "try-restart", "sysmonitor-ingress.service"], check=True)
    marker.write_text(fingerprint)
    print("TLS credentials refreshed for SysMonitor ingress only")


if __name__ == "__main__":
    main()
