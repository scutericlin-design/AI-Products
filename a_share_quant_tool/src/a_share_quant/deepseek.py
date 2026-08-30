from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings


class DeepSeekResearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchNote:
    summary: str
    risks: tuple[str, ...]
    data_gaps: tuple[str, ...]
    evidence_used: tuple[str, ...]
    model: str


class DeepSeekResearchClient:
    """Structured explanation and review client; it has no order capability.

    The model receives only supplied, timestamped facts. Output is rejected unless
    it is valid JSON and is kept separate from deterministic strategy signals.
    """

    def __init__(self, settings: Settings, session: object | None = None):
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY 未配置")
        self.base_url = settings.deepseek_base_url
        self.api_key = settings.deepseek_api_key
        self.model = settings.deepseek_model
        self.session = session

    def review_daily_research(self, *, trade_date: str, sentiment: dict[str, Any], signals: list[dict[str, Any]], data_quality: dict[str, Any]) -> ResearchNote:
        evidence = {
            "trade_date": trade_date,
            "sentiment": sentiment,
            "signals": signals,
            "data_quality": data_quality,
        }
        system = (
            "你是A股量化研究报告助手。仅总结用户提供的带日期事实；不得编造数据、不得给出个性化投资建议、"
            "不得输出买卖数量或下单指令。必须输出 json 对象，字段为 summary、risks、data_gaps、evidence_used；"
            "每项风险需能映射到输入事实。"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
            "max_tokens": 900,
            "thinking": {"type": "disabled"},
        }
        try:
            response_payload = self._post(payload)
            content = response_payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (HTTPError, URLError, OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise DeepSeekResearchError("DeepSeek 研究报告生成失败；策略与风控应继续使用确定性结果") from exc
        required = {"summary", "risks", "data_gaps", "evidence_used"}
        if not isinstance(parsed, dict) or required - parsed.keys():
            raise DeepSeekResearchError("DeepSeek 返回结构不符合研究报告协议")
        return ResearchNote(
            summary=str(parsed["summary"]),
            risks=tuple(str(x) for x in parsed["risks"]),
            data_gaps=tuple(str(x) for x in parsed["data_gaps"]),
            evidence_used=tuple(str(x) for x in parsed["evidence_used"]),
            model=self.model,
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.session is not None:
            response = self.session.post(url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            return response.json()
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=45) as response:  # nosec B310: endpoint is configured official API host
            return json.loads(response.read().decode("utf-8"))
