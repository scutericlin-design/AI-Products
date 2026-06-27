# AI Products

## A-Share Alpha Lab

Local-first A-share investment research app. It currently supports:

- OpenDesign prototype at `/opendesign/`
- Multi-user local web app at `/`
- Register/login
- Per-user portfolio positions
- Shared system stock pool
- Browser-triggered full-A-share scan

Run the multi-user app locally:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Future cloud deployment notes are in `deployment/README.md`.

## A-Share Realtime Trading Engine v1.3

The deployable v1.3 runtime is available as a separate container service:

```bash
docker compose up --build trading-engine
```

It runs this production chain:

```text
Docker -> Python trading engine -> APScheduler loop -> TuShare data layer
-> market state engine -> realtime leader tracker -> MiniMax decision layer
-> risk engine -> Feishu push -> SQLite logs
```

Local dry-run verification without external keys:

```bash
python -m app.trading --once
```

Runtime logs are written to SQLite tables:

- `trading_engine_runs`
- `trading_decision_logs`
- `trading_push_logs`

Configure keys and runtime switches in `.env` from `.env.example`.
