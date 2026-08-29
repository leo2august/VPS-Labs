import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
