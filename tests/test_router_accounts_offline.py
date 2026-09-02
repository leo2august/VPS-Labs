import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]

# Load lab_router_accounts + lab_quota as modules
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ra = _load("lab_router_accounts", ROOT / "lab_router_accounts.py")


class RouterAccountsOfflineTests(unittest.TestCase):
    def test_update_account_falls_back_to_db(self):
        """When 9router API is down, update_account writes isActive to the DB."""
        with patch.object(ra, "_call", side_effect=ValueError("9router tidak aktif")):
            with patch.object(ra, "_db_set_active", return_value={"ok": True, "mode": "db", "updated": 1}) as db:
                result = ra.update_account("abc123", True)
        db.assert_called_once()
        self.assertEqual(result["mode"], "db")
        self.assertTrue(result["ok"])

    def test_update_provider_accounts_falls_back_to_db(self):
        """Offline bulk toggle: read provider ids from DB then update them."""
        fake_ids = [("id1",), ("id2",)]
        with patch.object(ra, "_call", side_effect=ValueError("9router tidak aktif")), \
             patch.object(ra, "sqlite3") as sql, \
             patch.object(ra, "_db_set_active", return_value={"ok": True, "mode": "db", "updated": 2}) as db:
            con = sql.Mock()
            sql.connect.return_value = con
            con.execute.return_value.fetchall.return_value = fake_ids
            result = ra.update_provider_accounts("kiro", False)
        db.assert_called_once()
        self.assertEqual(result["mode"], "db")
        self.assertEqual(result["provider"], "kiro")
        self.assertFalse(result["enabled"])

    def test_update_provider_unknown_provider(self):
        """Unknown provider in offline mode raises."""
        with patch.object(ra, "_call", side_effect=ValueError("9router tidak aktif")), \
             patch.object(ra, "sqlite3") as sql:
            con = sql.Mock()
            sql.connect.return_value = con
            con.execute.return_value.fetchall.return_value = []
            with self.assertRaises(ValueError):
                ra.update_provider_accounts("tidak-ada", True)

    def test_base_url_resolution(self):
        """base URL from providerSpecificData wins; defaults used otherwise."""
        raw = {"provider": "deepseek", "providerSpecificData": {"baseUrl": "https://custom.example/v1"}}
        self.assertEqual(ra._base_url(raw), "https://custom.example/v1")
        self.assertEqual(ra._base_url({"provider": "deepseek"}), "https://api.deepseek.com/v1")
        self.assertEqual(ra._base_url({"provider": "groq"}), "https://api.groq.com/openai/v1")
        self.assertIsNone(ra._base_url({"provider": "kiro"}))

    def test_first_model_prefers_default(self):
        """defaultModel preferred over modelLock_ entries."""
        raw = {"defaultModel": "m-default", "modelLock_a": True, "modelLock_b": True}
        self.assertEqual(ra._first_model(raw), "m-default")
        self.assertEqual(ra._first_model({"modelLock_claude-sonnet": True}), "claude-sonnet")
        self.assertEqual(ra._first_model({}), "")

    def test_offline_test_account_valid(self):
        """Offline test returns valid + latency when provider responds 200."""
        raw = {"provider": "deepseek", "apiKey": "sk-test", "defaultModel": "deepseek-chat"}
        with patch.object(ra, "_account_data", return_value=raw), \
             patch.object(ra, "_offline_http", return_value=(200, b'{"choices":[{"text":"ok"}]}')):
            result = ra.test_account("abc")
        self.assertEqual(result["mode"], "db")
        self.assertTrue(result["result"]["valid"])
        self.assertIn("latency_ms", result["result"])

    def test_offline_test_account_invalid(self):
        """Offline test reports invalid on HTTP error."""
        raw = {"provider": "deepseek", "apiKey": "sk-test", "defaultModel": "deepseek-chat"}
        with patch.object(ra, "_account_data", return_value=raw), \
             patch.object(ra, "_offline_http", return_value=(401, b'{"error":"bad key"}')):
            result = ra.test_account("abc")
        self.assertFalse(result["result"]["valid"])
        self.assertEqual(result["result"]["error"], "HTTP 401")

    def test_offline_account_models_http(self):
        """Offline models: HTTP /models parsed into list."""
        raw = {"provider": "deepseek", "apiKey": "sk-test"}
        body = b'{"data":[{"id":"deepseek-chat"},{"id":"deepseek-reasoner"}]}'
        with patch.object(ra, "_account_data", return_value=raw), \
             patch.object(ra, "_offline_http", return_value=(200, body)):
            result = ra.account_models("abc")
        self.assertEqual(result["models"], ["deepseek-chat", "deepseek-reasoner"])

    def test_offline_account_models_fallback_locked(self):
        """Offline models falls back to modelLock_ keys when HTTP fails."""
        raw = {"provider": "kiro", "accessToken": "tok", "modelLock_z": True, "modelLock_a": True}
        with patch.object(ra, "_account_data", return_value=raw), \
             patch.object(ra, "_offline_http", return_value=(403, b'{"error":"no"}')):
            result = ra.account_models("abc")
        self.assertEqual(result["models"], ["a", "z"])

    def test_offline_delete_writes_db(self):
        """Offline delete removes row from SQLite."""
        con = MagicMock()
        with patch.object(ra, "_call", side_effect=ValueError("9router tidak aktif")), \
             patch.object(ra, "connect_write", return_value=con):
            result = ra.delete_account("abc")
        con.execute.assert_any_call("BEGIN IMMEDIATE")
        con.execute.assert_any_call("DELETE FROM providerConnections WHERE id=?", ("abc",))
        con.commit.assert_called_once()
        self.assertEqual(result["mode"], "db")


if __name__ == "__main__":
    unittest.main(verbosity=2)
