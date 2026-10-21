"""Persistence, validation and formatting. No shell or Telegram side effects."""
import contextlib
import hashlib
import html
import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone

STATUS = {0: ("🔴", "قطع"), 1: ("🟢", "در دسترس"),
          2: ("🟡", "در حال بررسی"), 3: ("🔧", "تعمیرات")}
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def clean(value, limit=120):
    return html.escape(str(value)[:limit])


def utcstamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_metrics(text):
    """Parse only Kuma's documented metrics; never evaluate label content."""
    result = {}
    names = {"monitor_status", "monitor_response_time", "monitor_uptime_ratio",
             "monitor_cert_days_remaining"}
    for line in text.splitlines():
        match = re.fullmatch(r'(\w+)\{(.*)\}\s+([^ ]+)(?:\s+\d+)?', line)
        if not match or match[1] not in names:
            continue
        try:
            value = float(match[3])
            labels = {k: json.loads('"' + v + '"') for k, v in LABEL.findall(match[2])}
        except (ValueError, json.JSONDecodeError):
            continue
        mid = labels.get("monitor_id")
        if not mid or not math.isfinite(value):
            continue
        row = result.setdefault(mid, {"id": mid, "name": labels.get("monitor_name", mid)})
        key = match[1].removeprefix("monitor_")
        if key == "uptime_ratio":
            key += "_" + labels.get("window", "unknown")
        row[key] = value
    return result


