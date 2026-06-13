from __future__ import annotations

import csv
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException

from app.config import PROJECT_ROOT, settings
from app.deps import current_user
from app.models import User


router = APIRouter(prefix="/api/system-pool", tags=["system-pool"])


def read_pool() -> list[dict[str, str]]:
    if not settings.recommended_pool_path.exists():
        return []
    with settings.recommended_pool_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@router.get("")
def list_system_pool(user: User = Depends(current_user)):
    return {"rows": read_pool()}


@router.post("/rebuild")
def rebuild_system_pool(user: User = Depends(current_user)):
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_a_share_spot_pool.py"),
        "--limit",
        "30",
        "--min-amount",
        "300000000",
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "System pool rebuild failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    return {"ok": True, "rows": len(read_pool()), "stdout": completed.stdout[-4000:]}
