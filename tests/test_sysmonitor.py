import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sysmonitor.app import Application, Config
from sysmonitor.core import Store, parse_metrics, resources_text, status_text, validate_telemetry

EVENT = {"monitor": {"id": 1, "name": "HTTPS <prod>"},
         "heartbeat": {"status": 0, "time": "2026-10-21 09:30:00", "msg": "secret URL ignored"}}
METRIC = {"cpu_percent": 20, "memory_percent": 40, "disk_percent": 83, "uptime_seconds": 3600,
          "load1": 0.2, "cores": 6, "services": {"httpd.service": "active"}}


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.fail = False

    def call(self, method, payload, timeout=15):
        self.calls.append((method, payload))
        if self.fail:
            raise RuntimeError("failure with secret URL must never be logged")
        return [] if method == "getUpdates" else {"message_id": 1}


class TestSysMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = SimpleNamespace(db=Path(self.tmp.name)/"state.db", owner=12345,
            bot_token="fake", webhook_token="w"*40, telemetry_token="t"*40,
            kuma_key="fake", kuma_url="http://127.0.0.1:3001", host="127.0.0.1", port=0)
        self.tg = FakeTelegram()
        self.app = Application(self.cfg, self.tg)

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table):
        with self.app.store.db() as db:
            return db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def update(self, uid=1, owner=12345, chat_type="private", text="/status"):
        return {"update_id": uid, "message": {"from": {"id": owner},
            "chat": {"id": owner, "type": chat_type}, "text": text}}

    def test_event_is_atomic_and_duplicate_safe_across_restart(self):
        self.assertTrue(self.app.store.event(EVENT))
        self.assertFalse(Store(self.cfg.db).event(EVENT))
        self.assertEqual(self.count("incidents"), 1)
        self.assertEqual(self.count("outbox"), 1)
        self.app.deliver_once()
        text = self.tg.calls[-1][1]["text"]
        self.assertIn("&lt;prod&gt;", text)
        self.assertNotIn("secret URL", text)
        self.assertEqual(self.count("outbox"), 0)

    def test_delivery_failure_persists_and_retries_after_restart(self):
        self.app.store.event(EVENT)
        self.tg.fail = True
        self.app.deliver_once()
        self.assertEqual(self.count("outbox"), 1)
        with self.app.store.db() as db:
            self.assertEqual(db.execute("SELECT attempts FROM outbox").fetchone()[0], 1)
            db.execute("UPDATE outbox SET next_try=0")
        tg = FakeTelegram()
        Application(self.cfg, tg).deliver_once()
        self.assertEqual(self.count("outbox"), 0)
        self.assertEqual(tg.calls[0][0], "sendMessage")

    def test_certificate_notification_without_heartbeat_is_accepted(self):
        self.app.store.event({"monitor": {"id": 1, "name": "TLS"}, "heartbeat": None, "msg": "private url"})
        self.app.deliver_once()
        self.assertNotIn("private url", self.tg.calls[-1][1]["text"])
        self.assertIn("TLS", self.tg.calls[-1][1]["text"])

    def test_outbox_preserves_down_before_recovery(self):
        self.app.store.event(EVENT)
        up = copy.deepcopy(EVENT)
        up["heartbeat"].update(status=1, time="2026-10-21 09:35:00")
        self.app.store.event(up)
        with self.app.store.db() as db:
            db.execute("UPDATE outbox SET next_try=? WHERE id=1", (time.time()+100,))
        self.app.deliver_once()
        self.assertEqual(self.tg.calls, [])

    def test_owner_allowlist_private_chat_and_update_replay(self):
        self.app.handle_update(self.update(1, owner=999))
        self.assertEqual(self.count("outbox"), 0)
        self.app.handle_update(self.update(2, chat_type="group"))
        self.assertEqual(self.count("outbox"), 0)
        self.app.handle_update(self.update(3))
        self.assertEqual(self.count("outbox"), 1)
        self.app.rate = float("-inf")
        self.app.handle_update(self.update(3))
        self.assertEqual(self.count("outbox"), 1)
        self.assertEqual(self.app.store.get("offset"), 4)

    def test_callback_allowlist(self):
        event = {"update_id": 2, "callback_query": {"id": "cb1", "data": "resources",
            "from": {"id": 12345}, "message": {"chat": {"id": 12345, "type": "private"}}}}
        self.app.handle_update(event)
        self.assertEqual(self.count("outbox"), 1)
        self.assertEqual(self.tg.calls[0][0], "answerCallbackQuery")

    def test_metrics_parsing_escaped_labels_and_units(self):
        raw = '\n'.join([
            'monitor_status{monitor_id="1",monitor_name="A \\"quoted\\" name"} 1',
            'monitor_response_time{monitor_id="1",monitor_name="A"} 42.1',
            'monitor_uptime_ratio{monitor_id="1",window="24h"} 0.999',
            'monitor_status{monitor_id="2",monitor_name="B"} NaN'])
        result = parse_metrics(raw)
        self.assertEqual(result["1"]["status"], 1)
        self.assertEqual(result["1"]["response_time"], 42.1)
        self.assertEqual(result["1"]["uptime_ratio_24h"], 0.999)
        self.assertNotIn("2", result)

    def test_stale_data_is_explicit(self):
        self.assertIn("تأیید نشده", status_text({"time": time.time()-300, "monitors": {}}))
        self.assertIn("قدیمی", resources_text({"time": time.time()-300, "data": METRIC}))

    def test_telemetry_rejects_extra_fields_nan_boolean_and_bad_unit(self):
        self.assertEqual(validate_telemetry(METRIC), METRIC)
        for extra in ({"hostname": "private"}, {"cpu_percent": float("nan")},
                      {"memory_percent": True}, {"disk_percent": 101},
                      {"services": {"a; reboot": "active"}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_telemetry({**METRIC, **extra})

    def test_resource_threshold_debounce_and_hysteresis(self):
        high = {**METRIC, "disk_percent": 86}
        self.app.accept_telemetry(high)
        self.app.accept_telemetry(high)
        self.assertEqual(self.count("outbox"), 0)
        self.app.accept_telemetry(high)
        self.assertEqual(self.count("outbox"), 1)
        self.app.accept_telemetry(high)
        self.app.accept_telemetry({**METRIC, "disk_percent": 82})
        self.assertEqual(self.count("outbox"), 1)
        self.app.accept_telemetry({**METRIC, "disk_percent": 79})
        self.assertEqual(self.count("outbox"), 2)

    def test_config_rejects_public_receiver_and_remote_kuma(self):
        env = {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_OWNER_ID": "12345",
            "KUMA_WEBHOOK_TOKEN": "w"*40, "TELEMETRY_TOKEN": "t"*40, "KUMA_API_KEY": "fake"}
        self.assertEqual(Config(env).host, "127.0.0.1")
        for changes in ({"LISTEN_HOST": "0.0.0.0"}, {"KUMA_URL": "http://example.com"},
                        {"TELEMETRY_TOKEN": "w"*40}, {"KUMA_API_KEY": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config({**env, **changes})

    def test_webhook_auth_payload_limits_and_persistence(self):
        server = HTTPServer(("127.0.0.1", 0), self.app.handler())
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        def post(path, value, token):
            request = urllib.request.Request(base+path, data=json.dumps(value).encode(),
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        try:
            self.assertEqual(post("/kuma", EVENT, "wrong"), 401)
            self.assertEqual(post("/kuma", EVENT, "w"*40), 202)
            self.assertEqual(post("/kuma", EVENT, "t"*40), 401)
            self.assertEqual(post("/telemetry/iran", METRIC, "t"*40), 202)
            self.assertEqual(post("/telemetry/iran", {"bad": 1}, "t"*40), 400)
            self.assertEqual(post("/kuma", {"msg": "x"*17000}, "w"*40), 413)
            self.assertEqual(self.count("incidents"), 1)
            self.assertEqual(self.app.store.get("telemetry")["data"]["disk_percent"], 83)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
