"""Minimal read-only Linux telemetry. No customer data, process list or public IP."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

UNITS = ("httpd.service", "mariadb.service", "sshd.service", "lfd.service", "imunify360.service")


def cpu_ticks():
    nums = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    return sum(nums), nums[3] + nums[4]


def process_sample():
    """Bounded numeric /proc sample; never read cmdline, environ, user or paths."""
    groups = (("php", "PHP"), ("httpd", "Apache"), ("apache", "Apache"),
              ("mariadb", "Database"), ("mysqld", "Database"), ("clam", "Security"),
              ("imunify", "Security"), ("lfd", "Security"), ("cpsrvd", "cPanel"),
              ("cpanel", "cPanel"), ("sshd", "SSH"), ("backup", "Backup"))
    result = {}
    for entry in list(Path("/proc").iterdir())[:8192]:
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "stat").read_text()[:4096]
            name = raw.split("(", 1)[1].rsplit(")", 1)[0].lower()
            fields = raw.rsplit(") ", 1)[1].split()
            group = next((g for needle, g in groups if needle in name), "Other")
            result[entry.name] = (group, int(fields[11]) + int(fields[12]), int(fields[21]))
        except (OSError, ValueError, IndexError):
            continue  # Process ended, or kernel denied this unprivileged sample.
    return result


def contributors(first, second, elapsed, memory_kb):
    totals = {}
    cores, hz, page = os.cpu_count() or 1, os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    for pid, (group, ticks, rss) in second.items():
        row = totals.setdefault(group, {"group": group, "cpu_percent": 0, "memory_percent": 0})
        before = first.get(pid)
        if before and before[0] == group:
            row["cpu_percent"] += max(0, ticks-before[1]) / hz / max(elapsed, 0.1) / cores * 100
        row["memory_percent"] += max(0, rss) * page / (memory_kb * 1024) * 100
    for row in totals.values():
        for field in ("cpu_percent", "memory_percent"):
            row[field] = round(min(100, row[field]), 2)
    return sorted(totals.values(), key=lambda r: r["cpu_percent"] + r["memory_percent"], reverse=True)[:6]


def collect():
    first, idle_first = cpu_ticks()
    processes_first = process_sample()
    started = time.monotonic()
    time.sleep(1)
    processes_second = process_sample()
    elapsed = time.monotonic() - started
    second, idle_second = cpu_ticks()
    cpu = 100 * (1 - (idle_second - idle_first) / max(1, second - first))
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        mem[key] = int(value.strip().split()[0])
    usage = shutil.disk_usage("/")
    services = {}
    for unit in UNITS:
        try:
            result = subprocess.run(["/usr/bin/systemctl", "is-active", unit],
                                    capture_output=True, text=True, timeout=2, check=False)
            state = result.stdout.strip()
            services[unit] = state if state in ("active", "inactive", "failed", "activating") else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            services[unit] = "unknown"
    return {
        "cpu_percent": round(max(0, min(100, cpu)), 2),
        "memory_percent": round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 2),
        "disk_percent": round(100 * usage.used / usage.total, 2),
        "swap_percent": round(100 * (1 - mem["SwapFree"] / mem["SwapTotal"]), 2) if mem["SwapTotal"] else 0,
        "uptime_seconds": float(Path("/proc/uptime").read_text().split()[0]),
        "load1": os.getloadavg()[0], "cores": os.cpu_count() or 1, "services": services,
        "contributors": contributors(processes_first, processes_second, elapsed, mem["MemTotal"])}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A redirect must never forward a telemetry bearer credential.


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = os.environ.get("TELEMETRY_URL", "")
    token = os.environ.get("TELEMETRY_TOKEN", "")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query
            or parsed.fragment or parsed.path != "/telemetry/iran" or len(token) < 32):
        raise SystemExit("A direct HTTPS /telemetry/iran URL and 32+ character token are required")
    payload = json.dumps(collect()).encode()
    request = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
            if response.status != 202:
                raise RuntimeError("Unexpected status")
    except Exception:
        # HTTP errors include URL; URL could contain private infrastructure details.
        logging.error("Telemetry delivery failed; next timer run will retry with a fresh sample")
        raise SystemExit(1)
    logging.info("Telemetry delivered")


if __name__ == "__main__":
    main()
