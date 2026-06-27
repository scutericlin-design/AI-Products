from __future__ import annotations

import time
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import PROJECT_ROOT, settings
from app.database import SessionLocal, init_db
from app.models import SessionToken, UsageLog
from app.routers import admin, analytics, auth, backtest, billing, feedback, institutional, portfolio, system_pool, watchlist
from app.security import hash_token


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(analytics.router)
app.include_router(backtest.router)
app.include_router(billing.router)
app.include_router(feedback.router)
app.include_router(institutional.router)
app.include_router(portfolio.router)
app.include_router(system_pool.router)
app.include_router(watchlist.router)

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")
app.mount("/opendesign", StaticFiles(directory=PROJECT_ROOT / "opendesign", html=True), name="opendesign")


@app.middleware("http")
async def usage_log_middleware(request, call_next):
    start = time.perf_counter()
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        path = request.url.path
        if path.startswith("/api/"):
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            response_bytes = 0
            if response is not None:
                response_bytes = int(response.headers.get("content-length") or 0)
            authorization = request.headers.get("authorization") or ""
            user_id = None
            db = SessionLocal()
            try:
                if authorization.startswith("Bearer "):
                    token = authorization.removeprefix("Bearer ").strip()
                    session = db.scalar(
                        select(SessionToken).where(
                            SessionToken.token_hash == hash_token(token),
                            SessionToken.expires_at > datetime.utcnow(),
                        )
                    )
                    if session is not None:
                        user_id = session.user_id
                db.add(
                    UsageLog(
                        user_id=user_id,
                        method=request.method,
                        path=path[:255],
                        status_code=status_code,
                        duration_ms=duration_ms,
                        response_bytes=response_bytes,
                        client_host=request.client.host if request.client else None,
                        user_agent=(request.headers.get("user-agent") or "")[:255],
                    )
                )
                db.commit()
            finally:
                db.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def root():
    return RedirectResponse(url="/static/app.html")


@app.get("/health")
def health():
    return {"ok": True, "app": settings.app_name}
