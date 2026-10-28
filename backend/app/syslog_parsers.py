"""Product-native WAF/DAS parsing. Original frame bytes/text are never trimmed or reserialized."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import re
import uuid

from .device_log_catalog import BUSINESS, COMMON, DAS_TYPES, TABLES, WAF_TYPES
from .syslog_protocol import device_timezone, envelope, event_datetime

PARSER_VERSION = "syslog-1"


class ParseError(ValueError):
    """Unclassifiable/undecodable message: preserve it in the receiver quarantine."""


@dataclass
class ParsedLog:
    table: str
    row: dict


def json_text(value) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # JSON escapes can encode unpaired surrogates even when the incoming bytes are valid UTF-8.
    serialized.encode("utf-8", errors="strict")
    return serialized


def _json_object(text: str) -> dict:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ParseError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ParseError("non-finite JSON number: " + value)

    result = json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ParseError("DAS message must be one JSON object")
    pending = [(result, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, (dict, list)):
            if depth > 64:
                raise ParseError("DAS JSON nesting exceeds 64")
            children = node.values() if isinstance(node, dict) else node
            pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return result


def _pairs(body: str, keys, warnings: list[str]) -> dict:
    pattern = re.compile(r"(?<!\S)(" + "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True)) + r")\s*:\s*", re.I)
    matches = list(pattern.finditer(body))
    result = {}
    for index, match in enumerate(matches):
        key = match[1].lower()
        if key in result:
            raise ParseError("ambiguous repeated WAF field: " + key)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[key] = body[match.end():end].strip()
    if not result:
        raise ParseError("WAF key:value fields not found")
    return result


ECLF = re.compile(r'^(\S+)\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"([^"\r\n]*)"\s+(\S+)\s+(\S+)\s+"([^"\r\n]*)"\s+"([^"\r\n]*)"\s*$', re.S)


def _waf(body: str, warnings: list[str]) -> tuple[str, dict]:
    tag = re.search(r"(?<!\S)tag:\s*(waf_log_[A-Za-z0-9_]+)(?=\s|$)", body)
    if not tag or tag[1] not in WAF_TYPES:
        raise ParseError("unknown or missing WAF tag")
    table = WAF_TYPES[tag[1]]
    tail = body[tag.end():].strip()
    if tag[1] == "waf_log_webaccess" and not re.match(r"(?:site_id|protect_id|stat_time):", tail):
        clf = ECLF.fullmatch(tail)
        if not clf:
            raise ParseError("malformed WAF ECLF access log")
        request = clf[3].split(" ", 2)
        if len(request) != 3:
            raise ParseError("malformed ECLF request line")
        return table, dict(tag=tag[1], src_ip=clf[1], stat_time=clf[2], method=request[0],
                           url=request[1], http_protocol=request[2], waf_status_code=clf[4],
                           res_content_len=clf[5], wa_referer=clf[6], agent=clf[7])
    keys = set(BUSINESS[table]) | {"tag", "stat_time", "password", "charaters", "api_event_typ"}
    payload = _pairs(body[tag.start():], keys, warnings)
    if payload.get("tag") != tag[1]:
        raise ParseError("invalid WAF tag value")
    return table, payload


LEGACY_KEYS = {
    "数据库名称": "database_name", "规则类型": "rule_type", "风险级别": "risk_level",
    "命中规则": "rule_name", "捕获时间": "capture_time", "服务器IP": "server_ip",
    "服务器端口": "server_port", "服务名（实例名）": "db_name", "客户端IP": "client_ip",
    "客户端端口": "client_port", "客户端MAC地址": "client_mac",
    "数据库用户名": "db_username", "操作系统用户名": "os_username", "SQL语句": "sql_statement",
}


def _das(body: str, warnings: list[str]) -> tuple[str, dict]:
    if body.lstrip().startswith("[CEF]:"):
        pattern = re.compile(r"\|(" + "|".join(map(re.escape, LEGACY_KEYS)) + r")[:：]")
        matches = list(pattern.finditer(body))
        payload = {"log_type": "audit", "format": "das_legacy_cef", "alarm_mode": "1"}
        for index, match in enumerate(matches):
            key = LEGACY_KEYS[match[1]]
            if key in payload:
                raise ParseError("ambiguous repeated legacy DAS field: " + key)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            payload[key] = body[match.end():end]
        if len(matches) < 2 or "sql_statement" not in payload:
            raise ParseError("unrecognized legacy DAS alarm")
        warnings.append("legacy DAS alarm; rule name is not a rule ID; no original event ID")
        return DAS_TYPES["audit"], payload
    try:
        payload = _json_object(body)
    except (ValueError, TypeError) as exc:
        raise ParseError("invalid DAS JSON: " + str(exc)) from exc
    kind = payload.get("log_type")
    if not isinstance(kind, str) or kind not in DAS_TYPES:
        raise ParseError("DAS log_type must be audit/session/warn; numeric variants require confirmed device samples")
    return DAS_TYPES[kind], payload


NULL_MARKERS = ("", "-", "None", "NULL", "null")


def _column_value(key: str, value, kind: str):
    if value is None or (isinstance(value, str) and value in NULL_MARKERS):
        return None
    if kind == "JSON":
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list):
            raise ValueError("expected JSON array")
        return json_text(value)
    if key in ("src_ip", "dst_ip", "client_ip", "server_ip", "raw_client_ip", "device_ip", "def_ip", "web_client_ip"):
        return str(ipaddress.ip_address(str(value).strip()))
    if "INT" in kind:
        if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
            raise ValueError("expected unsigned integer")
        number = int(value)
        high = 65535 if kind.startswith("SMALLINT") else 255 if kind.startswith("TINYINT") else 2**64 - 1
        if number > high or (key == "is_api" and number > 1):
            raise ValueError("integer out of range")
        return number
    if kind.startswith("DECIMAL"):
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("expected decimal") from exc
        precision, scale = map(int, re.findall(r"\d+", kind))
        if not number.is_finite() or number < 0 or number >= Decimal(10)**(precision - scale):
            raise ValueError("decimal out of range")
        if number.as_tuple().exponent < -scale:
            raise ValueError("decimal scale exceeded")
        if key in ("cpu", "mem") and number > 100:
            raise ValueError("percentage exceeds 100")
        return number
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("expected scalar text")
    text = str(value)
    width = re.fullmatch(r"VARCHAR\((\d+)\)", kind)
    if width and len(text) > int(width[1]):
        raise ValueError("column width exceeded; full value retained in payload/raw_log")
    if kind == "TEXT" and len(text.encode("utf-8")) > 65535:
        raise ValueError("TEXT byte limit exceeded; full value retained in payload/raw_log")
    return text


def parse_message(raw: bytes, *, product: str, source_instance: str, peer_ip: str,
                  transport: str, received_at: datetime | None = None,
                  ingest_id: str | None = None, timezone_name: str = "+08:00",
                  body_base64: bool = False) -> ParsedLog:
    if product not in ("waf", "das"):
        raise ValueError("product must be waf or das")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", source_instance):
        raise ValueError("invalid source_instance")
    if transport not in ("UDP", "TCP"):
        raise ValueError("transport must be UDP or TCP")
    peer_ip = str(ipaddress.ip_address(peer_ip))
    ingest_id = str(uuid.UUID(ingest_id)) if ingest_id else str(uuid.uuid4())
    received = received_at or datetime.now(timezone.utc)
    if received.tzinfo:
        received = received.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ParseError("invalid UTF-8; original bytes retained in quarantine") from exc
    zone = device_timezone(timezone_name)
    env = envelope(text, received, zone)
    body = env.body
    if body_base64:
        try:
            body = base64.b64decode(body.strip(), validate=True).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise ParseError("invalid base64 UTF-8 message body") from exc
    warnings: list[str] = []
    if env.metadata.get("invalid_priority"):
        warnings.append("non-standard legacy PRI <-1>; facility/severity unknown")
    table, payload = _waf(body, warnings) if product == "waf" else _das(body, warnings)
    values = dict(payload)
    if product == "waf":
        for alias, key in (("charaters", "characters"), ("api_event_typ", "api_event_type")):
            if alias in values and key not in values:
                values[key] = values[alias]
        if isinstance(values.get("src_ip"), str) and "," in values["src_ip"]:
            if payload["tag"] != "waf_log_ddos":
                warnings.append("src_ip: unexpected multiple IPs")
            else:
                values["src_ips"] = [item.strip() for item in values["src_ip"].split(",")]
                try:
                    values["src_ips"] = [str(ipaddress.ip_address(item)) for item in values["src_ips"]]
                    values["src_ip"] = None
                except ValueError:
                    warnings.append("src_ips: invalid IP in list")
                    values["src_ips"] = None
    else:
        if "table_names" not in values and "table_name" in values:
            values["table_names"] = values["table_name"]
        duration = values.get("duration")
        if duration is not None:
            match = re.fullmatch(r"(\d+)(?:us|µs)?", str(duration).strip())
            if match:
                values["duration_us"] = match[1]
            elif str(duration) not in NULL_MARKERS:
                warnings.append("duration: expected integer microseconds")
        if payload["log_type"] == "session" and not any(key in payload for key in ("ssid", "ss_action", "ss_result")):
            warnings.append("session fields missing; vendor template requires verification")
    timestamp = payload.get("stat_time" if product == "waf" else "capture_time")
    event_time = None
    if timestamp not in (None, ""):
        try:
            event_time = event_datetime(timestamp, zone)
        except (ValueError, TypeError, OverflowError):
            warnings.append("invalid product event timestamp; not replaced with receive time")
    elif env.metadata.get("timestamp"):
        timestamp = env.metadata["timestamp"]
        try:
            event_time = event_datetime(timestamp, zone)
            warnings.append("product timestamp missing; using Syslog header timestamp")
        except ValueError:
            warnings.append("invalid Syslog header timestamp")
    else:
        warnings.append("event timestamp missing")
    row = {
        "ingest_id": ingest_id, "source_instance": source_instance, "peer_ip": peer_ip,
        "transport": transport, "log_type": payload["tag"] if product == "waf" else payload["log_type"],
        "event_time": event_time, "event_time_raw": str(timestamp) if timestamp is not None else None,
        "received_at": received, "syslog_meta": json_text(env.metadata),
        "raw_log": text, "raw_bytes": raw, "raw_sha256": hashlib.sha256(raw).digest(),
        "payload": json_text(payload), "parser_version": PARSER_VERSION,
    }
    for key, kind in {**BUSINESS[table], "device_id": COMMON["device_id"],
                      "device_ip": COMMON["device_ip"], "device_hostname": COMMON["device_hostname"]}.items():
        value = env.metadata.get("hostname") if key == "device_hostname" else values.get(key)
        try:
            row[key] = _column_value(key, value, kind)
        except (ValueError, TypeError, OverflowError):
            row[key] = None
            warnings.append(key + ": invalid value; original retained")
    row.update(parse_status="partial" if warnings else "ok", parse_error="; ".join(warnings) or None)
    assert set(row) <= set(TABLES[table])
    return ParsedLog(table, row)


def decoded_waf_fields(payload: dict) -> dict:
    """Optional detail-view helper. Keep base64 fields untouched in stored records."""
    result = {}
    keys = ["wci", "wsi"]
    if payload.get("tag") == "waf_log_websec":
        keys += ["http", "alertinfo", "characters", "charaters"]
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or value in NULL_MARKERS:
            continue
        try:
            result[key] = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            result[key] = None
    return result
