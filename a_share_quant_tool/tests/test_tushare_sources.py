import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from a_share_quant.config import Settings
from a_share_quant.data import TushareDataClient, TushareProxyClient, TushareCallAudit
from a_share_quant.pipeline import build_daily_pipeline
from a_share_quant.storage import ArchiveStore


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [["000001.SZ", "20260818"]]}}


class _Session:
    def __init__(self):
        self.payload = None

    def post(self, url, json, timeout):
        self.payload = {"url": url, "json": json, "timeout": timeout}
        return _Response()


class TushareSourceTests(unittest.TestCase):
    def test_dashboard_proxy_archives_and_audits_without_exposing_token(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = _Session()
            audit_path = root / "reports" / "tushare_call_audit.jsonl"
            proxy = TushareProxyClient("secret-token", "https://proxy.example", TushareCallAudit(audit_path), session=session)
            client = TushareDataClient(ArchiveStore(root / "data"), None, pro_client=proxy, audit_path=audit_path)

            with patch.object(ArchiveStore, "write_frame"):
                result = client.pro.daily(trade_date="20260818", fields="ts_code,trade_date")

            self.assertEqual(result.iloc[0]["ts_code"], "000001.SZ")
            self.assertEqual(session.payload["json"]["api_name"], "daily")
            self.assertEqual(session.payload["json"]["token"], "secret-token")
            self.assertEqual(session.payload["json"]["fields"], "ts_code,trade_date")
            self.assertNotIn("fields", session.payload["json"]["params"])
            audit = json.loads(audit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(audit["api"], "daily")
            self.assertTrue(audit["ok"])
            self.assertNotIn("secret-token", audit_path.read_text(encoding="utf-8"))

    def test_settings_default_to_dashboard_proxy(self):
        settings = Settings()
        self.assertEqual(settings.tushare_source, "dashboard_proxy")

    def test_invalid_source_is_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                TushareDataClient(ArchiveStore(Path(directory)), None, source="automatic")

    def test_pipeline_factory_uses_settings_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(data_dir=root / "data", reports_dir=root / "reports", tushare_source="dashboard_proxy")
            pipeline = build_daily_pipeline(settings)
            self.assertEqual(pipeline.client._source, "dashboard_proxy")
