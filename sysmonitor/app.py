"""Loopback-only authenticated receiver and allowlisted Telegram long polling."""
import base64
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from .core import Store, clean, contributors_text, parse_metrics, resources_text, status_text, utcstamp, validate_telemetry, STATUS

LOG = logging.getLogger("sysmonitor")


def request_json(url, payload=None, headers=None, timeout=15, max_bytes=2_000_000):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    # Never log URLs: Telegram embeds the bot token in its API path.
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Response too large")
    return json.loads(raw)


class Config:
    def __init__(self, env=os.environ):
        self.bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
        self.owner = int(env.get("TELEGRAM_OWNER_ID", "0"))
        self.webhook_token = env.get("KUMA_WEBHOOK_TOKEN", "")
        self.telemetry_token = env.get("TELEMETRY_TOKEN", "")
        self.kuma_key = env.get("KUMA_API_KEY", "")
        self.kuma_url = env.get("KUMA_URL", "http://127.0.0.1:3001").rstrip("/")
        self.host = env.get("LISTEN_HOST", "127.0.0.1")
        self.port = int(env.get("LISTEN_PORT", "9081"))
        self.db = env.get("STATE_DB", "/var/lib/sysmonitor/state.sqlite3")
        if self.owner <= 0 or not self.bot_token or len(self.webhook_token) < 32:
            raise ValueError("Owner, bot token and webhook token (32+ chars) are required")
        if len(self.telemetry_token) < 32 or self.telemetry_token == self.webhook_token:
            raise ValueError("A separate telemetry token (32+ chars) is required")
        url = urllib.parse.urlsplit(self.kuma_url)
        try:
            local = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            local = False
        if url.scheme != "http" or not local or url.username or url.query or url.path:
            raise ValueError("Kuma must use a plain loopback HTTP origin")
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("Receiver must bind to loopback; use an authenticated TLS proxy for telemetry")
        if not self.kuma_key:
            raise ValueError("Read-only Kuma API key required")


class Telegram:
    def __init__(self, token):
        self.base = "https://api.telegram.org/bot" + token + "/"

    def call(self, method, payload, timeout=15):
        data = request_json(self.base + method, payload, timeout=timeout)
        if not data.get("ok"):
            raise RuntimeError("Telegram API rejected request")
        return data["result"]


KEYBOARD = {"inline_keyboard": [
    [{"text": "🛰 وضعیت سرویس‌ها", "callback_data": "status"},
     {"text": "📊 منابع ایران", "callback_data": "resources"}],
    [{"text": "⏱ آپتایم", "callback_data": "uptime"},
     {"text": "📋 رخدادها", "callback_data": "incidents"}],
    [{"text": "🧪 تست اعلان", "callback_data": "test"},
     {"text": "راهنما", "callback_data": "help"}],
    [{"text": "گزارش روزانه روشن", "callback_data": "daily_on"},
     {"text": "گزارش روزانه خاموش", "callback_data": "daily_off"}]]}

TEHRAN = ZoneInfo("Asia/Tehran")


