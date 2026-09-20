# Deployment Notes

This app is designed to run locally first and move later to a cloud VM on Alibaba Cloud or Tencent Cloud.

For the Tencent Cloud Lighthouse deployment workflow, use:

```text
deployment/tencent-cloud.md
```

## Local Python

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Local Docker

```bash
cp .env.example .env
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000/
```

## Cloud VM Path

1. Provision an Alibaba Cloud or Tencent Cloud Linux VM.
2. Install Docker and Docker Compose.
3. Copy this repository to the VM.
4. Create `.env`.
5. Set the trading credentials and the personal-plan switches in `.env`, then run `docker compose --profile trading up -d --build`.

## Personal five-year plan and Feishu notifications

The `trading-engine` service can run the private five-year plan described in
`/api/personal-plan`. It produces only manual trade-plan messages; it does not
connect to a broker or submit orders.

Configure these values on the server (never commit the keys):

```text
TRADING_PERSONAL_PLAN_ENABLED=true
TRADING_DRY_RUN=false
TRADING_PUSH_ENABLED=true
TRADING_TUSHARE_TOKEN=...
TRADING_FEISHU_WEBHOOK_URL=...
```

The plan blocks a new stock buy when its fundamental status or valuation data
is missing or stale. Update the complete stock-pool document through the
admin-only `GET`/`PUT /api/personal-plan` endpoints after each financial-report
review and after manually placing a trade, including the account value, cash,
and holdings. The scheduler deduplicates unchanged instructions and sends a
single daily status message when there is no actionable change.
6. Put Caddy and HTTPS in front of port `8000`; `docker-compose.prod.yml` already includes Caddy.

## Database Migration Path

Local MVP uses SQLite:

```text
DATABASE_URL=sqlite:////app/data/app.db
```

Cloud production should use PostgreSQL:

```text
DATABASE_URL=postgresql+psycopg://user:password@host:5432/ashare_alpha
```

When switching to PostgreSQL, add `psycopg[binary]` to `requirements.txt`.

## Current Multi-User Capabilities

- User registration and login.
- Session token auth.
- Per-user portfolio positions.
- Per-user watchlist.
- Shared system recommendation pool.
- Browser-triggered system pool rebuild.

## Important Compliance Note

This is a research and decision-support system. If it becomes a public paid service that provides individual stock buy/sell advice, review securities investment advisory compliance before launch.
