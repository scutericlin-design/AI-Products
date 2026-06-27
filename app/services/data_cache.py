from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select

from app.config import settings
from app.database import SessionLocal
from app.models import DataCacheEntry
from app.services.timezone import BEIJING_TZ, beijing_iso, file_mtime_beijing_iso, now_beijing


PROVIDER = "tushare"
LOCAL_DATASETS = {
    "daily": settings.market_data_cache_dir / "daily",
    "daily_basic": settings.market_data_cache_dir / "daily_basic",
    "fina_indicator": settings.market_data_cache_dir / "fina_indicator",
}
LOCAL_SINGLE_FILES = {
    "stock_basic": settings.market_data_cache_dir / "stock_basic.csv",
    "trade_cal": settings.market_data_cache_dir / "trade_cal.csv",
}


def active_cache_backend() -> str:
    configured = settings.market_data_cache_backend.lower().strip()
    if configured == "filesystem":
        return "filesystem"
    return "database"


def read_market_cache(
    provider: str,
    dataset: str,
    cache_key: str,
    path: Path,
    dtype: dict[str, str] | None = None,
) -> pd.DataFrame | None:
    if active_cache_backend() == "database":
        db = SessionLocal()
        try:
            entry = db.scalar(
                select(DataCacheEntry).where(
                    DataCacheEntry.provider == provider,
                    DataCacheEntry.dataset == dataset,
                    DataCacheEntry.cache_key == cache_key,
                )
            )
            if entry is None or not entry.payload_csv:
                return None
            return pd.read_csv(StringIO(entry.payload_csv), dtype=dtype or {})
        finally:
            db.close()
    if not path.exists() or path.stat().st_size == 0:
        return None
    return pd.read_csv(path, dtype=dtype or {}, encoding="utf-8-sig")


def write_market_cache(
    provider: str,
    dataset: str,
    cache_key: str,
    path: Path,
    frame: pd.DataFrame,
    trade_date: str | None = None,
) -> None:
    if active_cache_backend() == "database":
        buffer = StringIO()
        frame.to_csv(buffer, index=False)
        payload = buffer.getvalue()
        db = SessionLocal()
        try:
            entry = db.scalar(
                select(DataCacheEntry).where(
                    DataCacheEntry.provider == provider,
                    DataCacheEntry.dataset == dataset,
                    DataCacheEntry.cache_key == cache_key,
                )
            )
            if entry is None:
                entry = DataCacheEntry(provider=provider, dataset=dataset, cache_key=cache_key)
                db.add(entry)
            entry.trade_date = trade_date
            entry.row_count = int(len(frame))
            entry.size_bytes = len(payload.encode("utf-8"))
            entry.payload_csv = payload
            db.commit()
        finally:
            db.close()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _file_record(dataset: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    has_data_rows = True
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            handle.readline()
            has_data_rows = bool(handle.readline().strip())
    trade_date = path.stem if has_data_rows and path.stem.isdigit() and len(path.stem) == 8 else None
    return {
        "dataset": dataset,
        "path": str(path),
        "cache_key": path.stem,
        "trade_date": trade_date,
        "empty": not has_data_rows,
        "size_bytes": int(stat.st_size),
        "updated_at": file_mtime_beijing_iso(path),
    }


def _summarize_records(dataset: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted(item["trade_date"] for item in records if item.get("trade_date"))
    return {
        "dataset": dataset,
        "file_count": len(records),
        "empty_count": sum(1 for item in records if item.get("empty")),
        "size_bytes": sum(int(item["size_bytes"]) for item in records),
        "oldest_trade_date": dates[0] if dates else None,
        "latest_trade_date": dates[-1] if dates else None,
        "latest_updated_at": max((item["updated_at"] for item in records), default=None),
    }


def _filesystem_cache_summary() -> dict[str, Any]:
    records_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for dataset, directory in LOCAL_DATASETS.items():
        records_by_dataset[dataset] = [_file_record(dataset, path) for path in sorted(directory.glob("*.csv"))]
    for dataset, path in LOCAL_SINGLE_FILES.items():
        records_by_dataset[dataset] = [_file_record(dataset, path)] if path.exists() else []
    datasets = [_summarize_records(dataset, records) for dataset, records in records_by_dataset.items()]
    total_size = sum(int(item["size_bytes"]) for item in datasets)
    return {
        "backend": "filesystem",
        "storage_mode": "本地文件缓存",
        "root": str(settings.market_data_cache_dir),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "datasets": datasets,
        "supports_prune": True,
    }


def _database_cache_summary() -> dict[str, Any]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                DataCacheEntry.dataset,
                func.count(DataCacheEntry.id),
                func.coalesce(func.sum(DataCacheEntry.size_bytes), 0),
                func.min(DataCacheEntry.trade_date),
                func.max(DataCacheEntry.trade_date),
                func.max(DataCacheEntry.updated_at),
            )
            .where(DataCacheEntry.provider == PROVIDER)
            .group_by(DataCacheEntry.dataset)
        ).all()
        datasets = [
            {
                "dataset": dataset,
                "file_count": int(count),
                "size_bytes": int(size_bytes or 0),
                "oldest_trade_date": oldest_trade_date,
                "latest_trade_date": latest_trade_date,
                "latest_updated_at": beijing_iso(latest_updated_at),
            }
            for dataset, count, size_bytes, oldest_trade_date, latest_trade_date, latest_updated_at in rows
        ]
        total_size = sum(int(item["size_bytes"]) for item in datasets)
        return {
            "backend": "database",
            "storage_mode": "数据库缓存",
            "root": "data_cache_entries",
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "datasets": datasets,
            "supports_prune": True,
        }
    finally:
        db.close()


