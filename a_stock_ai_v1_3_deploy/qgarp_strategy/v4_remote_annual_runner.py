"""Temporary-cache runner for isolated annual Q-GARP v4 research."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from qgarp_strategy.config import load_settings
from qgarp_strategy.storage import QGARPStore


def main() -> int:
    engine_path = Path(os.getenv("QGARP_V4_ENGINE_PATH", "/tmp/qgarp_v44_research.py"))
    annual_path = Path(os.getenv("QGARP_V4_ANNUAL_RUNNER_PATH", "/tmp/v4_annual_research.py"))
    ledger_path = os.getenv("QGARP_V4_LEDGER_PATH")
    if ledger_path:
        ledger_spec = importlib.util.spec_from_file_location("qgarp_strategy.v4_factor_state_ledger", Path(ledger_path))
        if not ledger_spec or not ledger_spec.loader:
            raise RuntimeError(f"unable to load factor ledger: {ledger_path}")
        ledger_module = importlib.util.module_from_spec(ledger_spec)
        sys.modules[ledger_spec.name] = ledger_module
        ledger_spec.loader.exec_module(ledger_module)
    backtest_path = os.getenv("QGARP_V4_BACKTEST_PATH")
    if backtest_path:
        backtest_spec = importlib.util.spec_from_file_location("qgarp_strategy.v3_backtest", Path(backtest_path))
        if not backtest_spec or not backtest_spec.loader:
            raise RuntimeError(f"unable to load research backtest: {backtest_path}")
        backtest_module = importlib.util.module_from_spec(backtest_spec)
        sys.modules[backtest_spec.name] = backtest_module
        backtest_spec.loader.exec_module(backtest_module)
    spec = importlib.util.spec_from_file_location("qgarp_v44_candidate", engine_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"unable to load research engine: {engine_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    annual_spec = importlib.util.spec_from_file_location("qgarp_v44_annual", annual_path)
    if not annual_spec or not annual_spec.loader:
        raise RuntimeError(f"unable to load annual runner: {annual_path}")
    annual_module = importlib.util.module_from_spec(annual_spec)
    sys.modules[annual_spec.name] = annual_module
    annual_spec.loader.exec_module(annual_module)
    settings = load_settings()
    start_year = int(os.getenv("QGARP_V4_RESEARCH_START_YEAR", "2019"))
    end_year = int(os.getenv("QGARP_V4_RESEARCH_END_YEAR", "2026"))
    print(json.dumps({"status": "started", "start_year": start_year, "end_year": end_year}), flush=True)
    report = annual_module.run_annual_style_research(
        QGARPStore(settings.db_path), settings, module.QGARPv4Research(), start_year, end_year,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
