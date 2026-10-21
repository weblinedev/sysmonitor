#!/usr/bin/env python3
"""Consistent local SQLite backups. Root-only output, no deletion or offsite claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the monitoring host")
    os.umask(0o077)
    root = Path("/var/backups/sysmonitor")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(mode=0o700)
    copied = []
    for source, name in ((Path("/var/lib/sysmonitor-kuma/kuma.db"), "kuma.db"),
                         (Path("/var/lib/sysmonitor/state.sqlite3"), "bridge.sqlite3")):
        if not source.exists():
            continue
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination / name) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise SystemExit("Backup verification failed")
        copied.append(name)
    for source, name in ((Path("/var/lib/sysmonitor-kuma/db-config.json"), "kuma-db-config.json"),
                         (Path("/etc/systemd/system/sysmonitor-kuma.service"), "sysmonitor-kuma.service"),
                         (Path("/etc/systemd/system/sysmonitor.service"), "sysmonitor.service")):
        if source.is_file():
            shutil.copyfile(source, destination / name)
            copied.append(name)
    if not any(name.endswith((".db", ".sqlite3")) for name in copied):
        raise SystemExit("No databases found; not a usable database backup")
    hashes = {name: hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in copied}
    (destination/"manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print("LOCAL_BACKUP_VERIFIED", destination)
    print("Secret environment files require a separate encrypted backup; this is not offsite recovery.")


if __name__ == "__main__":
    main()
