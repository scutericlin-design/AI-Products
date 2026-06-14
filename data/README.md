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

Normal product usage does not require a hand-maintained stock-list file. Use the web page buttons and API endpoints to update the system pool and account holdings.

## System-Generated Output

- `data/raw/prices/<code>.csv`
- `data/raw/price_ingest_manifest.csv`
- `data/processed/factors_price_daily.csv`
- `data/processed/factors_price_latest.csv`
- `data/processed/signal_daily.csv`
- `data/processed/signal_latest.csv`
- `data/processed/recommended_pool.csv`

These files are system-generated caches or research outputs. Normal users should not edit them. Web pages and API endpoints are the operating interface; generated files are kept only so the local research engine can cache expensive API results and rerun backtests.

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

## Portfolio Advice

Normal use is browser/API only:

1. Log in to the app.
2. Open `个人持股`.
3. Add or update holdings in the web form.
4. The `/api/portfolio/advice` endpoint reads server-side account holdings and joins them to the latest system pool.

The default risk budget is:

- single-stock maximum target: `12%`
- watch/degraded-stock cap: `4%`

The API output combines current account holdings and `institutional_score_v3` system-pool signals into:

- `portfolio_action`
- `suggested_target_weight`
- `weight_delta`
- `pnl_pct`
- `advice_reason`

`scripts/build_portfolio_advice.py --portfolio-csv ...` remains only as a development/legacy import helper and is not part of the normal product workflow.

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

This writes the same generated cache read by `/api/system-pool`. The web UI consumes the API, not the CSV file directly. It is a same-day institutional score screen, so use it as the first pass before deeper 20-day factor, announcement, and portfolio-risk review.

The full-A-share scan now uses `institutional_score_v3`:

- `alpha_score`: strength confirmation, close position, and gap quality.
- `liquidity_capacity_score`: market-wide amount percentile and trading capacity.
- `risk_control_score`: stability, tradability, and reversal risk.
- `crowding_penalty`: chase-risk and overheated move deduction.

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

The System Pool page has an `更新全A股票池` button. It calls the local API endpoint `/api/system-pool/rebuild`, runs the full-A-share scan, updates the generated cache, and refreshes the table in the browser through `/api/system-pool`.
