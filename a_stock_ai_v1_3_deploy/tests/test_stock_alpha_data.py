from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd

from stock_alpha.cli import benchmark_comparison
from stock_alpha.data import ResearchDataClient, load_dataset, write_json


class StockAlphaDataTests(TestCase):
    def test_offline_missing_input_does_not_fetch_or_invent(self):
        with TemporaryDirectory() as directory:
            client = ResearchDataClient(Path(directory), offline=True)
            with patch.object(client.session, "post") as post:
                with self.assertRaisesRegex(RuntimeError, "Missing offline input"):
                    client.query("daily", ts_code="600000.SH")
                post.assert_not_called()

    def test_query_caches_without_saving_credentials(self):
        with TemporaryDirectory() as directory:
            client = ResearchDataClient(Path(directory))
            response = Mock()
            response.json.return_value = {"code": 0, "data": {"fields": ["ts_code", "close"], "items": [["600000.SH", 10.0]]}}
            with patch.dict("os.environ", {"TUSHARE_TOKEN": "not-a-real-secret"}), patch.object(client.session, "post", return_value=response) as post, patch("stock_alpha.data.time.sleep"):
                first = client.query("daily", ts_code="600000.SH")
                second = client.query("daily", ts_code="600000.SH")
            pd.testing.assert_frame_equal(first, second)
            self.assertEqual(post.call_count, 1)
            for path in Path(directory).rglob("*.json"):
                self.assertNotIn("not-a-real-secret", path.read_text())

    def test_manifest_tampering_fails_before_replay(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            for name in ("bars", "fundamentals", "memberships"):
                path = root / f"{name}.csv.gz"
                pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260105"]}).to_csv(path, index=False, compression="gzip")
                files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            write_json(root / "calendar.json", ["20260105"])
            files["calendar"] = {"sha256": hashlib.sha256((root / "calendar.json").read_bytes()).hexdigest()}
            write_json(root / "manifest.json", {"files": files})
            load_dataset(root)
            write_json(root / "calendar.json", ["20260106"])
            with self.assertRaisesRegex(ValueError, "calendar"):
                load_dataset(root)

    def test_benchmark_rejects_missing_prices(self):
        frame = pd.DataFrame({"trade_date": ["20260105", "20260106"], "close": [100.0, float("nan")]})
        with self.assertRaisesRegex(ValueError, "Invalid benchmark"):
            benchmark_comparison(frame, "20260105", "20260106")

    def test_benchmark_year_return_keeps_boundary_overnight(self):
        frame = pd.DataFrame({"trade_date": ["20251230", "20251231", "20260105"], "close": [100.0, 110.0, 121.0]})
        result = benchmark_comparison(frame, "20251230", "20260105")
        self.assertAlmostEqual(result["annual"][0]["return_pct"], 10.0)
        self.assertAlmostEqual(result["annual"][1]["return_pct"], 10.0)
        self.assertAlmostEqual(result["annual"][1]["return_80pct_pct"], 8.0)
