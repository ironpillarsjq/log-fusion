"""DDL for the five Windows event log categories."""
from .config import CATEGORIES, DB_CONFIG, TABLE_PREFIX
from .db import connection

SYSTEM_COLUMNS = [
    ("provider_name", "VARCHAR(255)"), ("provider_guid", "VARCHAR(100)"),
    ("event_id", "VARCHAR(64)"), ("event_version", "VARCHAR(32)"),
    ("level", "VARCHAR(32)"), ("task", "VARCHAR(64)"),
    ("opcode", "VARCHAR(64)"), ("keywords", "VARCHAR(255)"),
    ("time_created", "DATETIME(6) NULL"), ("event_record_id", "BIGINT NULL"),
    ("correlation_activity_id", "VARCHAR(100)"),
    ("correlation_related_activity_id", "VARCHAR(100)"),
    ("execution_process_id", "BIGINT NULL"), ("execution_thread_id", "BIGINT NULL"),
    ("channel", "VARCHAR(255)"), ("computer", "VARCHAR(255)"),
    ("security_user_id", "VARCHAR(255)"), ("system_extra", "JSON"),
]


def table_name(category):
    return f"{TABLE_PREFIX}{category.lower()}_logs"


def create_database_and_tables():
    with connection(database=False) as conn:
        with conn.cursor() as cur:
            db = DB_CONFIG.database.replace("`", "``")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
    with connection(database=True) as conn:
        with conn.cursor() as cur:
            for category in CATEGORIES:
                columns = ["id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY",
                           "source_year SMALLINT NULL", "source_file VARCHAR(500) NOT NULL"]
                columns += [f"{name} {kind}" for name, kind in SYSTEM_COLUMNS]
                columns += ["eventdata JSON NOT NULL", "imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
                            "INDEX idx_time_created (time_created)", "INDEX idx_event_id (event_id)"]
                cur.execute(f"CREATE TABLE IF NOT EXISTS `{table_name(category)}` (" + ",".join(columns) + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        conn.commit()
