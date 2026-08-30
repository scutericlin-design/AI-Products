from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


class ArchiveStore:
    """Immutable point-in-time archive keyed by dataset and trade date."""

    def __init__(self, root: Path):
        self.root = root

    def write_frame(self, dataset: str, trade_date: str, frame: pd.DataFrame, source: str) -> Path:
        target = self.root / "raw" / dataset / f"trade_date={trade_date}"
        target.mkdir(parents=True, exist_ok=True)
        path = target / "snapshot.parquet"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite archived snapshot: {path}")
        frame.to_parquet(path, index=False)
        (target / "manifest.json").write_text(json.dumps({"dataset": dataset, "trade_date": trade_date, "source": source, "rows": len(frame)}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_frame(self, dataset: str, trade_date: str) -> pd.DataFrame:
        return pd.read_parquet(self.root / "raw" / dataset / f"trade_date={trade_date}" / "snapshot.parquet")

    def write_report(self, name: str, payload: dict[str, Any]) -> Path:
        path = self.root / "processed" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path