def validate_telemetry(payload):
    allowed = {"cpu_percent", "memory_percent", "disk_percent", "swap_percent",
               "uptime_seconds", "load1", "cores", "services", "contributors"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Unexpected telemetry fields")
    out = {}
    for key, value in payload.items():
        if key == "contributors":
            if not isinstance(value, list) or len(value) > 6:
                raise ValueError("Invalid contributors")
            for row in value:
                if (not isinstance(row, dict) or set(row) != {"group", "cpu_percent", "memory_percent"}
                        or row["group"] not in PROCESS_GROUPS):
                    raise ValueError("Invalid contributor group")
                for field in ("cpu_percent", "memory_percent"):
                    n = row[field]
                    if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 100:
                        raise ValueError("Invalid contributor metric")
            out[key] = value
        elif key == "services":
            if not isinstance(value, dict) or len(value) > 20:
                raise ValueError("Invalid services")
            if any(not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", k)
                   or v not in ("active", "inactive", "failed", "unknown", "activating")
                   for k, v in value.items()):
                raise ValueError("Invalid service status")
            out[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Invalid metric")
            if value < 0 or (key.endswith("percent") and value > 100):
                raise ValueError("Metric out of range")
            out[key] = value
    if not {"cpu_percent", "memory_percent", "disk_percent", "uptime_seconds"} <= out.keys():
        raise ValueError("Missing required metrics")
    return out


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                monitor TEXT NOT NULL, status INTEGER NOT NULL, stamp REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, text TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_try REAL NOT NULL DEFAULT 0, created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS resource_daily (
                day TEXT PRIMARY KEY, samples INTEGER NOT NULL,
                cpu_sum REAL NOT NULL, cpu_max REAL NOT NULL,
                ram_sum REAL NOT NULL, ram_max REAL NOT NULL, disk_max REAL NOT NULL);
            ''')

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def enqueue(self, text):
        with self.db() as db:
            self._enqueue(db, text)

    @staticmethod
    def _enqueue(db, text):
        if db.execute("SELECT count(*) FROM outbox").fetchone()[0] >= 2000:
            raise RuntimeError("Outbox full; investigate delivery")
        db.execute("INSERT INTO outbox(text,created) VALUES (?,?)", (text, time.time()))

    def event(self, payload):
        heartbeat, monitor = payload.get("heartbeat"), payload.get("monitor")
        if heartbeat is None and monitor is None:
            self.enqueue("🛰 <b>SysMonitor · تست اتصال</b>\nمسیر ارسال هشدار متصل است؛ این پیام قطعی واقعی نیست.")
            return True
        if heartbeat is None and isinstance(monitor, dict):
            # Certificate expiry and other non-heartbeat notifications use this shape.
            # Do not forward raw diagnostic messages: they may contain private URLs.
            self.enqueue("🟠 <b>اعلان Uptime Kuma</b>\n" + clean(monitor.get("name", "Service"))
                         + "\nاعلان گواهی یا وضعیت تکمیلی دریافت شد؛ جزئیات را در داشبورد بررسی کن.")
            return True
        if not isinstance(heartbeat, dict) or not isinstance(monitor, dict):
            raise ValueError("Invalid Kuma event")
        status = heartbeat.get("status")
        if type(status) is not int or status not in STATUS or not monitor.get("id") or not heartbeat.get("time"):
            raise ValueError("Invalid heartbeat")
        mid, name = str(monitor["id"]), str(monitor.get("name", "Service"))[:120]
        canonical = json.dumps([mid, status, heartbeat["time"]], ensure_ascii=False)
        event_key = hashlib.sha256(canonical.encode()).hexdigest()
        icon, label = STATUS[status]
        text = f"{icon} <b>SysMonitor · {label}</b>\n\n<b>{clean(name)}</b>\n"
        text += "مبدأ سنجش: ترکیه\n" + clean(heartbeat["time"], 50)
        text += "\n\n" + ("اتصال دوباره برقرار شد. ✅" if status == 1 else
                           "دسترسی از مبدأ مانیتور برقرار نیست؛ علت هنوز تأیید نشده است." if status == 0
                           else "وضعیت در داشبورد قابل پیگیری است.")
        with self.db() as db:
            count = db.execute("INSERT OR IGNORE INTO incidents(event_key,monitor,status,stamp) VALUES (?,?,?,?)",
                               (event_key, name, status, time.time())).rowcount
            if count:
                self._enqueue(db, text)
            return bool(count)

    def recent(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT monitor,status,stamp FROM incidents ORDER BY id DESC LIMIT 8")]

    def prune(self):
        with self.db() as db:
            db.execute("DELETE FROM incidents WHERE stamp < ?", (time.time() - 90*86400,))


def status_text(snapshot, max_age=120):
    if not snapshot or time.time() - snapshot.get("time", 0) > max_age:
        return "⚪ <b>داده تازه ندارم</b>\nاتصال به Uptime Kuma یا سرویس مانیتور را بررسی کن. وضعیت سرور تأیید نشده است."
    rows = snapshot.get("monitors", {})
    lines = ["🛰 <b>SysMonitor · وضعیت سرویس‌ها</b>", "مبدأ سنجش: ترکیه", ""]
    for row in list(rows.values())[:30]:
        icon, label = STATUS.get(row.get("status"), ("⚪", "نامشخص"))
        ms = row.get("response_time")
        suffix = f" · زمان پاسخ: <code>{ms:.0f} ms</code>" if ms is not None and row.get("status") == 1 else ""
        lines.append(f"{icon} <b>{clean(row['name'], 70)}</b>\n{label}{suffix}\n")
    if not rows:
        lines.append("هنوز مانیتوری با داده قابل‌خواندن ثبت نشده است.")
    lines += ["", "آخرین دریافت: <code>" + utcstamp(snapshot["time"]) + "</code>"]
    return "\n".join(lines)


def resources_text(snapshot, max_age=180):
    if not snapshot:
        return "⚪ داده منابع هنوز متصل نشده است؛ از روی آپتایم نمی‌توان CPU یا RAM را حدس زد."
    age = time.time() - snapshot["time"]
    data = snapshot["data"]
    lines = ["📊 <b>منابع سرور ایران</b>"]
    if age > max_age:
        lines += ["⚠️ داده قدیمی است؛ این اعداد وضعیت فعلی را تأیید نمی‌کنند."]
    for key, label in [("cpu_percent", "CPU"), ("memory_percent", "RAM"),
                       ("disk_percent", "دیسک ریشه"), ("swap_percent", "Swap")]:
        if key in data:
            lines.append(f"{label}: <b>{data[key]:.1f}%</b>")
    if "load1" in data:
        lines.append(f"Load 1m: {data['load1']:.2f} · cores: {data.get('cores', '?')}")
    lines.append(f"Uptime: {data['uptime_seconds']/86400:.1f} days")
    for unit, value in data.get("services", {}).items():
        lines.append(f"{'🟢' if value == 'active' else '🟠'} {clean(unit)}: {clean(value)}")
    if data.get("contributors"):
        lines += ["", contributors_text(data)]
    lines += ["", "آخرین دریافت: <code>" + utcstamp(snapshot["time"]) + "</code>"]
    return "\n".join(lines)


PROCESS_GROUPS = frozenset(("PHP", "Apache", "Database", "Security", "cPanel", "SSH", "Backup", "Other"))


def contributors_text(data):
    rows = sorted(data.get("contributors", []), key=lambda r: r["cpu_percent"] + r["memory_percent"], reverse=True)[:4]
    if not rows:
        return "سهم سرویس‌ها در این نمونه در دسترس نیست."
    lines = ["بیشترین سهم مشاهده‌شده در همین نمونه:"]
    for row in rows:
        lines.append(f"• <b>{row['group']}</b> · CPU <code>{row['cpu_percent']:.1f}%</code> · RAM <code>{row['memory_percent']:.1f}%</code>")
    lines.append("این سهم مصرف است، نه اثبات علت؛ حافظه اشتراکی ممکن است چندبار شمرده شود.")
    return "\n".join(lines)
