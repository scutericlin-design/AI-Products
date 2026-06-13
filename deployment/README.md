# Deployment Notes

This app is designed to run locally first and move later to a cloud VM on Alibaba Cloud or Tencent Cloud.

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
5. Run `docker compose up -d --build`.
6. Put Nginx and HTTPS in front of port `8000`.

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
