import tempfile
import unittest
from pathlib import Path

import lab_security
import app


class NotificationLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "notifications.db"
        self.alerts = [{
            "key": "service:caddy",
            "level": "warn",
            "icon": "🔄",
            "title": "caddy mati",
            "detail": "Service caddy tidak berjalan.",
        }]

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_deduplicates_and_preserves_history(self):
        first = lab_security.notification_log(self.alerts, self.db)
        second = lab_security.notification_log(self.alerts, self.db)
        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 1)
        self.assertEqual(second["unread"], 1)
        self.assertTrue(second["notifications"][0]["active"])

        resolved = lab_security.notification_log([], self.db)
        self.assertEqual(resolved["total"], 1)
        self.assertFalse(resolved["notifications"][0]["active"])

    def test_dynamic_alert_value_keeps_stable_key(self):
        original = lab_security.urgent_alerts
        try:
            lab_security.urgent_alerts = lambda: {"alerts": [], "count": 0, "urgent": 0}
            a = [{"level": "danger", "icon": "🔐", "title": "29 gagal SSH/jam", "detail": "Ada percobaan login SSH gagal berulang."}]
            b = [{"level": "danger", "icon": "🔐", "title": "31 gagal SSH/jam", "detail": "Ada percobaan login SSH gagal berulang."}]
            for alerts in (a, b):
                for alert in alerts:
                    stable = __import__('re').sub(r"\d+(?:\.\d+)?", "#", alert["title"])
                    alert["key"] = __import__('hashlib').sha256("|".join((alert["level"], stable, alert["detail"])).encode()).hexdigest()[:20]
                lab_security.notification_log(alerts, self.db)
            self.assertEqual(lab_security.notification_log(None, self.db)["total"], 1)
            self.assertEqual(lab_security.notification_log(None, self.db)["notifications"][0]["title"], "31 gagal SSH/jam")
        finally:
            lab_security.urgent_alerts = original

    def test_read_and_delete_actions(self):
        item = lab_security.notification_log(self.alerts, self.db)["notifications"][0]
        lab_security.update_notifications("read", self.db, item["id"])
        self.assertEqual(lab_security.notification_log(None, self.db)["unread"], 0)

        lab_security.update_notifications("delete", self.db, item["id"])
        self.assertEqual(lab_security.notification_log(None, self.db)["total"], 0)

    def test_read_all_and_delete_all(self):
        alerts = self.alerts + [{**self.alerts[0], "key": "disk", "title": "Disk penuh"}]
        lab_security.notification_log(alerts, self.db)
        lab_security.update_notifications("read_all", self.db)
        self.assertEqual(lab_security.notification_log(None, self.db)["unread"], 0)
        lab_security.update_notifications("delete_all", self.db)
        self.assertEqual(lab_security.notification_log(None, self.db)["total"], 0)


class NotificationApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = lab_security.NOTIFICATION_DB
        lab_security.NOTIFICATION_DB = Path(self.tmp.name) / "api-notifications.db"
        app.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def tearDown(self):
        lab_security.NOTIFICATION_DB = self.original_db
        self.tmp.cleanup()

    def test_notification_api_lists_and_updates(self):
        response = self.client.get("/api/lab/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertIn("notifications", response.get_json())

        response = self.client.post("/api/lab/notifications", json={"action": "read_all"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
