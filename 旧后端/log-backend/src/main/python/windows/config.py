"""Configuration loaded from environment variables.

Set DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME before running.
The defaults are intentionally harmless placeholders.
"""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIRS = [
    ROOT_DIR / "2008", ROOT_DIR / "2016",
    ROOT_DIR / "2019", ROOT_DIR / "2022",
]


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = os.getenv("DB_HOST", "192.168.5.6")
    port: int = int(os.getenv("DB_PORT", "3306"))
    user: str = os.getenv("DB_USER", "root")
    password: str = os.getenv("DB_PASSWORD", "mysql_YbJWfE")
    database: str = os.getenv("DB_NAME", "windows_logs")
    charset: str = os.getenv("DB_CHARSET", "utf8mb4")


DB_CONFIG = DatabaseConfig()
TABLE_PREFIX = os.getenv("TABLE_PREFIX", "")
CATEGORIES = ("Application", "Security", "Setup", "System", "ForwardedEvents")
