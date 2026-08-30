import json
import unittest

from a_share_quant.config import Settings
from a_share_quant.deepseek import DeepSeekResearchClient


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": json.dumps({"summary": "事实摘要", "risks": ["数据缺口"], "data_gaps": [], "evidence_used": ["sentiment"]})}}]}


class _Session:
    def __init__(self):
        self.payload = None

    def post(self, url, headers, json, timeout):
        self.payload = json
        return _Response()


class DeepSeekTests(unittest.TestCase):
    def test_research_client_uses_json_and_never_receives_order_capability(self):
        session = _Session()
        settings = Settings(deepseek_api_key="test-key")
        note = DeepSeekResearchClient(settings, session=session).review_daily_research(
            trade_date="20260818", sentiment={"state": "advance"}, signals=[], data_quality={"fresh": True},
        )
        self.assertEqual(note.summary, "事实摘要")
        self.assertEqual(session.payload["response_format"], {"type": "json_object"})
        self.assertNotIn("orders", session.payload)
