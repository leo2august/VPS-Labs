import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lab_router_accounts", ROOT / "lab_router_accounts.py")
assert spec and spec.loader
accounts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounts)


class ProviderBulkAndGreetingTests(unittest.TestCase):
    def test_bulk_update_only_matching_provider(self):
        calls = []

        def fake_call(method, path, payload=None, timeout=25):
            if method == "GET":
                return {"connections": [
                    {"id": "k1", "provider": "kiro", "isActive": False},
                    {"id": "k2", "provider": "Kiro", "isActive": True},
                    {"id": "g1", "provider": "github", "isActive": True},
                ]}
            calls.append((method, path, payload))
            return {"connection": {"id": path.rsplit("/", 1)[-1]}}

        with patch.object(accounts, "_call", side_effect=fake_call):
            result = accounts.update_provider_accounts("kiro", False)

        self.assertEqual(result, {"ok": True, "provider": "kiro", "enabled": False, "updated": 2, "failed": []})
        self.assertEqual(calls, [
            ("PUT", "/api/providers/k1", {"isActive": False}),
            ("PUT", "/api/providers/k2", {"isActive": False}),
        ])

    def test_bulk_update_rejects_unknown_provider(self):
        with patch.object(accounts, "_call", return_value={"connections": []}):
            with self.assertRaisesRegex(ValueError, "provider tidak ditemukan"):
                accounts.update_provider_accounts("missing", True)

    def test_greeting_uses_labs_brand_not_login_email(self):
        app_source = (ROOT / "app.py").read_text()
        template = (ROOT / "templates" / "index.html").read_text()
        self.assertIn('brand_name=_lab_brand()[0]', app_source)
        self.assertIn('id="greetTitle" data-user="{{ brand_name }}"', template)
        self.assertIn('Halo, {{ brand_name }}.', template)

    def test_provider_group_has_bulk_controls(self):
        template = (ROOT / "templates" / "index.html").read_text()
        self.assertIn("toggleProviderAccounts(this.dataset.provider,true,this)", template)
        self.assertIn("toggleProviderAccounts(this.dataset.provider,false,this)", template)
        self.assertIn("api/lab/router-account/toggle-provider", template)


if __name__ == "__main__":
    unittest.main()
