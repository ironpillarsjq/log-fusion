#!/usr/bin/env python3
"""Create seven Audit tables and import normalized CSV records into MySQL."""

from __future__ import annotations

import argparse
import csv
import getpass
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# Fill these four values, or pass host/port/user on the command line. When the
# password remains empty, the script asks for it without displaying the input.
MYSQL_HOST = "100.107.214.72"
MYSQL_PORT = "3306"
MYSQL_USER = "root"
MYSQL_PASSWORD = "mysql_YbJWfE"
MYSQL_DATABASE = "log-linux"
DEFAULT_CSV_FILE = r"C:\Users\Admin\Documents\Codex\2026-09-22\uo\work\audit_normalized_all.csv"

TABLE_COLUMNS: Dict[str, List[str]] = {
    "authentication_session": [
        "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
        "operation", "account", "executable", "hostname", "source_address", "terminal", "result", "tips",
    ],
    "account_security_change": [
        "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
        "operation", "executable", "hostname", "source_address", "terminal", "result", "tips",
    ],
    "process_command_execution": [
        "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
        "executable", "command", "result", "tips",
    ],
    "file_object_access": [
        "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
        "executable", "command", "object_path", "object_inode", "action", "result", "tips",
    ],
    "network_ipc_communication": [
        "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
        "executable", "command", "action", "source_address", "destination_address", "source_port",
        "destination_port", "protocol", "result", "tips",
    ],
    "system_service_audit_lifecycle": [
        "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
        "action", "target", "executable", "hostname", "result", "tips",
    ],
    "security_policy_config_change": [
        "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
        "action", "target", "executable", "result", "tips",
    ],
}


COLUMN_TYPES = {
    "event_time": "DATETIME(6) NOT NULL",
    "event_id": "BIGINT NOT NULL",
    "category": "VARCHAR(64) NOT NULL",
    "type": "VARCHAR(64) NOT NULL",
    "pid": "BIGINT NULL",
    "ppid": "BIGINT NULL",
    "uid": "BIGINT NULL",
    "auid": "BIGINT NULL",
    "session_id": "BIGINT NULL",
    "operation": "VARCHAR(255) NULL",
    "account": "VARCHAR(255) NULL",
    "executable": "TEXT NULL",
    "hostname": "VARCHAR(255) NULL",
    "source_address": "VARCHAR(255) NULL",
    "terminal": "VARCHAR(255) NULL",
    "command": "TEXT NULL",
    "object_path": "TEXT NULL",
    "object_inode": "BIGINT NULL",
    "action": "VARCHAR(255) NULL",
    "target": "TEXT NULL",
    "destination_address": "VARCHAR(255) NULL",
    "source_port": "INT UNSIGNED NULL",
    "destination_port": "INT UNSIGNED NULL",
    "protocol": "VARCHAR(64) NULL",
    "result": "VARCHAR(64) NULL",
    "tips": "LONGTEXT NULL",
}

INTEGER_COLUMNS = {
    "event_id", "pid", "ppid", "uid", "auid", "session_id", "object_inode",
    "source_port", "destination_port",
}


def quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def create_table_sql(table: str) -> str:
    definitions = [
        f"  {quote_identifier(column)} {COLUMN_TYPES[column]}"
        for column in TABLE_COLUMNS[table]
    ]
    definitions.extend([
        "  KEY `idx_event_time` (`event_time`)",
        "  KEY `idx_event_id` (`event_id`)",
        "  KEY `idx_type` (`type`)",
    ])
    return (
        f"CREATE TABLE IF NOT EXISTS {quote_identifier(table)} (\n"
        + ",\n".join(definitions)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )


def parse_event_time(value: str, line_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"CSV line {line_number}: invalid event_time {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"CSV line {line_number}: event_time must include a timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def convert_value(column: str, value: Optional[str], line_number: int) -> Any:
    if value is None or value == "":
        return None
    if column == "event_time":
        return parse_event_time(value, line_number)
    if column in INTEGER_COLUMNS:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(
                f"CSV line {line_number}: column {column} is not an integer: {value!r}"
            ) from exc
    return value


def validate_csv_header(fieldnames: Optional[Sequence[str]]) -> None:
    if not fieldnames:
        raise ValueError("CSV file has no header")
    required = set().union(*TABLE_COLUMNS.values())
    missing = sorted(required.difference(fieldnames))
    if missing:
        raise ValueError("CSV header is missing columns: " + ", ".join(missing))


def inspect_csv(csv_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_csv_header(reader.fieldnames)
        for line_number, row in enumerate(reader, start=2):
            category = row.get("category", "")
            if category not in TABLE_COLUMNS:
                raise ValueError(
                    f"CSV line {line_number}: unsupported category {category!r}"
                )
            for column in TABLE_COLUMNS[category]:
                convert_value(column, row.get(column), line_number)
            counts[category] += 1
    return counts


def load_mysql_connector() -> Any:
    try:
        import mysql.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency. Install it with: python -m pip install mysql-connector-python"
        ) from exc
    return mysql.connector


def connect_server(connector: Any, args: argparse.Namespace) -> Any:
    return connector.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
    )


