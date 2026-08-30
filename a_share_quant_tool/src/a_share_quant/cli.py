from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .config import Settings
from .data import TushareDataClient
from .models import SentimentMetrics
from .sentiment import SentimentEngine
from .storage import ArchiveStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="a-share-quant")
    commands = parser.add_subparsers(dest="command", required=True)
    sentiment = commands.add_parser("sentiment", help="从盘后指标文件计算五态情绪")
    sentiment.add_argument("--metrics", required=True, help="SentimentMetrics 的 JSON 文件")
    sentiment.add_argument("--output", default="sentiment_latest.json")
    health = commands.add_parser("tushare-health", help="验证当前配置的数据源并生成无密钥审计记录")
    health.add_argument("--date", required=True, help="待验证交易日，格式 YYYYMMDD")
    args = parser.parse_args()
    if args.command == "sentiment":
        payload = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
        snapshot = SentimentEngine().evaluate(SentimentMetrics(**payload))
        settings = Settings.from_env()
        settings.ensure_directories()
        path = ArchiveStore(settings.data_dir).write_report(args.output, asdict(snapshot))
        print(path)
    elif args.command == "tushare-health":
        settings = Settings.from_env()
        settings.ensure_directories()
        frame = TushareDataClient.from_settings(ArchiveStore(settings.data_dir), settings).market_snapshot(args.date)
        print(json.dumps({"source": settings.tushare_source, "date": args.date, "rows": len(frame)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
