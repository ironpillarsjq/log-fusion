"""MySQL connection helpers."""
from contextlib import contextmanager
from .config import DB_CONFIG

try:
    import pymysql
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("请先运行: pip install -r scripts/requirements.txt") from exc


def connect(database=True):
    params = {
        "host": DB_CONFIG.host, "port": DB_CONFIG.port,
        "user": DB_CONFIG.user, "password": DB_CONFIG.password,
        "charset": DB_CONFIG.charset, "autocommit": False,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if database:
        params["database"] = DB_CONFIG.database
    return pymysql.connect(**params)


@contextmanager
def connection(database=True):
    conn = connect(database=database)
    try:
        yield conn
    finally:
        conn.close()
