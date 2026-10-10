from collections.abc import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import get_settings

settings = get_settings()
linux_engine = create_engine(settings.linux_url, pool_pre_ping=True, pool_recycle=1800)
windows_engine = create_engine(settings.windows_url, pool_pre_ping=True, pool_recycle=1800)
monitor_engine = create_engine(settings.log_fusion_url, pool_pre_ping=True, pool_recycle=1800)


class Base(DeclarativeBase):
    pass


MonitorSessionLocal = sessionmaker(bind=monitor_engine, autoflush=False, expire_on_commit=False)


def get_monitor_db() -> Generator[Session, None, None]:
    db = MonitorSessionLocal()
    try:
        yield db
    finally:
        db.close()


def engine_for(platform: str) -> Engine:
    return windows_engine if platform.lower() == "windows" else linux_engine


def rows(engine: Engine, sql: str, params: dict | None = None) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def one(engine: Engine, sql: str, params: dict | None = None) -> dict | None:
    result = rows(engine, sql, params)
    return result[0] if result else None


def db_health() -> dict:
    result = {}
    for name, engine in (("linux_logs", linux_engine), ("windows_logs", windows_engine), ("log_fusion", monitor_engine)):
        try:
            with engine.connect() as conn:
                result[name] = bool(conn.execute(text("SELECT 1")).scalar())
        except Exception:
            result[name] = False
    return result
