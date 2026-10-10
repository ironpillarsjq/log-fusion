"""测试夹具：环境隔离与数据库硬隔离。

两条硬约束（对应 docs/TODO.md 技术债条目「不连接生产库」）：

1. 在导入 `app.*` 之前把连接参数改写成不可用的占位值，避免任何用例意外使用
   生产/开发库凭据。SQLAlchemy Engine 是惰性连接，只要用例不真正执行 SQL 就
   不会发起连接。
2. 用类级 patch 直接禁用 `sqlalchemy.engine.Engine.connect/begin`：任何漏掉
   monkeypatch 的用例都会立刻抛 AssertionError，而不是静默访问真实数据库。
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# --- 必须在导入 app.* 之前完成（直接赋值，不用 setdefault，确保不可被外部环境绕过）---
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "3307"
os.environ["DB_USER"] = "test"
os.environ["DB_PASSWORD"] = "test"
os.environ["LINUX_DB"] = "test_linux"
os.environ["WINDOWS_DB"] = "test_windows"
os.environ["LOG_FUSION_DB"] = "test_fusion"


def pytest_configure(config):
    config.addinivalue_line("markers", "no_db: 该用例不访问数据库")


@pytest.fixture(autouse=True)
def forbid_database(monkeypatch):
    """任何真实数据库访问都直接失败，保证测试可在无 MySQL 环境反复运行。"""
    from sqlalchemy.engine import Engine

    def _forbidden(*args, **kwargs):
        raise AssertionError("测试禁止访问数据库；请 monkeypatch 数据访问层")

    monkeypatch.setattr(Engine, "connect", _forbidden)
    monkeypatch.setattr(Engine, "begin", _forbidden)


@pytest.fixture(autouse=True)
def no_schema_ddl(monkeypatch):
    """即使误触 lifespan，也不对 log_fusion 执行 create_all。"""
    from app import main

    monkeypatch.setattr(main, "_ensure_tables", lambda: None)
    return None
