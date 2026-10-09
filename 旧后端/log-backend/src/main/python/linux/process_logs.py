#!/usr/bin/env python3
"""Normalize Fluent Bit Linux JSON from stdin to JSON on stdout."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_to_mysql_csv import FIELDNAMES
from audit_to_mysql_csv import normalize as audit_normalize
from audit_to_mysql_csv import parse_line as parse_audit_line


SYSLOG_RE = re.compile(r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<clock>\d{2}:\d{2}:\d{2})\s+(?P<host>\S+)\s+(?P<process>[^:]+):\s*(?P<message>.*)$")


def text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def event_time(record: dict[str, Any]) -> str | None:
    value = text(record.get("timestamp"))
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except ValueError:
        return None


def generic_auth_record(record: dict[str, Any], log_message: str | None, timestamp: str | None) -> dict[str, Any]:
    digest = hashlib.sha256(
        (f"{record.get('client_id', '')}|{timestamp or ''}|{log_message or ''}").encode("utf-8")
    ).digest()
    synthetic_id = str(int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF)
    normalized = {name: "" for name in FIELDNAMES}
    normalized.update(
        event_time=timestamp,
        event_id=synthetic_id,
        category="authentication_session",
        type="SYSLOG",
        operation="session_open" if log_message and "session opened" in log_message.lower()
        else "session_close" if log_message and "session closed" in log_message.lower()
        else "syslog",
        hostname=None,
        tips=log_message or "",
    )
    if log_message:
        user = re.search(r"for user ([^\s(]+)", log_message, re.IGNORECASE)
        if user:
            normalized["account"] = user.group(1)
    return normalized


def normalize(record: dict[str, Any]) -> dict[str, Any]:
    log_message = text(record.get("log"))
    host = None
    process = None
    message = log_message
    if log_message:
        match = SYSLOG_RE.match(log_message)
        if match:
            host = match.group("host")
            process = match.group("process")
            message = match.group("message") or None
    source = text(record.get("source"))
    timestamp = event_time(record)
    audit = {}
    audit_type = None
    audit_event_id = None
    if log_message:
        parsed = parse_audit_line(log_message)
        if parsed:
            audit_type, stamp, audit_event_id, fields = parsed
            audit = audit_normalize(audit_type, stamp, audit_event_id, fields) or {}
    normalized = audit or generic_auth_record(record, log_message, timestamp)
    if host and not normalized.get("hostname"):
        normalized["hostname"] = host
    return {
        "client_id": text(record.get("client_id")),
        "event_time": audit.get("event_time") or timestamp,
        "os": text(record.get("os")) or "linux",
        "source": source,
        "file": text(record.get("file")),
        "collector": text(record.get("collector")),
        "host": host,
        "process": process,
        "category": normalized.get("category") or source or "linux",
        "event_id": normalized.get("event_id") or audit_event_id,
        "audit_type": audit_type,
        "operation": normalized.get("operation"),
        "command": normalized.get("command"),
        "result": normalized.get("result"),
        "message": message,
        "normalized": normalized,
        "raw": record,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise ValueError("request body must be a JSON object or array")
        result = [normalize(item) for item in payload if isinstance(item, dict)]
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        return 0
    except Exception as exc:
        print(f"linux log processing failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