class Application:
    def __init__(self, config, telegram=None):
        self.config = config
        Path(config.db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store = Store(config.db)
        self.telegram = telegram or Telegram(config.bot_token)
        self.stop = threading.Event()
        self.ticks = {}
        self.rate = float("-inf")

    def reply(self, command):
        if command in ("daily_on", "daily_off"):
            command = "daily " + command.rsplit("_", 1)[1]
        if command.split(" ")[0] == "daily":
            parts = command.split()
            if parts[1:] in (["on"], ["off"]):
                self.store.set("daily_enabled", parts[1] == "on")
            elif len(parts) == 3 and parts[1] == "time" and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", parts[2]):
                self.store.set("daily_time", parts[2])
            elif len(parts) > 1:
                return "تنظیم معتبر: <code>/daily on</code> یا <code>/daily off</code> یا <code>/daily time 23:00</code>"
            enabled = self.store.get("daily_enabled", True)
            hour = self.store.get("daily_time", "23:00")
            return f"📅 گزارش روزانه {'روشن' if enabled else 'خاموش'} است.\nساعت <code>{hour}</code> به وقت تهران.\nهشدارهای قطعی و منابع همیشه فعال‌اند.\n/daily on · /daily off\n<code>/daily time 23:00</code>"
        if command in ("start", "status"):
            return status_text(self.store.get("snapshot"))
        if command == "resources":
            return resources_text(self.store.get("telemetry"))
        if command == "uptime":
            snapshot = self.store.get("snapshot")
            if not snapshot or time.time() - snapshot["time"] > 120:
                return status_text(None)
            lines = ["⏱ <b>آپتایم ثبت‌شده در Kuma</b>"]
            for row in list(snapshot["monitors"].values())[:20]:
                ratios = [f"{window}: {row['uptime_ratio_' + window]*100:.3f}%"
                          for window in ("24h", "30d") if 'uptime_ratio_' + window in row]
                lines.append(clean(row["name"], 45) + " · " + (" | ".join(ratios) or "داده کافی نیست"))
            lines.append("\nاین نسبت، تجربه همه کاربران یا آپتایم قبل از شروع پایش را تضمین نمی‌کند.")
            return "\n".join(lines)
        if command == "incidents":
            lines = ["📋 <b>آخرین رخدادهای دریافت‌شده</b>"]
            for row in self.store.recent():
                icon, label = STATUS[row["status"]]
                lines.append(f"{icon} {clean(row['monitor'], 70)} · {label}\n{utcstamp(row['stamp'])}")
            return "\n\n".join(lines) if len(lines) > 1 else "📋 هنوز رخدادی دریافت نشده است."
        if command == "test":
            return "🧪 <b>تست SysMonitor موفق بود</b>\nدریافت فرمان و مسیر پاسخ تلگرام کار می‌کند. این تست، شبیه‌سازی قطع سرور نیست."
        return ("🛰 <b>SysMonitor | Webline</b>\n\n"
                "/status — وضعیت سرویس‌ها\n/resources — منابع ایران\n/uptime — نسبت آپتایم\n"
                "/incidents — آخرین رخدادها\n/test — تست پاسخ\n/daily — تنظیم گزارش روزانه\n\n"
                "فقط مالک به گزارش‌ها دسترسی دارد. بات دستور اجرایی، ری‌استارت یا دسترسی شل ندارد.\n"
                "قطع از ترکیه ممکن است ناشی از مسیر شبکه باشد؛ علت خرابی سرور نیاز به بررسی دارد.")

    def handle_update(self, update):
        uid = update.get("update_id")
        if type(uid) is not int:
            return
        callback = update.get("callback_query")
        message = callback.get("message", {}) if isinstance(callback, dict) else update.get("message", {})
        sender = callback.get("from", {}) if isinstance(callback, dict) else message.get("from", {})
        chat = message.get("chat", {})
        allowed = (sender.get("id") == self.config.owner and chat.get("id") == self.config.owner
                   and chat.get("type") == "private")
        text = None
        if allowed and time.monotonic() - self.rate >= 1:
            self.rate = time.monotonic()
            if callback:
                cmd = callback.get("data", "help")
            else:
                parts = str(message.get("text", "")).strip().split(maxsplit=1)
                cmd = parts[0].lstrip("/").split("@")[0] if parts else "help"
                if cmd == "daily" and len(parts) > 1:
                    cmd += " " + parts[1].lower()
            text = self.reply(cmd)
        # Persist command reply and offset together. Restart never loses an accepted response.
        with self.store.db() as db:
            row = db.execute("SELECT value FROM state WHERE key='offset'").fetchone()
            if row and uid < json.loads(row[0]):
                return
            if text:
                self.store._enqueue(db, text)
            db.execute("INSERT OR REPLACE INTO state VALUES ('offset',?)", (json.dumps(uid+1),))
        if callback and allowed:
            try:
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
            except Exception:
                LOG.warning("Callback acknowledgement failed")

    def poll_once(self):
        updates = self.telegram.call("getUpdates", {
            "offset": self.store.get("offset", 0), "timeout": 25,
            "allowed_updates": ["message", "callback_query"]}, timeout=35)
        for update in updates:
            self.handle_update(update)

    def deliver_once(self):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM outbox ORDER BY id LIMIT 1").fetchone()
        if not row or row["next_try"] > time.time():
            return
        try:
            self.telegram.call("sendMessage", {"chat_id": self.config.owner, "text": row["text"],
                "parse_mode": "HTML", "protect_content": True,
                "link_preview_options": {"is_disabled": True}, "reply_markup": KEYBOARD})
        except Exception:
            attempts = row["attempts"] + 1
            with self.store.db() as db:
                db.execute("UPDATE outbox SET attempts=?, next_try=? WHERE id=?",
                           (attempts, time.time() + min(300, 2**min(attempts, 9)), row["id"]))
            LOG.warning("Telegram delivery failed; retained for retry")
            return
        with self.store.db() as db:
            db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
        self.store.set("last_delivery", time.time())

    def scrape_once(self):
        auth = base64.b64encode((":" + self.config.kuma_key).encode()).decode()
        req = urllib.request.Request(self.config.kuma_url + "/metrics", headers={"Authorization": "Basic " + auth})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("Metrics too large")
            monitors = parse_metrics(raw.decode())
            self.store.set("snapshot", {"time": time.time(), "monitors": monitors})
            if self.store.get("scrape_failed"):
                self.store.enqueue("🟢 <b>ارتباط با Kuma بازیابی شد</b>\nدریافت وضعیت سرویس‌ها دوباره برقرار است.")
            self.store.set("scrape_failed", False)
        except Exception:
            if not self.store.get("scrape_failed"):
                self.store.enqueue("🟠 <b>دریافت داده از Kuma متوقف شد</b>\nاین هشدار درباره سامانه مانیتور است؛ وضعیت سرور ایران تأیید نشده است.")
            self.store.set("scrape_failed", True)
            LOG.warning("Kuma scrape failed")
        telemetry = self.store.get("telemetry")
        stale = telemetry is not None and time.time() - telemetry["time"] > 180
        if stale and not self.store.get("telemetry_stale"):
            self.store.enqueue("⚪ <b>داده منابع ایران قدیمی شده</b>\nبیش از ۳ دقیقه داده تازه نرسیده است؛ وضعیت سرویس‌ها را جداگانه بررسی کن.")
        self.store.set("telemetry_stale", stale)
        self.store.prune()
        self.daily_once()

    def accept_telemetry(self, payload):
        data = validate_telemetry(payload)
        previous = self.store.get("telemetry")
        gap = previous is not None and time.time() - previous["time"] > 180
        self.store.set("telemetry", {"time": time.time(), "data": data})
        if self.store.get("telemetry_stale"):
            self.store.enqueue("🟢 <b>دریافت منابع ایران بازیابی شد</b>\nداده تازه دوباره در دسترس است.")
            self.store.set("telemetry_stale", False)
        now = time.time()
        day = datetime.fromtimestamp(now, TEHRAN).date().isoformat()
        with self.store.db() as db:
            db.execute("""INSERT INTO resource_daily VALUES (?,1,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET samples=samples+1,
                cpu_sum=cpu_sum+excluded.cpu_sum,cpu_max=max(cpu_max,excluded.cpu_max),
                ram_sum=ram_sum+excluded.ram_sum,ram_max=max(ram_max,excluded.ram_max),
                disk_max=max(disk_max,excluded.disk_max)""",
                (day, data["cpu_percent"], data["cpu_percent"], data["memory_percent"], data["memory_percent"], data["disk_percent"]))
            db.execute("DELETE FROM resource_daily WHERE day < ?", ((datetime.fromtimestamp(now, TEHRAN)-timedelta(days=35)).date().isoformat(),))
        # Wall-clock sustained pressure, not merely a burst of five HTTP requests.
        for key, label in (("cpu_percent", "CPU"), ("memory_percent", "RAM")):
            state = self.store.get("pressure:" + key, {"count": 0, "since": now, "active": False})
            value = data[key]
            if gap or value < 90:
                state.update(count=0, since=now)
            if value >= 90:
                if not state["count"]:
                    state["since"] = now
                state["count"] += 1
            if state["count"] >= 5 and now-state["since"] >= 300 and not state["active"]:
                self.store.enqueue(f"🟠 <b>فشار پایدار {label} در ایران</b>\nمصرف: <code>{value:.1f}%</code>\nحداقل ۵ دقیقه پیوسته بالای ۹۰٪.\n\n" + contributors_text(data))
                state["active"] = True
            if state["active"] and value < 85:
                self.store.enqueue(f"🟢 <b>{label} به محدوده عادی برگشت</b>\nمصرف: <code>{value:.1f}%</code>")
                state["active"] = False
            self.store.set("pressure:" + key, state)
        value = data["disk_percent"]
        old = self.store.get("disk_level", 0)
        level = 3 if value >= 95 else 2 if value >= 90 else 1 if value >= 80 else 0
        # Escalate immediately; require two percentage points of recovery below the old boundary.
        if level < old and value >= (80, 90, 95)[old-1]-2:
            level = old
        if level != old:
            icon, label = (("🟢", "عادی"), ("🟡", "هشدار"), ("🔴", "خطر"), ("🚨", "بحرانی"))[level]
            action = ("فضای آزاد را بررسی و برای افزایش ظرفیت یا پاکسازی بررسی‌شده اقدام کن."
                      if level else "مصرف به زیر محدوده هشدار برگشت.")
            self.store.enqueue(f"{icon} <b>دیسک ایران · {label}</b>\nمصرف ریشه: <code>{value:.1f}%</code>\n{action}\nهیچ فایلی خودکار حذف نمی‌شود.")
            self.store.set("disk_level", level)
        for unit in ("httpd.service", "mariadb.service", "sshd.service", "lfd.service"):
            value = data.get("services", {}).get(unit, "unknown")
            state = self.store.get("service:" + unit, {"count": 0, "since": now, "active": False})
            if gap or value not in ("inactive", "failed"):
                state.update(count=0, since=now)
            if value in ("inactive", "failed"):
                if not state["count"]:
                    state["since"] = now
                state["count"] += 1
                if state["count"] >= 3 and now-state["since"] >= 120 and not state["active"]:
                    self.store.enqueue(f"🔴 <b>سرویس ایران پایدار غیرفعال است</b>\n<code>{unit}</code>\nحداقل دو دقیقه و سه نمونه. ری‌استارت خودکار انجام نمی‌شود.")
                    state["active"] = True
            elif value == "active" and state["active"]:
                self.store.enqueue(f"🟢 <b>سرویس ایران بازیابی شد</b>\n<code>{unit}</code>")
                state["active"] = False
            self.store.set("service:" + unit, state)

    def daily_once(self):
        if not self.store.get("daily_enabled", True):
            return
        now = datetime.fromtimestamp(time.time(), TEHRAN)
        day = now.date().isoformat()
        if now.strftime("%H:%M") < self.store.get("daily_time", "23:00"):
            return
        snapshot = self.store.get("snapshot")
        if snapshot:
            snapshot = {**snapshot, "monitors": dict(list(snapshot["monitors"].items())[:5])}
        telemetry = self.store.get("telemetry")
        if telemetry:
            telemetry = {**telemetry, "data": {k: v for k, v in telemetry["data"].items() if k not in ("services", "contributors")}}
        lines = [f"📅 <b>گزارش روزانه SysMonitor</b>\n<code>{day}</code> · وقت تهران", status_text(snapshot), resources_text(telemetry)]
        if snapshot and time.time()-snapshot["time"] <= 120:
            ratios = [clean(r["name"], 45)+f" · <code>{r['uptime_ratio_24h']*100:.3f}%</code>"
                      for r in snapshot["monitors"].values() if "uptime_ratio_24h" in r]
            if ratios:
                lines.append("آپتایم ثبت‌شده در بازه ۲۴ ساعت:\n"+"\n".join(ratios))
        with self.store.db() as db:
            row = db.execute("SELECT value FROM state WHERE key='daily_sent'").fetchone()
            if row and json.loads(row[0]) == day:
                return
            stats = db.execute("SELECT * FROM resource_daily WHERE day=?", (day,)).fetchone()
            if stats:
                lines.append(f"📈 نمونه‌های امروز: {stats['samples']}\nCPU میانگین/اوج: {stats['cpu_sum']/stats['samples']:.1f}% / {stats['cpu_max']:.1f}%\nRAM میانگین/اوج: {stats['ram_sum']/stats['samples']:.1f}% / {stats['ram_max']:.1f}%\nاوج دیسک: {stats['disk_max']:.1f}%")
            lines.append("این گزارش جمع‌بندی پایش است؛ سالم بودن همه وب‌سایت‌ها و تراکنش‌ها را تضمین نمی‌کند.\nتنظیم گزارش: /daily")
            self.store._enqueue(db, "\n\n".join(lines))
            db.execute("INSERT OR REPLACE INTO state VALUES ('daily_sent',?)", (json.dumps(day),))

    def worker(self, name, action, interval):
        while not self.stop.is_set():
            try:
                action()
            except Exception:
                LOG.error("%s worker failed; will retry", name)
                self.stop.wait(5)
            self.ticks[name] = time.time()
            self.stop.wait(interval)

    def handler(self):
        app = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass  # Do not record credentials, payloads or request paths.

            def respond(self, code, value):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != "/healthz":
                    return self.respond(404, {"ok": False})
                healthy = all(time.time() - app.ticks.get(name, 0) < 100 for name in ("poll", "delivery", "scrape"))
                self.respond(200 if healthy else 503, {"ok": healthy})

            def do_POST(self):
                token = {"/kuma": app.config.webhook_token, "/telemetry/iran": app.config.telemetry_token}.get(self.path)
                if token is None:
                    return self.respond(404, {"ok": False})
                if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                    return self.respond(401, {"ok": False})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 16384:
                        return self.respond(413, {"ok": False})
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("Object required")
                    if self.path == "/kuma":
                        app.store.event(payload)
                    else:
                        app.accept_telemetry(payload)
                    self.respond(202, {"ok": True})
                except (ValueError, TypeError, KeyError):
                    self.respond(400, {"ok": False})
                except Exception:
                    LOG.error("Receiver failed to persist event")
                    self.respond(503, {"ok": False})
        return Handler

    def run(self):
        for name, action, interval in (("poll", self.poll_once, 0.5), ("delivery", self.deliver_once, 1), ("scrape", self.scrape_once, 30)):
            threading.Thread(target=self.worker, args=(name, action, interval), daemon=True).start()
        server = HTTPServer((self.config.host, self.config.port), self.handler())
        server.timeout = 1
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        LOG.info("SysMonitor started; receiver bound to loopback")
        while not self.stop.is_set():
            server.handle_request()
        server.server_close()


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Application(Config()).run()


if __name__ == "__main__":
    main()
