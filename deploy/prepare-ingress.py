#!/usr/bin/env python3
"""Stage a narrowly scoped Caddy TLS receiver. Does not start or enable it."""
import argparse
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import urllib.request

VERSION = "2.11.7"
ARCHIVE_SHA256 = "727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hostname", required=True)
    p.add_argument("--source-ip", required=True)
    p.add_argument("--certificate", required=True)
    p.add_argument("--private-key", required=True)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("root required")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", args.hostname):
        p.error("invalid hostname")
    source = str(ipaddress.ip_address(args.source_ip))
    cert, key = Path(args.certificate), Path(args.private_key)
    if not all(x.is_absolute() and x.is_file() and re.fullmatch(r"/[a-zA-Z0-9/_.-]+", str(x)) for x in (cert, key)):
        p.error("absolute existing certificate/key paths required")
    unit = Path("/etc/systemd/system/sysmonitor-ingress.service")
    config = Path("/etc/sysmonitor/Caddyfile")
    if unit.exists() or config.exists():
        p.error("ingress already staged; inspect it before changing")
    os.umask(0o077)
    binary = Path(f"/opt/sysmonitor-caddy-{VERSION}/caddy")
    with tempfile.TemporaryDirectory(prefix="sysmonitor-ingress-") as directory:
        scratch = Path(directory)
        archive = scratch / "caddy.tar.gz"
        url = f"https://github.com/caddyserver/caddy/releases/download/v{VERSION}/caddy_{VERSION}_linux_amd64.tar.gz"
        urllib.request.urlretrieve(url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise SystemExit("Official archive SHA-256 mismatch")
        with tarfile.open(archive) as tf:
            member = tf.getmember("caddy")
            if not member.isfile():
                raise SystemExit("Unexpected archive entry")
            binary.parent.mkdir(mode=0o755, exist_ok=True)
            binary.write_bytes(tf.extractfile(member).read())
        binary.chmod(0o755)
        binary.parent.chmod(0o755)
        # Validate with private temporary credentials before any public listener exists.
        (scratch / "tls.crt").write_bytes(cert.read_bytes())
        (scratch / "tls.key").write_bytes(key.read_bytes())
        content = """{
    admin off
    auto_https off
    persist_config off
    servers {
        protocols h1 h2
        timeouts {
            read_header 5s
            read_body 5s
            write 10s
            idle 30s
        }
        max_header_size 16KB
    }
}
https://HOSTNAME:18443 {
    tls {$CREDENTIALS_DIRECTORY}/tls.crt {$CREDENTIALS_DIRECTORY}/tls.key
    @sample {
        remote_ip SOURCEIP 127.0.0.1 ::1
        method POST
        path /telemetry/iran
    }
    handle @sample {
        request_body {
            max_size 16KB
        }
        reverse_proxy 127.0.0.1:9081 {
            transport http {
                dial_timeout 2s
                response_header_timeout 5s
            }
        }
    }
    handle {
        respond 404
    }
}
""".replace("HOSTNAME", args.hostname).replace("SOURCEIP", source)
        staged = scratch / "Caddyfile"
        staged.write_text(content)
        subprocess.run([str(binary), "validate", "--config", str(staged), "--adapter", "caddyfile"],
                       env={**os.environ, "CREDENTIALS_DIRECTORY": str(scratch)}, check=True)
        config.write_text(content)
        config.chmod(0o600)
    unit.write_text(f"""[Unit]
Description=SysMonitor restricted telemetry TLS ingress
After=network-online.target sysmonitor.service
Wants=network-online.target

[Service]
DynamicUser=true
StateDirectory=sysmonitor-ingress
Environment=XDG_DATA_HOME=/var/lib/sysmonitor-ingress
Environment=XDG_CONFIG_HOME=/var/lib/sysmonitor-ingress
LoadCredential=tls.crt:{cert}
LoadCredential=tls.key:{key}
LoadCredential=Caddyfile:/etc/sysmonitor/Caddyfile
ExecStart={binary} run --config %d/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=128M
CPUQuota=20%
TasksMax=64

[Install]
WantedBy=multi-user.target
""")
    unit.chmod(0o644)
    print("INGRESS_STAGED_NOT_STARTED; no firewall rule or existing listener changed")
    print("Activation: systemctl daemon-reload && systemctl enable --now sysmonitor-ingress.service")
    print("Rollback: systemctl disable --now sysmonitor-ingress.service")
    print("Existing certificate renewal remains external; restart only this ingress after renewal.")


if __name__ == "__main__":
    main()
