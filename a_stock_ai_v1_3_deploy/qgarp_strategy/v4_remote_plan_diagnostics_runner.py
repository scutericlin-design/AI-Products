"""Run read-only Q-GARP v4 plan attribution against an isolated cache."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from qgarp_strategy.config import load_settings
from qgarp_strategy.storage import QGARPStore


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    engine = _load("qgarp_v4_candidate", Path(os.getenv("QGARP_V4_ENGINE_PATH", "/tmp/qgarp_v47_factor_reversal.py")))
    diagnostics = _load("qgarp_v4_diagnostics", Path(os.getenv("QGARP_V4_DIAGNOSTICS_PATH", "/tmp/v4_plan_diagnostics.py")))
    print(json.dumps({"status": "started"}), flush=True)
    result = diagnostics.diagnose_monthly_plans(QGARPStore(load_settings().db_path), load_settings(), engine.QGARPv4Research())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