def ensure_tables(connection: Any, database: str) -> List[str]:
    cursor = connection.cursor()
    try:
        placeholders = ",".join(["%s"] * len(TABLE_COLUMNS))
        cursor.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ({placeholders})",
            (database, *TABLE_COLUMNS.keys()),
        )
        existing = {row[0] for row in cursor.fetchall()}
        missing = [table for table in TABLE_COLUMNS if table not in existing]
        for table in missing:
            cursor.execute(create_table_sql(table))
        connection.commit()
        validate_table_columns(cursor, database)
        return missing
    finally:
        cursor.close()


def validate_table_columns(cursor: Any, database: str) -> None:
    for table, expected in TABLE_COLUMNS.items():
        cursor.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        actual = [row[0] for row in cursor.fetchall()]
        if actual != expected:
            raise RuntimeError(
                f"Table {table!r} has an incompatible schema. "
                f"Expected columns: {expected}; actual columns: {actual}"
            )


def insert_sql(table: str) -> str:
    columns = TABLE_COLUMNS[table]
    names = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {quote_identifier(table)} ({names}) VALUES ({placeholders})"


def flush_buffer(cursor: Any, table: str, rows: List[Tuple[Any, ...]]) -> None:
    if rows:
        cursor.executemany(insert_sql(table), rows)
        rows.clear()


def import_csv(connection: Any, database: str, csv_path: Path, batch_size: int) -> Counter[str]:
    cursor = connection.cursor()
    buffers: Dict[str, List[Tuple[Any, ...]]] = {table: [] for table in TABLE_COLUMNS}
    counts: Counter[str] = Counter()
    try:
        cursor.execute(f"USE {quote_identifier(database)}")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            validate_csv_header(reader.fieldnames)
            for line_number, row in enumerate(reader, start=2):
                category = row.get("category", "")
                if category not in TABLE_COLUMNS:
                    raise ValueError(
                        f"CSV line {line_number}: unsupported category {category!r}"
                    )
                values = tuple(
                    convert_value(column, row.get(column), line_number)
                    for column in TABLE_COLUMNS[category]
                )
                buffers[category].append(values)
                counts[category] += 1
                if len(buffers[category]) >= batch_size:
                    flush_buffer(cursor, category, buffers[category])
        for table, rows in buffers.items():
            flush_buffer(cursor, table, rows)
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def nonempty(value: Optional[str], label: str) -> str:
    if value and value.strip():
        return value.strip()
    raise ValueError(f"MySQL {label} is empty")


def resolve_connection_args(args: argparse.Namespace) -> None:
    args.host = nonempty(args.host, "host")
    args.user = nonempty(args.user, "user")
    if args.port is None:
        raise ValueError("MySQL port is empty")
    if not 1 <= args.port <= 65535:
        raise ValueError("MySQL port must be between 1 and 65535")
    if args.password is None:
        args.password = getpass.getpass("MySQL password: ")


def print_counts(prefix: str, counts: Mapping[str, int]) -> None:
    print(prefix)
    for category in TABLE_COLUMNS:
        print(f"  {category}={counts.get(category, 0)}")
    print(f"  total={sum(counts.values())}")


def build_parser() -> argparse.ArgumentParser:
    configured_port = int(MYSQL_PORT) if str(MYSQL_PORT).strip() else None
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
    "csv_file",
    nargs="?",
    type=Path,
    default=Path(DEFAULT_CSV_FILE),
    help="normalized UTF-8 CSV file",
)
    parser.add_argument("--host", default=MYSQL_HOST or None, help="MySQL server IP or hostname")
    parser.add_argument("--port", type=int, default=configured_port, help="MySQL port")
    parser.add_argument("--user", default=MYSQL_USER or None, help="MySQL user")
    parser.add_argument("--password", default=MYSQL_PASSWORD or None, help=argparse.SUPPRESS)
    parser.add_argument("--database", default=MYSQL_DATABASE, help="database name")
    parser.add_argument("--batch-size", type=int, default=1000, help="rows per executemany batch")
    parser.add_argument("--dry-run", action="store_true", help="validate and count CSV without MySQL")
    parser.add_argument("--create-only", action="store_true", help="only ensure the database and seven tables")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.batch_size < 1:
            raise ValueError("batch-size must be at least 1")
        if not args.create_only:
            if args.csv_file is None:
                parser.error("csv_file is required unless --create-only is used")
            if not args.csv_file.is_file():
                raise ValueError(f"CSV file does not exist: {args.csv_file}")
        if args.dry_run:
            if args.create_only:
                raise ValueError("--dry-run and --create-only cannot be used together")
            print_counts("CSV validation passed:", inspect_csv(args.csv_file))
            return 0

        resolve_connection_args(args)
        connector = load_mysql_connector()
        connection = connect_server(connector, args)
        try:
            created = ensure_tables(connection, args.database)
            if created:
                print("Created missing tables: " + ", ".join(created))
            else:
                print("All seven tables already exist.")
            print("Seven table schemas are ready.")
            if args.create_only:
                return 0
            counts = import_csv(connection, args.database, args.csv_file, args.batch_size)
            print_counts("Import completed:", counts)
            return 0
        finally:
            connection.close()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
