#!/usr/bin/env python3
"""Normalize Fluent Bit Windows winlog JSON from stdin to JSON on stdout."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def event_time(record: dict[str, Any]) -> str | None:
    value = text(record.get("TimeGenerated") or record.get("TimeWritten"))
    if not value:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, pattern)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except ValueError:
        return None


def integer(value: Any) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(record: dict[str, Any]) -> dict[str, Any]:
    channel = text(record.get("Channel"))
    return {
        "client_id": text(record.get("client_id")),
        "event_time": event_time(record),
        "event_id": text(record.get("EventID")),
        "channel": channel,
        "source_name": text(record.get("SourceName")),
        "computer_name": text(record.get("ComputerName")),
        "event_type": text(record.get("EventType")),
        "record_number": integer(record.get("RecordNumber")),
        "qualifiers": text(record.get("Qualifiers")),
        "data": text(record.get("Data")),
        "message": text(record.get("Message")),
        "log_source": text(record.get("log_source")) or (channel.lower() if channel else None),
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
        print(f"windows log processing failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
