from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict

from .backtest import BacktestResult


def parameter_sensitivity(values: Iterable[float], run: Callable[[float], BacktestResult]) -> list[dict]:
    """Report a parameter neighborhood; it never chooses a best parameter."""
    rows = []
    for value in values:
        result = run(value)
        rows.append({"parameter": value, **asdict(result), "trade_count": len(result.trades)})
    return rows


def walk_forward(windows: Iterable[tuple[str, str, str, str]], run: Callable[[str, str, str, str], BacktestResult]) -> list[dict]:
    """Run predetermined train/test windows without retraining on test data."""
    return [
        {"train_start": a, "train_end": b, "test_start": c, "test_end": d, **asdict(run(a, b, c, d))}
        for a, b, c, d in windows
    ]
