"""Durable local inbox and additive six-table MySQL writer. No schema creation on import."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import uuid

from sqlalchemy import text

from .device_log_catalog import TABLES
from .syslog_parsers import ParsedLog


class SpoolFull(RuntimeError):
    pass


class Inbox:
    """One receiver per inbox file; FULL synchronous commits precede parser/database processing."""
    def __init__(self, path: Path | str, max_bytes: int = 1024**3):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.max_bytes = max_bytes
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS inbox (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, ingest_id TEXT NOT NULL UNIQUE,
            product TEXT NOT NULL, source_instance TEXT NOT NULL, peer_ip TEXT NOT NULL,
            transport TEXT NOT NULL, received_at TEXT NOT NULL, timezone_name TEXT NOT NULL,
            body_base64 INTEGER NOT NULL, raw BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', error TEXT, attempts INTEGER NOT NULL DEFAULT 0)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS inbox_status_sequence ON inbox(status,sequence)")
        self.db.execute("CREATE TABLE IF NOT EXISTS inbox_usage (id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO inbox_usage SELECT 1,COALESCE(SUM(length(raw)),0) FROM inbox")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS inbox_added AFTER INSERT ON inbox BEGIN
            UPDATE inbox_usage SET bytes=bytes+length(NEW.raw) WHERE id=1; END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS inbox_removed AFTER DELETE ON inbox BEGIN
            UPDATE inbox_usage SET bytes=bytes-length(OLD.raw) WHERE id=1; END""")
        self.db.commit()

    def append(self, raw: bytes, *, product: str, source_instance: str, peer_ip: str,
               transport: str, timezone_name: str, body_base64: bool = False,
               received_at: datetime | None = None) -> str:
        identifier = str(uuid.uuid4())
        received_at = received_at or datetime.now(timezone.utc)
        with self.lock, self.db:
            # Read within the write transaction, so separate processes cannot overrun the payload budget.
            self.db.execute("BEGIN IMMEDIATE")
            used = self.db.execute("SELECT bytes FROM inbox_usage WHERE id=1").fetchone()[0]
            if used + len(raw) > self.max_bytes:
                raise SpoolFull("Syslog inbox payload budget exhausted")
            self.db.execute("""INSERT INTO inbox(ingest_id,product,source_instance,peer_ip,transport,
                received_at,timezone_name,body_base64,raw) VALUES(?,?,?,?,?,?,?,?,?)""",
                (identifier, product, source_instance, peer_ip, transport,
                 received_at.isoformat(), timezone_name, int(body_base64), raw))
        return identifier

    def pending(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM inbox WHERE status='pending' ORDER BY sequence LIMIT ?", (limit,))]

    def acknowledge(self, sequence: int):
        with self.lock, self.db:
            self.db.execute("DELETE FROM inbox WHERE sequence=? AND status='pending'", (sequence,))

    def failed(self, sequence: int, error: str, *, quarantine: bool = False):
        with self.lock, self.db:
            self.db.execute("UPDATE inbox SET status=?,error=?,attempts=attempts+1 WHERE sequence=?",
                            ("quarantine" if quarantine else "pending", error[:2000], sequence))

    def retry_quarantine(self):
        with self.lock, self.db:
            self.db.execute("UPDATE inbox SET status='pending',error=NULL WHERE status='quarantine'")

    def status(self) -> dict:
        with self.lock:
            groups = {row[0]: row[1] for row in self.db.execute("SELECT status,COUNT(*) FROM inbox GROUP BY status")}
            size = self.db.execute("SELECT bytes FROM inbox_usage WHERE id=1").fetchone()[0]
            error = self.db.execute("SELECT error FROM inbox WHERE error IS NOT NULL ORDER BY sequence DESC LIMIT 1").fetchone()
            return dict(pending=groups.get("pending", 0), quarantine=groups.get("quarantine", 0),
                        payloadBytes=size, maxPayloadBytes=self.max_bytes, lastError=error[0] if error else None)

    def close(self):
        with self.lock:
            self.db.close()


class MySQLDeviceStore:
    def __init__(self, engine):
        self.engine = engine

    def write(self, parsed: ParsedLog):
        if parsed.table not in TABLES or not set(parsed.row) <= set(TABLES[parsed.table]):
            raise ValueError("unknown Syslog table/columns")
        required = ("ingest_id", "raw_bytes", "raw_sha256", "raw_log")
        if any(key not in parsed.row for key in required):
            raise ValueError("missing original message/identity")
        names = ",".join(f"`{key}`" for key in parsed.row)
        parameters = ",".join(f":{key}" for key in parsed.row)
        with self.engine.begin() as connection:
            # Only the stable ingest identity deduplicates inbox replays; payload hash is not unique.
            connection.execute(text(f"INSERT INTO `{parsed.table}` ({names}) VALUES ({parameters}) "
                                    "ON DUPLICATE KEY UPDATE `ingest_id`=`ingest_id`"), parsed.row)
            stored = connection.execute(text(f"SELECT raw_sha256 FROM `{parsed.table}` WHERE ingest_id=:ingest_id"),
                                        {"ingest_id": parsed.row["ingest_id"]}).scalar_one()
            if stored != parsed.row["raw_sha256"]:
                raise ValueError("ingest_id conflict with different original message")
