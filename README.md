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
