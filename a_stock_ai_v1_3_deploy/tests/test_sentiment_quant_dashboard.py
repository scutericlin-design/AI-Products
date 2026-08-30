import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentiment_quant_dashboard import build_sentiment_quant_dashboard_payload


class SentimentQuantDashboardTests(unittest.TestCase):
    def test_missing_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("A_SHARE_QUANT_ROOT")
            os.environ["A_SHARE_QUANT_ROOT"] = tmp
            try:
                payload = build_sentiment_quant_dashboard_payload()
            finally:
                if old is None:
                    del os.environ["A_SHARE_QUANT_ROOT"]
                else:
                    os.environ["A_SHARE_QUANT_ROOT"] = old
        self.assertEqual(payload["latest_decision"]["state"], "BLOCKED")
        self.assertEqual(payload["latest_decision"]["target_exposure"], 0.0)
        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["no_real_orders"])

    def test_complete_archive_exposes_read_only_snapshot(self):
        required = ["daily", "daily_basic", "stk_limit", "suspend_d", "stock_basic", "limit_up_pool", "broken_board_pool", "limit_down_pool", "limit_cpt_list", "moneyflow"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            for dataset in required:
                path = data / "raw" / dataset / "trade_date=20260818"
                path.mkdir(parents=True)
                (path / "manifest.json").write_text(json.dumps({"dataset": dataset, "trade_date": "20260818"}), encoding="utf-8")
            processed = data / "processed"
            processed.mkdir()
            (processed / "sentiment_latest.json").write_text(json.dumps({"state": "advance", "target_exposure": 1.0, "score": 70, "reasons": ["确认"], "metrics": {"date": "20260818"}}), encoding="utf-8")
            old = os.environ.get("A_SHARE_QUANT_ROOT")
            os.environ["A_SHARE_QUANT_ROOT"] = str(root)
            try:
                payload = build_sentiment_quant_dashboard_payload()
            finally:
                if old is None:
                    del os.environ["A_SHARE_QUANT_ROOT"]
                else:
                    os.environ["A_SHARE_QUANT_ROOT"] = old
        self.assertEqual(payload["data_quality"]["status"], "OK")
        self.assertEqual(payload["latest_decision"]["state"], "ADVANCE")
        self.assertEqual(payload["latest_decision"]["target_exposure"], 1.0)

    def test_stale_snapshot_hides_candidates_and_ai_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed = root / "data" / "processed"
            processed.mkdir(parents=True)
            (processed / "sentiment_latest.json").write_text(json.dumps({"state": "advance", "target_exposure": 1.0, "metrics": {"date": "20260817"}}), encoding="utf-8")
            (processed / "signals_latest.json").write_text(json.dumps({"signals": [{"symbol": "000001.SZ", "date": "20260817"}]}), encoding="utf-8")
            (processed / "daily_research_latest.json").write_text(json.dumps({"trade_date": "20260817", "summary": "旧报告"}), encoding="utf-8")
            old = os.environ.get("A_SHARE_QUANT_ROOT")
            os.environ["A_SHARE_QUANT_ROOT"] = str(root)
            try:
                payload = build_sentiment_quant_dashboard_payload()
            finally:
                if old is None:
                    del os.environ["A_SHARE_QUANT_ROOT"]
                else:
                    os.environ["A_SHARE_QUANT_ROOT"] = old
        self.assertEqual(payload["latest_decision"]["state"], "BLOCKED")
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["research_note"], {})

    @patch("sentiment_quant_dashboard.build_dashboard_payload")
    def test_intraday_observation_is_read_only_and_uses_existing_engine_cycle(self, build_dashboard):
        build_dashboard.return_value = {
            "health": {
                "ok": True,
                "latest_cycle_age_seconds": 42,
                "stale_limit_seconds": 300,
                "market_window": {"is_open": True, "phase": "morning"},
            },
            "latest_cycle": {"cycle_id": "cycle-1", "status": "ok", "finished_at": "2026-08-19T10:03:00+08:00"},
            "sentiment": {"risk_appetite": "risk_on", "trade_permission": "BUY_ALLOWED", "sentiment_score": 66.2, "coverage_level": "high", "coverage_count": 80, "breadth": 0.61, "hard_rules": ["盘中仅观察"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("A_SHARE_QUANT_ROOT")
            os.environ["A_SHARE_QUANT_ROOT"] = tmp
            try:
                payload = build_sentiment_quant_dashboard_payload()
            finally:
                if old is None:
                    del os.environ["A_SHARE_QUANT_ROOT"]
                else:
                    os.environ["A_SHARE_QUANT_ROOT"] = old
        live = payload["intraday_observation"]
        self.assertEqual(live["status"], "LIVE")
        self.assertEqual(live["refresh_interval_seconds"], 180)
        self.assertFalse(live["actionable"])
        self.assertEqual(payload["latest_decision"]["target_exposure"], 0.0)
