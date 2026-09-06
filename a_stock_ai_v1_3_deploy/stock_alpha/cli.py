"""Run fixed research comparisons; never promotes a strategy or sends an order."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from stock_alpha.backtest import run_backtest
from stock_alpha.data import ResearchDataClient, load_dataset, write_json
from stock_alpha.model import ModelConfig, build_targets, prepare_model_data


def benchmark_comparison(frame: pd.DataFrame, start: str, end: str) -> dict:
    rows = frame.loc[(frame.trade_date >= start) & (frame.trade_date <= end)].sort_values("trade_date")
    if rows.empty or rows.trade_date.duplicated().any():
        raise ValueError("Missing or ambiguous benchmark data")
    closes = pd.to_numeric(rows.close, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError("Invalid benchmark prices")
    changes = np.r_[0.0, closes[1:] / closes[:-1] - 1.0]
    curve = pd.DataFrame({"date": rows.trade_date.to_numpy(), "equity": np.cumprod(1 + changes),
                          "equity_80pct": np.cumprod(1 + 0.8 * changes)})
    annual = []
    previous, previous_scaled = 1.0, 1.0
    for year, group in curve.groupby(curve.date.str[:4], sort=True):
        final, final_scaled = float(group.equity.iloc[-1]), float(group.equity_80pct.iloc[-1])
        annual.append({"year": int(year), "return_pct": (final / previous - 1) * 100,
                       "return_80pct_pct": (final_scaled / previous_scaled - 1) * 100})
        previous, previous_scaled = final, final_scaled
    return {"symbol": "000300.SH", "description": "CSI300 price index; 80% daily-reset index + 20% zero-rate cash reference, no costs/dividends",
            "annual": annual, "daily_curve": curve.to_dict("records")}


def _report(result: dict) -> str:
    lines = ["# 股票多因子研究验证结果", "", "**状态：研究代理回测，不可直接替代主策略，不是机构策略复刻。**", "",
             "本报告按连续账户逐年归因，没有每年清零；2026年为年初至最新可用交易日，不是全年收益。",
             "财报按公告日延后使用，但历史修订版本未独立验证。采用复权单位净值代理，不是真实股数成交。", "",
             "| 方案 | 成本倍数 | 累计收益 | 最大回撤 | Sharpe（无风险利率0） | 成本 | 状态 |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for run in result["runs"]:
        metrics = run["metrics"]
        sharpe = metrics.get("sharpe")
        lines.append(f"| {run['variant']} | {run['cost_multiplier']:g} | {metrics['return_pct']:+.2f}% | {abs(metrics['max_drawdown_pct']):.2f}% | {sharpe:.2f} | {metrics['costs']:.2f} | {run['status']} |" if sharpe is not None else
                     f"| {run['variant']} | {run['cost_multiplier']:g} | {metrics['return_pct']:+.2f}% | {abs(metrics['max_drawdown_pct']):.2f}% | N/A | {metrics['costs']:.2f} | {run['status']} |")
    lines += ["", "## 逐年结果", "", "| 年份 | balanced净收益 | trend_scaled净收益 | 沪深300价格收益 | 80%指数+20%现金参考 |", "|---|---:|---:|---:|---:|"]
    by_variant = {run["variant"]: {row["year"]: row for row in run["annual"]} for run in result["runs"] if run["cost_multiplier"] == 1}
    reference = {row["year"]: row for row in (result.get("benchmark") or {}).get("annual", [])}
    for year in sorted(by_variant.get("balanced", {})):
        a = by_variant["balanced"][year]["return_pct"]
        b = by_variant.get("trend_scaled", {}).get(year, {}).get("return_pct")
        index = reference.get(year, {})
        def fmt(value: float | None) -> str:
            return f"{value:+.2f}%" if value is not None else "缺数据"
        lines.append(f"| {year} | {fmt(a)} | {fmt(b)} | {fmt(index.get('return_pct'))} | {fmt(index.get('return_80pct_pct'))} |")
    lines += ["", "## 使用边界", "", "- 只预声明两个固定方案并测试成本翻倍，没有按年份调参选冠军。",
              "- 股票池为每年开始前已知的160只大市值流通股，历史并集随数据而变；不是全A股，也不是旧adaptive160，不能与旧报告直接比较。",
              "- 最大12只、单票8%、目标总仓位80%；趋势缩放版在弱趋势或高波动时降低预算。上述数值是研究起点，不是已证实的最优参数。",
              "- 相关性约束不等于行业中性，仍可能存在行业/风格集中。",
              "- 双倍成本敏感性不是统计显著性证明，低换手也不等于有Alpha。",
              "- 财报版本、历史ST/退市、涨跌停成交队列及公司行动现金/份额账本未完整核验；任何漂亮收益均不得越过这些缺口。",
              "- 旧回测多次使用的年份不能再称为真正未见样本，仍需冻结版本后的前瞻模拟。",
              "- 旧主策略、ETF、热点龙头、QGARP账户均未由本命令改动。", "",
              "## 数据与代码", "", f"数据清单哈希：`{result['manifest_sha256']}`", "",
              f"完整输入与方案：`comparison.json`；各次运行保留`daily_curve`、`orders`和排除诊断。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch-benchmark", action="store_true")
    args = parser.parse_args()
    bars, fundamentals, memberships, calendar, manifest = load_dataset(args.data)
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"runs": [], "promoted": False, "manifest": manifest,
              "manifest_sha256": hashlib.sha256((args.data / "manifest.json").read_bytes()).hexdigest(),
              "source_hashes": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(__file__).parent.glob("*.py")}}
    base_config = ModelConfig()
    sessions = [day for day in calendar if manifest["start"] <= day <= manifest["end"]]
    signal_dates = sorted(set(sessions[::base_config.rebalance_sessions] + [sessions[-1]]))
    print(json.dumps({"stage": "prepare", "bars": len(bars), "sessions": len(sessions)}), flush=True)
    prepared = prepare_model_data(bars, fundamentals, memberships, signal_dates, base_config, end=manifest["end"])
    for variant in ("balanced", "trend_scaled"):
        config = ModelConfig(variant=variant)
        for cost in (1.0, 2.0):
            filename = f"{variant}_cost{cost:g}.json"
            print(json.dumps({"stage": "backtest", "variant": variant, "cost": cost}), flush=True)
            run = run_backtest(bars, fundamentals, memberships, calendar, manifest["start"], manifest["end"], config, cost_multiplier=cost, prepared=prepared)
            write_json(args.output / filename, run)
            result["runs"].append({key: run[key] for key in ("variant", "status", "metrics", "annual", "cost_multiplier")})
            print(json.dumps({"completed": filename, "metrics": run["metrics"]}), flush=True)
        plan = build_targets(bars, fundamentals, memberships, calendar[-1], {}, config, prepared=prepared)
        write_json(args.output / f"{variant}_latest_shadow_targets.json", {**plan, "mode": "research_only", "not_orders": True, "config": asdict(config)})
    if args.fetch_benchmark:
        client = ResearchDataClient(args.data)
        index = client.query("index_daily", ts_code="000300.SH", start_date=manifest["start"], end_date=manifest["end"])
        index["trade_date"] = index.trade_date.astype(str)
        result["benchmark"] = benchmark_comparison(index, manifest["start"], manifest["end"])
    result["promotion"] = {"allowed": False, "reason": "Exploratory proxy; no verified historical status/corporate-action ledger or prospective execution validation"}
    write_json(args.output / "comparison.json", result)
    (args.output / "comparison.md").write_text(_report(result), encoding="utf-8")
    print(json.dumps({"stage": "complete", "report": str(args.output / "comparison.md"), "promoted": False}), flush=True)


if __name__ == "__main__":
    main()
