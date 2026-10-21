#!/usr/bin/env python3
"""First-time secret configuration through a non-echoing TTY. Never prints tokens."""
import base64
import getpass
import json
import os
from pathlib import Path
import secrets
import urllib.request


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the monitoring host")
    os.umask(0o077)
    target = Path("/etc/sysmonitor/bridge.env")
    if target.exists():
        raise SystemExit("Existing configuration found; inspect it instead of overwriting")
    owner = int(input("Verified Telegram owner ID: "))
    token = getpass.getpass("Bot token (hidden): ")
    key = getpass.getpass("Kuma metrics API key (hidden): ")
    if owner <= 0 or not token or not key or any("\n" in v or "\r" in v for v in (token, key)):
        raise SystemExit("Invalid values")
    try:
        with urllib.request.urlopen("https://api.telegram.org/bot"+token+"/getMe", timeout=10) as response:
            identity = json.load(response)
        if not identity.get("ok"):
            raise ValueError()
        authorization = base64.b64encode((":"+key).encode()).decode()
        req = urllib.request.Request("http://127.0.0.1:3001/metrics", headers={"Authorization": "Basic "+authorization})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise ValueError()
    except Exception:
        raise SystemExit("Identity or metrics validation failed; no configuration written")
    config = {"TELEGRAM_OWNER_ID": str(owner), "TELEGRAM_BOT_TOKEN": token,
        "KUMA_API_KEY": key, "KUMA_WEBHOOK_TOKEN": secrets.token_urlsafe(48),
        "TELEMETRY_TOKEN": secrets.token_urlsafe(48), "KUMA_URL": "http://127.0.0.1:3001",
        "LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": "9081", "STATE_DB": "/var/lib/sysmonitor/state.sqlite3"}
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with target.open("x") as output:
        output.write("\n".join(k+"="+v for k,v in config.items())+"\n")
    print("CONFIGURED", identity["result"]["username"])


if __name__ == "__main__":
    main()
