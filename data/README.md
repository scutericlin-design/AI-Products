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
- `data/processed/portfolio_advice.csv`
- `data/processed/recommended_pool.csv`

These files are local research data, not audited production data. Before using signals for actual trading, add field validation, duplicate checks, missing-date checks, and a second data source.

CSV outputs are written as `utf-8-sig` so Chinese text opens correctly in Excel and Numbers.

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

## Build Portfolio Advice

Create `data/portfolio.csv`:

```csv
symbol,name,weight,cost_price,shares
300750,宁德时代,0.085,145.20,100
```

Then run:

```bash
python scripts/build_portfolio_advice.py
```

The default risk budget is:

- single-stock maximum target: `12%`
- watch/degraded-stock cap: `4%`

The output combines current holdings and `signal_latest.csv` into:

- `portfolio_action`
- `suggested_target_weight`
- `weight_delta`
- `pnl_pct`
- `advice_reason`

## Build System Recommendation Pool

After building signals:

```bash
python scripts/build_recommended_pool.py --limit 30
```

The system pool uses the latest trade date only, excludes `avoid`, keeps `buy` and `watch`, and caps the result at 30 stocks.

For a full-A-share fast scan, use the AKShare Sina spot quote endpoint:

```bash
python scripts/build_a_share_spot_pool.py --limit 30 --min-amount 300000000
```

This writes the same `data/processed/recommended_pool.csv` consumed by the prototype UI. It is a same-day strength and liquidity screen, so use it as the first pass before deeper 20-day factor, announcement, and portfolio-risk review.

The full-A-share scan now uses a quality-first score:

- `strength_score`: prefers meaningful positive strength, but penalizes overheated one-day moves.
- `liquidity_score`: favors higher turnover value so candidates are easier to trade.
- `close_position_score`: favors stocks closing near the upper part of the intraday range.
- `stability_score`: penalizes excessive intraday amplitude.
- `gap_quality_score`: penalizes large gap opens.
- `tradability_score`: penalizes limit-up or near-limit-up stocks that may be hard to buy.

These filters improve candidate quality but do not guarantee success. Every recommendation still needs manual review.

## Browser-Only Operation

For normal use, start the local app server once:

```bash
python scripts/local_app_server.py --host 127.0.0.1 --port 8289
```

Then open:

```text
http://127.0.0.1:8289/opendesign/
```

The System Pool page has an `更新全A股票池` button. It calls the local API endpoint `/api/rebuild-system-pool`, runs the full-A-share scan, rewrites `recommended_pool.csv`, and refreshes the table in the browser.
