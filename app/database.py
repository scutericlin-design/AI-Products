from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.database_url, future=True, **engine_kwargs)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        with engine.begin() as connection:
            user_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)").all()}
            if "role" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'customer'")
            if "status" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN status VARCHAR(32) DEFAULT 'active'")
            if "plan" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN plan VARCHAR(32) DEFAULT 'free'")
            if "plan_expires_at" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN plan_expires_at DATETIME")
            if "feature_flags_json" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN feature_flags_json TEXT")
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(strategy_configs)").all()}
            if "market_scope" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE strategy_configs ADD COLUMN market_scope VARCHAR(128) DEFAULT 'main,chinext,star'"
                )
            if "strategy_type" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE strategy_configs ADD COLUMN strategy_type VARCHAR(64) DEFAULT 'short_elastic_2_8w'"
                )
            if "include_beijing" not in columns:
                connection.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN include_beijing INTEGER DEFAULT 0")
            if "strategy_profiles_json" not in columns:
                connection.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN strategy_profiles_json TEXT")