def cache_summary() -> dict[str, Any]:
    if active_cache_backend() == "database":
        return _database_cache_summary()
    return _filesystem_cache_summary()


def _cutoff_from_records(records: list[dict[str, Any]], keep_recent_days: int) -> str | None:
    dates = sorted(item["trade_date"] for item in records if item.get("trade_date"))
    if not dates:
        return None
    latest = pd.to_datetime(dates[-1], format="%Y%m%d", errors="coerce")
    if pd.isna(latest):
        return None
    return (latest - pd.Timedelta(days=keep_recent_days)).strftime("%Y%m%d")


def _prune_files(dataset: str, keep_recent_days: int, dry_run: bool) -> dict[str, Any]:
    datasets = list(LOCAL_DATASETS) if dataset == "all" else [dataset]
    deleted_files = 0
    deleted_bytes = 0
    candidates: list[str] = []
    for item in datasets:
        directory = LOCAL_DATASETS.get(item)
        if directory is None:
            continue
        records = [_file_record(item, path) for path in sorted(directory.glob("*.csv"))]
        cutoff_trade_date = _cutoff_from_records(records, keep_recent_days)
        cutoff_mtime = now_beijing().replace(tzinfo=None) - timedelta(days=keep_recent_days)
        for record in records:
            path = Path(record["path"])
            if record.get("trade_date") and cutoff_trade_date:
                should_delete = str(record["trade_date"]) < cutoff_trade_date
            else:
                should_delete = datetime.fromtimestamp(path.stat().st_mtime, tz=BEIJING_TZ).replace(tzinfo=None) < cutoff_mtime
            if not should_delete:
                continue
            candidates.append(str(path))
            deleted_files += 1
            deleted_bytes += int(record["size_bytes"])
            if not dry_run:
                path.unlink(missing_ok=True)
    return {
        "backend": "filesystem",
        "dataset": dataset,
        "dry_run": dry_run,
        "matched_items": deleted_files,
        "deleted_items": 0 if dry_run else deleted_files,
        "deleted_bytes": 0 if dry_run else deleted_bytes,
        "candidate_bytes": deleted_bytes,
        "candidates": candidates[:50],
        "summary": cache_summary(),
    }


