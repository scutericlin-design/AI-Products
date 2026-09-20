import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import fcntl

from dashboard.stock_alpha_data import build_stock_alpha_payload
from dashboard.server import DashboardHandler
from stock_alpha.paper import Ledger


class StockAlphaDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def ledger(self):
        ledger = Ledger(self.root / "paper.sqlite")
        ledger.initialize()
        (self.root / "worker.lock").touch()
        return ledger

    def test_missing_database_never_creates_account(self):
        self.assertEqual(build_stock_alpha_payload(self.root)["status"], "unavailable")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_read_only_balances_and_no_secret_disclosure(self):
        ledger = self.ledger()
        ledger.set_state("last_ai", {"mode":"ai_reviewed", "model":"deepseek-v4-flash", "api_key":"SECRET_SENTINEL", "attempts":[]})
        digest = hashlib.sha256(ledger.path.read_bytes()).hexdigest()
        payload = build_stock_alpha_payload(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["read_only"])
        self.assertEqual(set(payload["accounts"]), {"A_baseline","B_enhanced","C_ai"})
        self.assertEqual(payload["accounts"]["C_ai"]["equity"], 1000000)
        self.assertTrue(payload["worker_stale"])
        self.assertNotIn("SECRET_SENTINEL", json.dumps(payload))
        self.assertEqual(hashlib.sha256(ledger.path.read_bytes()).hexdigest(), digest)

    def test_governance_state_is_projected_read_only(self):
        ledger = self.ledger()
        ledger.set_state("governance", {"automatic_promotion":False,"status":"collecting_evidence"})
        data = build_stock_alpha_payload(self.root)
        self.assertFalse(data["governance"]["automatic_promotion"])

    def test_account_partition_and_live_wal(self):
        ledger = self.ledger()
        with ledger.connect() as db:
            db.execute("UPDATE accounts SET cash=990000 WHERE id='C_ai'")
            db.commit()
            result = build_stock_alpha_payload(self.root)
            self.assertEqual(result["accounts"]["C_ai"]["cash"], 990000)
            self.assertEqual(result["accounts"]["A_baseline"]["cash"], 1000000)

    def test_corrupt_database_is_not_reset(self):
        path = self.root / "paper.sqlite"
        path.write_text("not a database")
        self.assertEqual(build_stock_alpha_payload(self.root)["status"], "unavailable")
        self.assertEqual(path.read_text(), "not a database")

    def test_missing_required_account_fails_closed(self):
        ledger = self.ledger()
        with ledger.connect() as db:
            db.execute("DELETE FROM accounts WHERE id='A_baseline'")
        self.assertEqual(build_stock_alpha_payload(self.root)["accounts"], {})

    def test_new_routes_require_existing_auth(self):
        for path in ("/stock-alpha", "/api/stock-alpha-dashboard"):
            handler = DashboardHandler.__new__(DashboardHandler)
            handler._normalized_path = Mock(return_value=path)
            handler._require_auth = Mock(return_value=False)
            handler._send_file = Mock()
            with patch("dashboard.server.build_stock_alpha_payload") as build:
                handler.do_GET()
                handler._require_auth.assert_called_once_with(path)
                build.assert_not_called()
                handler._send_file.assert_not_called()

    def test_authenticated_api_uses_only_isolated_reader(self):
        handler = DashboardHandler.__new__(DashboardHandler)
        handler._normalized_path = Mock(return_value="/api/stock-alpha-dashboard")
        handler._require_auth = Mock(return_value=True)
        handler._send_json = Mock()
        with patch("dashboard.server.build_stock_alpha_payload", return_value={"read_only":True}) as reader, patch("dashboard.server.build_dashboard_payload") as old:
            handler.do_GET()
            reader.assert_called_once_with()
            old.assert_not_called()

    def test_empty_cycles_have_no_fabricated_curve(self):
        ledger = self.ledger()
        data = build_stock_alpha_payload(self.root)
        for account in data["accounts"].values():
            self.assertEqual(account["curve"], [])
            self.assertEqual(account["recent_trades"], [])

    def test_busy_worker_is_never_read_mid_write(self):
        self.ledger()
        with (self.root / "worker.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(build_stock_alpha_payload(self.root)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
