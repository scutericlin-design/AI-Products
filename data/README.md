# Local Data Layer

This folder stores the first usable A-share MVP data.

## Current Source Policy

- Primary quote endpoint: `ak.stock_zh_a_daily`
- Diagnostic endpoint only: `ak.stock_zh_a_hist`

`stock_zh_a_hist` can fail in this network because the Eastmoney endpoint may close the connection. The MVP therefore starts with the endpoint that already works locally.

## Commands

Check AKShare:

```bash
source .venv/bin/activate
python scripts/check_akshare.py
```

Ingest the default test basket:

```bash
python scripts/ingest_prices.py --start 20240101 --end 20240131
```

Ingest your own stock list:

```bash
python scripts/ingest_prices.py --stock-list data/my_stock_list.csv --start 20240101 --end 20240630
```

The custom stock list should be:

```csv
code,name
300750,宁德时代
688012,中微公司
```

## Output

- `data/raw/prices/<code>.csv`
- `data/raw/price_ingest_manifest.csv`
- `data/processed/factors_price_daily.csv`
- `data/processed/factors_price_latest.csv`
- `data/processed/signal_daily.csv`
- `data/processed/signal_latest.csv`

These files are local research data, not audited production data. Before using signals for actual trading, add field validation, duplicate checks, missing-date checks, and a second data source.

## Build Price Factors

After ingesting prices:

```bash
python scripts/build_price_factors.py
```

The first factor set uses only daily price, amount, and turnover:

- `momentum_5d`, `momentum_10d`, `momentum_20d`
- `close_vs_ma20`
- `amount_ratio_5_20`
- `turnover_5d`
- `volatility_20d`
- `breakout_20d`
- `price_factor_score`

For a real 2-8 week strategy, ingest at least 1-3 years of daily data before relying on the ranking.

## Build Strategy Signals

After building factors:

```bash
python scripts/build_signals.py
```

The first signal engine converts `price_factor_score` into:

- `buy`
- `watch`
- `hold_or_reduce`
- `avoid`

It also writes target weights, human-readable reasons, and risk flags such as:

- `insufficient_20d_history`
- `momentum_20d_negative`
- `below_ma20`
- `high_volatility`
- `amount_contraction`

Signals are research outputs only. They require manual review before any real trade.