def _prune_database(dataset: str, keep_recent_days: int, dry_run: bool) -> dict[str, Any]:
    db = SessionLocal()
    try:
        latest_trade_date = db.scalar(
            select(func.max(DataCacheEntry.trade_date)).where(
                DataCacheEntry.provider == PROVIDER,
                DataCacheEntry.trade_date.is_not(None),
            )
        )
        cutoff_trade_date = None
        if latest_trade_date:
            cutoff_trade_date = (pd.to_datetime(latest_trade_date) - pd.Timedelta(days=keep_recent_days)).strftime("%Y%m%d")
        query = select(DataCacheEntry).where(DataCacheEntry.provider == PROVIDER)
        if dataset != "all":
            query = query.where(DataCacheEntry.dataset == dataset)
        if cutoff_trade_date:
            query = query.where(DataCacheEntry.trade_date < cutoff_trade_date)
        else:
            query = query.where(DataCacheEntry.updated_at < now_beijing().replace(tzinfo=None) - timedelta(days=keep_recent_days))
        entries = db.scalars(query).all()
        size = sum(int(entry.size_bytes or 0) for entry in entries)
        if not dry_run and entries:
            ids = [entry.id for entry in entries]
            db.execute(delete(DataCacheEntry).where(DataCacheEntry.id.in_(ids)))
            db.commit()
        return {
            "backend": "database",
            "dataset": dataset,
            "dry_run": dry_run,
            "matched_items": len(entries),
            "deleted_items": 0 if dry_run else len(entries),
            "deleted_bytes": 0 if dry_run else size,
            "candidate_bytes": size,
            "candidates": [f"{entry.dataset}:{entry.cache_key}" for entry in entries[:50]],
            "summary": cache_summary(),
        }
    finally:
        db.close()


def prune_cache(dataset: str, keep_recent_days: int, dry_run: bool = True) -> dict[str, Any]:
    allowed = {"all", *LOCAL_DATASETS.keys()}
    if dataset not in allowed:
        raise ValueError(f"Unsupported cache dataset: {dataset}")
    keep_recent_days = max(7, min(int(keep_recent_days), 3650))
    if active_cache_backend() == "database":
        return _prune_database(dataset, keep_recent_days, dry_run)
    return _prune_files(dataset, keep_recent_days, dry_run)


def import_filesystem_cache_to_database(overwrite: bool = False) -> dict[str, Any]:
    imported = 0
    skipped = 0
    bytes_imported = 0
    db = SessionLocal()
    try:
        paths: list[tuple[str, Path]] = []
        for dataset, directory in LOCAL_DATASETS.items():
            paths.extend((dataset, path) for path in sorted(directory.glob("*.csv")))
        paths.extend((dataset, path) for dataset, path in LOCAL_SINGLE_FILES.items() if path.exists())
        for dataset, path in paths:
            record = _file_record(dataset, path)
            cache_key = path.stem
            existing = db.scalar(
                select(DataCacheEntry).where(
                    DataCacheEntry.provider == PROVIDER,
                    DataCacheEntry.dataset == dataset,
                    DataCacheEntry.cache_key == cache_key,
                )
            )
            if existing is not None and not overwrite:
                skipped += 1
                continue
            payload = path.read_text(encoding="utf-8-sig")
            row_count = max(0, len(payload.splitlines()) - 1)
            if existing is None:
                existing = DataCacheEntry(provider=PROVIDER, dataset=dataset, cache_key=cache_key)
                db.add(existing)
            existing.trade_date = record.get("trade_date")
            existing.row_count = row_count
            existing.size_bytes = len(payload.encode("utf-8"))
            existing.payload_csv = payload
            imported += 1
            bytes_imported += existing.size_bytes
        db.commit()
        return {
            "imported": imported,
            "skipped": skipped,
            "bytes_imported": bytes_imported,
            "summary": _database_cache_summary(),
        }
    finally:
        db.close()
