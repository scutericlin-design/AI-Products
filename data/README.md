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

These files are local research data, not audited production data. Before using signals for actual trading, add field validation, duplicate checks, missing-date checks, and a second data source.
