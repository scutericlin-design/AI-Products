"""Temporary-cache entrypoint for read-only Q-GARP v4.9 signal diagnostics."""

from __future__ import annotations

import json
import os
import importlib.util
import sys
from pathlib import Path

from qgarp_strategy.config import load_settings
from qgarp_strategy.storage import QGARPStore


def _load_diagnostics():
    path = Path(os.getenv("QGARP_SIGNAL_DIAGNOSTICS_PATH", "/tmp/v4_signal_diagnostics.py"))
    spec = importlib.util.spec_from_file_location("qgarp_strategy.v4_signal_diagnostics", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"unable to load signal diagnostics: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    settings = load_settings()
    print(json.dumps({"status": "started", "research_only": True}), flush=True)
    report = _load_diagnostics().run_v49_signal_diagnostics(
        QGARPStore(settings.db_path), settings,
        os.getenv("QGARP_SIGNAL_START", "20190101"), os.getenv("QGARP_SIGNAL_END", "20260731"),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
