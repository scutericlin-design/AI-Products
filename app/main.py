from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, settings
from app.database import init_db
from app.routers import analytics, auth, backtest, portfolio, system_pool, watchlist


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(analytics.router)
app.include_router(backtest.router)
app.include_router(portfolio.router)
app.include_router(system_pool.router)
app.include_router(watchlist.router)

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")
app.mount("/data", StaticFiles(directory=PROJECT_ROOT / "data"), name="data")
app.mount("/opendesign", StaticFiles(directory=PROJECT_ROOT / "opendesign", html=True), name="opendesign")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def root():
    return RedirectResponse(url="/static/app.html")
