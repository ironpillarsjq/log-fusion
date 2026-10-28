"""Bounded byte framing and RFC3164/RFC5424 envelope extraction, independent of products."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import re
from zoneinfo import ZoneInfo


class ProtocolError(ValueError):
    pass


def device_timezone(name: str) -> tzinfo:
    if name.upper() in ("UTC", "Z"):
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", name)
    if match:
        hours, minutes = int(match[2]), int(match[3])
        if hours > 23 or minutes > 59:
            raise ValueError("invalid timezone offset")
        return timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[1] == "+" else -1)))
    return ZoneInfo(name)


def event_datetime(value, zone: tzinfo) -> datetime:
    # No implicit local-machine timezone or seconds/milliseconds guessing.
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing event timestamp")
    raw = value.strip().replace("\u00a0", " ")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


class TCPFramer:
    """octet: length SP message; line: LF/CRLF; auto detects per frame. Never guesses EOF."""
    def __init__(self, max_bytes: int = 1024 * 1024, mode: str = "auto"):
        if max_bytes < 1 or mode not in ("auto", "octet", "line"):
            raise ValueError("invalid framing configuration")
        self.max_bytes, self.mode = max_bytes, mode
        self.buffer = bytearray()

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        while self.buffer:
            octet = self.mode == "octet" or (self.mode == "auto" and 48 <= self.buffer[0] <= 57)
            if octet:
                space = self.buffer.find(b" ")
                if space < 0:
                    if len(self.buffer) > 10 or not self.buffer.isdigit():
                        raise ProtocolError("invalid TCP length prefix")
                    break
                prefix = self.buffer[:space]
                if not prefix.isdigit() or prefix.startswith(b"0") or len(prefix) > 10:
                    raise ProtocolError("invalid TCP length prefix")
                length = int(prefix)
                if length > self.max_bytes:
                    raise ProtocolError("TCP message exceeds max_bytes")
                end = space + 1 + length
                if len(self.buffer) < end:
                    break
                message = bytes(self.buffer[space + 1:end])
            else:
                newline = self.buffer.find(b"\n")
                if newline < 0:
                    if len(self.buffer) > self.max_bytes + 1:
                        raise ProtocolError("TCP message exceeds max_bytes")
                    break
                end = newline + 1
                message = bytes(self.buffer[:newline])
                if message.endswith(b"\r"):
                    message = message[:-1]
                if len(message) > self.max_bytes:
                    raise ProtocolError("TCP message exceeds max_bytes")
            del self.buffer[:end]
            if message:
                yield message

    def finish(self):
        if self.buffer:
            raise ProtocolError("incomplete TCP frame at disconnect")


@dataclass(frozen=True)
class Envelope:
    body: str
    metadata: dict


RFC3164 = re.compile(r"^(?P<stamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(?P<host>\S+)\s+(?P<body>.*)$", re.S)


def envelope(text: str, received_at: datetime, zone: tzinfo) -> Envelope:
    body, meta = text, {}
    priority = re.match(r"^<(\d{1,3})>", body)
    if priority:
        pri = int(priority[1])
        if pri > 191:
            raise ProtocolError("invalid Syslog priority")
        meta.update(priority=pri, facility=pri // 8, severity=pri % 8)
        body = body[priority.end():]
    elif body.startswith("<-1>"):
        # The old DAS manual contains this non-standard PRI. Preserve it, without inventing severity.
        meta["invalid_priority"] = "<-1>"
        body = body[4:]
    if priority and re.match(r"^\d+ ", body):
        parts = body.split(" ", 6)
        if len(parts) != 7 or parts[0] != "1":
            raise ProtocolError("unsupported/malformed RFC5424 header")
        version, stamp, host, app, process, msgid, tail = parts
        meta.update(format="rfc5424", timestamp=None if stamp == "-" else stamp,
                    hostname=None if host == "-" else host, app_name=app, procid=process, msgid=msgid)
        if tail.startswith("-") and (len(tail) == 1 or tail[1] == " "):
            sd, body = "-", tail[2:] if len(tail) > 1 else ""
        elif tail.startswith("["):
            pos = 0
            while pos < len(tail) and tail[pos] == "[":
                pos += 1
                quoted = escaped = False
                while pos < len(tail):
                    char = tail[pos]
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = not quoted
                    elif char == "]" and not quoted:
                        pos += 1
                        break
                    pos += 1
                else:
                    raise ProtocolError("unterminated RFC5424 structured data")
            if pos < len(tail) and tail[pos] != " ":
                raise ProtocolError("invalid RFC5424 structured data separator")
            sd, body = tail[:pos], tail[pos + 1:] if pos < len(tail) else ""
        else:
            raise ProtocolError("missing RFC5424 structured data")
        meta["structured_data"] = sd
    else:
        match = RFC3164.match(body)
        if match:
            meta.update(format="rfc3164", hostname=match["host"])
            # Choose nearest year around reception to handle New Year's late delivery.
            local = received_at.replace(tzinfo=timezone.utc).astimezone(zone)
            candidates = []
            for year in (local.year - 1, local.year, local.year + 1):
                try:
                    candidates.append(datetime.strptime(f'{year} {match["stamp"]}', "%Y %b %d %H:%M:%S").replace(tzinfo=zone))
                except ValueError:
                    pass
            if not candidates:
                raise ProtocolError("invalid RFC3164 timestamp")
            chosen = min(candidates, key=lambda item: abs((item - local).total_seconds()))
            meta["timestamp"] = chosen.isoformat()
            body = match["body"]
        else:
            meta["format"] = "vendor"
        # Vendor examples may contain hostname/program but omit the date/PRI.
        prefix = re.match(r"^(?:(\S+)\s+)?([A-Za-z0-9_.-]+)(?:\[\d+\])?:\s+", body)
        if prefix:
            if prefix[1] and "hostname" not in meta:
                meta["hostname"] = prefix[1]
            meta["app_name"] = prefix[2]
            body = body[prefix.end():]
        elif re.match(r"^\S+\s+tag:", body):
            host, body = body.split(None, 1)
            meta.setdefault("hostname", host)
    return Envelope(body.lstrip("\ufeff"), meta)
