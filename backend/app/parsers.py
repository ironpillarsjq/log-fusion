"""原始日志解析层：把采集端（Fluent Bit / 模拟客户端）的记录解析成可直接入库的行。

参考实现（只读，未修改）：`旧后端/log-backend/src/main/python/`
- `linux/process_logs.py`：Fluent Bit Linux JSON 的归一化入口（syslog 正则 + auditd 分支）
- `linux/audit_to_mysql_csv.py`：auditd 类型→分类映射、字段抽取、operation/action/result 归一化
- `windows/process_logs.py`、`windows/evtx_parser.py`、`windows/schema.py`：Windows 事件字段与建表列

与旧实现的关键差异（也是本次故障的修复点）：
1. **时间统一解析成 naive UTC `datetime`**。旧实现产出 ISO 字符串（`2026-10-02T09:31:59.929001Z`）
   或带时区偏移的字符串（`2026-10-02 20:03:18 +0800`），直接写 MySQL `datetime` 会报
   `(1292, "Incorrect datetime value")`，之前的实现就是这样静默失败的。
2. **按目标表真实列裁剪并做类型转换**（见 `catalog.LINUX_TABLE_FIELDS` / `WINDOWS_TABLE_FIELDS`）：
   整型列为空时写 `NULL` 而不是 `""`（否则 1366），字符串按列宽截断（否则 1406），
   被裁掉的非空字段追加进 `tips`，不丢信息。
3. **解析与入库分离**：`normalize_*` 只做纯函数解析，接口可以分别报告“解析失败”和“入库失败”。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .catalog import LINUX_TABLE_FIELDS, WINDOWS_TABLE_FIELDS

# --- 常量 ---------------------------------------------------------------------

WINDOWS_TABLE_MAP = {
    "application": "application_logs",
    "security": "security_logs",
    "setup": "setup_logs",
    "system": "system_logs",
    "forwardedevents": "forwardedevents_logs",
}
DEFAULT_WINDOWS_TABLE = "application_logs"

LINUX_INT_FIELDS = ("event_id", "pid", "ppid", "uid", "auid", "session_id", "source_port", "destination_port")
WINDOWS_INT_FIELDS = ("source_year", "event_record_id", "execution_process_id", "execution_thread_id")

# 列宽来自 information_schema 实测（docs/DATABASE.md）
LINUX_TEXT_LIMITS = {
    "category": 64, "type": 64, "operation": 255, "account": 255, "hostname": 255,
    "source_address": 255, "terminal": 255, "result": 64, "action": 255, "protocol": 64,
}
WINDOWS_TEXT_LIMITS = {
    "source_file": 500, "provider_name": 255, "provider_guid": 100, "event_id": 64,
    "event_version": 32, "level": 32, "task": 64, "opcode": 64, "keywords": 255,
    "correlation_activity_id": 100, "correlation_related_activity_id": 100,
    "channel": 255, "computer": 255, "security_user_id": 255,
}

# Linux：auditd 类型 → 分类（照搬旧实现，含 SYSCALL 的上下文判定）
CATEGORY_TYPES = {
    "authentication_session": {
        "USER_AUTH", "USER_ACCT", "CRED_ACQ", "CRED_DISP", "CRED_REFR", "USER_LOGIN",
        "USER_LOGOUT", "USER_START", "USER_END", "USER_CHAUTHTOK", "USER_ERR", "GRP_AUTH",
        "LOGIN", "CRYPTO_SESSION", "CRYPTO_KEY_USER",
    },
    "account_security_change": {
        "ADD_USER", "DEL_USER", "ADD_GROUP", "DEL_GROUP", "USER_MGMT", "GRP_MGMT",
        "CHUSER_ID", "CHGRP_ID", "ACCT_LOCK", "ACCT_UNLOCK", "USER_ROLE_CHANGE", "ROLE_ASSIGN",
        "ROLE_REMOVE", "LABEL_OVERRIDE", "LABEL_LEVEL_CHANGE", "USER_LABELED_EXPORT",
        "USER_UNLABELED_EXPORT", "DEV_ALLOC", "DEV_DEALLOC", "USER_DEVICE", "FS_RELABEL",
        "USER_MAC_POLICY_LOAD", "USER_MAC_CONFIG_CHANGE", "USER_MAC_STATUS",
    },
    "process_command_execution": {
        "SYSCALL", "EXECVE", "PROCTITLE", "CWD", "USER_CMD", "USER_TTY", "TTY", "BPRM_FCAPS",
        "CAPSET", "MMAP", "SECCOMP", "KERN_MODULE", "BPF", "OBJ_PID",
    },
    "file_object_access": {
        "SYSCALL", "PATH", "DAC_CHECK", "FS_RELABEL", "FANOTIFY", "OPENAT2", "INTEGRITY_DATA",
        "INTEGRITY_METADATA", "INTEGRITY_STATUS", "INTEGRITY_HASH", "INTEGRITY_PCR", "INTEGRITY_RULE",
        "INTEGRITY_EVM_XATTR", "INTEGRITY_POLICY_RULE",
    },
    "network_ipc_communication": {
        "SOCKADDR", "SOCKETCALL", "NETFILTER_PKT", "NETFILTER_CFG", "IPC", "IPC_SET_PERM",
        "MQ_OPEN", "MQ_SENDRECV", "MQ_NOTIFY", "MQ_GETSETATTR", "FD_PAIR",
    },
    "system_service_audit_lifecycle": {
        "SYSTEM_BOOT", "SYSTEM_SHUTDOWN", "SYSTEM_RUNLEVEL", "SERVICE_START", "SERVICE_STOP",
        "DAEMON_START", "DAEMON_END", "DAEMON_ABORT", "DAEMON_CONFIG", "DAEMON_ROTATE",
        "DAEMON_RESUME", "DAEMON_ACCEPT", "DAEMON_CLOSE", "DAEMON_ERR", "CONFIG_CHANGE", "ADD_RULE",
        "DEL_RULE", "LIST_RULES", "SET_FEATURE", "GET_FEATURE", "FEATURE_CHANGE", "TRIM", "MAKE_EQUIV",
    },
    "security_policy_config_change": {
        "CONFIG_CHANGE", "ADD_RULE", "DEL_RULE", "LIST_RULES", "SET_FEATURE", "GET_FEATURE",
        "FEATURE_CHANGE", "WATCH_INS", "WATCH_REM", "WATCH_LIST", "TRIM", "MAKE_EQUIV", "AVC",
        "AVC_PATH", "SELINUX_ERR", "MAC_POLICY_LOAD", "MAC_STATUS", "MAC_CONFIG_CHANGE", "MAC_UNLBL_ALLOW",
        "MAC_CIPSOV4_ADD", "MAC_CIPSOV4_DEL", "MAC_MAP_ADD", "MAC_MAP_DEL", "MAC_IPSEC_ADDSA",
        "MAC_IPSEC_DELSA", "MAC_IPSEC_ADDSPD", "MAC_IPSEC_DELSPD", "MAC_IPSEC_EVENT", "MAC_UNLBL_STCADD",
        "MAC_UNLBL_STCDEL", "MAC_CALIPSO_ADD", "MAC_CALIPSO_DEL", "USER_MAC_POLICY_LOAD",
        "USER_MAC_CONFIG_CHANGE", "USER_MAC_STATUS",
    },
}

TYPE_TO_CATEGORY = {
    type_name: category
    for category, type_names in CATEGORY_TYPES.items()
    for type_name in type_names
}
for _type in {"CONFIG_CHANGE", "ADD_RULE", "DEL_RULE", "LIST_RULES", "SET_FEATURE", "GET_FEATURE",
              "FEATURE_CHANGE", "TRIM", "MAKE_EQUIV"}:
    TYPE_TO_CATEGORY[_type] = "security_policy_config_change"
TYPE_TO_CATEGORY["FS_RELABEL"] = "file_object_access"
for _type in {"USER_MAC_POLICY_LOAD", "USER_MAC_CONFIG_CHANGE", "USER_MAC_STATUS"}:
    TYPE_TO_CATEGORY[_type] = "security_policy_config_change"

DEFAULT_OPERATIONS = {
    "LOGIN": "login", "USER_LOGIN": "login", "USER_LOGOUT": "logout",
    "USER_START": "session_open", "USER_END": "session_close",
    "USER_AUTH": "authentication", "USER_ACCT": "accounting",
    "CRED_ACQ": "credential_acquire", "CRED_DISP": "credential_dispose", "CRED_REFR": "credential_refresh",
    "CRYPTO_SESSION": "crypto_session", "CRYPTO_KEY_USER": "crypto_key",
    "ADD_USER": "add_user", "DEL_USER": "delete_user", "ADD_GROUP": "add_group", "DEL_GROUP": "delete_group",
    "ACCT_LOCK": "lock_account", "ACCT_UNLOCK": "unlock_account",
}
DEFAULT_ACTIONS = {
    "SERVICE_START": "start", "SERVICE_STOP": "stop", "SYSTEM_BOOT": "boot", "SYSTEM_SHUTDOWN": "shutdown",
    "SYSTEM_RUNLEVEL": "change_runlevel", "DAEMON_START": "start", "DAEMON_END": "stop",
    "DAEMON_ABORT": "abort", "DAEMON_CONFIG": "configure", "DAEMON_ROTATE": "rotate", "DAEMON_RESUME": "resume",
    "ADD_RULE": "add_rule", "DEL_RULE": "delete_rule", "LIST_RULES": "list_rules",
    "WATCH_INS": "insert_watch", "WATCH_REM": "remove_watch", "WATCH_LIST": "list_watches",
    "MAC_POLICY_LOAD": "load_policy", "USER_MAC_POLICY_LOAD": "load_policy",
    "MAC_CONFIG_CHANGE": "change_config", "USER_MAC_CONFIG_CHANGE": "change_config",
    "MAC_STATUS": "change_status", "USER_MAC_STATUS": "change_status",
    "AVC": "access_control_decision", "AVC_PATH": "identify_avc_path", "FS_RELABEL": "relabel",
}

OUTER_RE = re.compile(r"^type=(?P<type>\S+)\s+msg=audit\((?P<stamp>[^:()]+):(?P<event_id>\d+)\):(?P<body>.*)$")
PAIR_RE = re.compile(r"(?P<key>[A-Za-z0-9_.-]+)=(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s]+)")
SYSLOG_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<clock>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<process>[^:]+):\s*(?P<message>.*)$"
)
_SYSLOG_TIME_RE = re.compile(r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<clock>\d{2}:\d{2}:\d{2})")
_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S,%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d",
)

# Windows 记录里已知的键：其余键进入 system_extra
WINDOWS_KNOWN_KEYS = {
    "client_id", "Channel", "channel", "ComputerName", "computer", "Data", "StringInserts",
    "EventID", "event_id", "EventType", "EventCategory", "Qualifiers", "RecordNumber",
    "SourceName", "TimeGenerated", "TimeWritten", "date", "log_source", "Message",
    "ProviderGuid", "provider_guid", "Version", "Level", "Task", "Opcode", "Keywords",
    "EventRecordID", "ActivityID", "RelatedActivityID", "ProcessID", "ThreadID", "Sid",
    "ExecutionProcessID", "ExecutionThreadID", "event_time", "system_extra", "eventdata", "raw",
}


# --- 通用工具 -----------------------------------------------------------------

def text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


def int_or_none(value: Any) -> int | None:
    """整型列：空值必须是 NULL，不能是空字符串（否则 MySQL 报 1366）。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    matches = re.search(r"-?\d+", str(value))
    return int(matches.group(0)) if matches else None


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def stable_event_id(*parts: Any) -> int:
    """按内容生成稳定 id（旧实现用 sha256；不要用 Python hash()，它跨进程会变）。"""
    digest = hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


# --- 时间解析（本次故障的核心修复）--------------------------------------------

def _from_epoch(value: float) -> datetime | None:
    try:
        if value > 1e14:      # 微秒
            value /= 1_000_000
        elif value > 1e11:    # 毫秒
            value /= 1_000
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    """把采集端各种时间写法解析成 naive UTC datetime（入库用的类型）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{9,}(\.\d+)?", raw):     # epoch 秒/毫秒/微秒字符串
        return _from_epoch(float(raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            continue
    return None


def parse_syslog_time(line: str | None, fallback_year: int | None = None) -> datetime | None:
    """syslog 行首的 `Oct  2 17:30:01` 没有年份，用 fallback_year（或当前年）补。"""
    match = _SYSLOG_TIME_RE.match(line or "")
    if not match:
        return None
    month = _MONTHS.get(match.group("month"))
    if not month:
        return None
    year = fallback_year or datetime.now(timezone.utc).year
    hour, minute, second = (int(part) for part in match.group("clock").split(":"))
    try:
        return datetime(year, month, int(match.group("day")), hour, minute, second)
    except ValueError:
        return None


# --- Linux：auditd ------------------------------------------------------------

def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")


def parse_pairs(body: str) -> dict[str, str]:
    return {match.group("key"): _unquote(match.group("value")) for match in PAIR_RE.finditer(body)}


def parse_audit_line(line: str) -> tuple[str, str, str, dict[str, str]] | None:
    """解析 `type=EXECVE msg=audit(1696234567.123:456): argc=2 a0="ls" ...`。"""
    match = OUTER_RE.match((line or "").rstrip("\r\n"))
    if not match:
        return None
    body = match.group("body").strip()
    fields = parse_pairs(body)
    inner = re.search(r"\bmsg='(?P<msg>(?:\\.|[^'])*)'\s*$", body)
    if inner:
        fields.update(parse_pairs(inner.group("msg")))
    type_name = match.group("type")
    if type_name == "AVC":
        avc = re.search(r"\bavc:\s+(?P<decision>denied|granted)\s+\{\s*(?P<permissions>[^}]+?)\s*\}", body, re.IGNORECASE)
        if avc:
            fields["avc"] = avc.group("decision").lower()
            fields["permissions"] = " ".join(avc.group("permissions").split())
    return type_name, match.group("stamp"), match.group("event_id"), fields


def first(fields: dict[str, str], *names: str) -> str:
    for name in names:
        if fields.get(name) not in (None, ""):
            return fields[name]
    return ""


def value_or_default(fields: dict[str, str], default: str, *names: str) -> str:
    return first(fields, *names) or default


def normalize_result(value: str) -> str:
    lowered = (value or "").lower()
    if value == "1" or lowered in {"yes", "success", "ok", "true"}:
        return "success"
    if value == "0" or lowered in {"no", "failed", "fail", "false"}:
        return "failed"
    if lowered in {"allow", "allowed", "grant", "granted"}:
        return "allowed"
    if lowered in {"deny", "denied"}:
        return "denied"
    return value or ""


def category_for(type_name: str, fields: dict[str, str]) -> str | None:
    if type_name == "SYSCALL":
        syscall = first(fields, "syscall").lower()
        return "process_command_execution" if syscall in {"execve", "execveat", "59", "322"} else "file_object_access"
    return TYPE_TO_CATEGORY.get(type_name)


def tips_string(fields: dict[str, str], used: set[str]) -> str:
    items = []
    for key, value in fields.items():
        if key in used or key == "msg":
            continue
        safe = str(value).replace("\\", "\\\\").replace(";", r"\;").replace("=", r"\=")
        items.append(f"{key}={safe}")
    return ";".join(items)


def audit_row(category: str, type_name: str, stamp: str, event_id: str, fields: dict[str, str]) -> dict[str, Any]:
    """照搬旧实现的字段映射，产出 category 对应表的行。"""
    row: dict[str, Any] = {"type": type_name, "event_id": int_or_none(event_id)}
    timestamp = parse_datetime(stamp)
    row["event_time"] = timestamp

    for source, target in (("pid", "pid"), ("uid", "uid"), ("auid", "auid"), ("ses", "session_id"), ("exe", "executable")):
        row[target] = first(fields, source) or None

    if category in {"authentication_session", "account_security_change"}:
        row["operation"] = value_or_default(fields, DEFAULT_OPERATIONS.get(type_name, ""), "op") or None
    if category == "file_object_access":
        row["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "fan_type", "syscall") or None
    elif category == "network_ipc_communication":
        row["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "cmd", "hook") or None
    elif category == "system_service_audit_lifecycle":
        row["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op") or None
    elif category == "security_policy_config_change":
        row["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "avc", "event") or None

    if category == "authentication_session":
        row["account"] = first(fields, "acct") or None
    if category in {"authentication_session", "account_security_change"}:
        row["hostname"] = first(fields, "hostname") or None
        row["source_address"] = first(fields, "addr") or None
        row["terminal"] = first(fields, "terminal") or None
    if category in {"process_command_execution", "file_object_access", "network_ipc_communication"}:
        row["ppid"] = first(fields, "ppid") or None
        row["command"] = first(fields, "comm", "cmd") or None
    if category == "file_object_access":
        row["object_path"] = first(fields, "path", "name", "filename") or None
        row["object_inode"] = first(fields, "inode", "ino") or None
    if category in {"system_service_audit_lifecycle", "security_policy_config_change"}:
        row["target"] = first(fields, "unit", "feature", "policy", "bool", "rule", "path", "name", "obj") or None
    if category == "system_service_audit_lifecycle":
        row["hostname"] = first(fields, "hostname") or None
    if type_name == "EXECVE":
        args = [fields[key] for key in sorted(
            (k for k in fields if k.startswith("a") and k[1:].isdigit()),
            key=lambda k: int(k[1:]))]
        if args:
            row["command"] = " ".join(args)
    if category == "network_ipc_communication":
        row["source_address"] = first(fields, "saddr", "src", "addr") or None
        row["destination_address"] = first(fields, "daddr", "dst") or None
        row["source_port"] = int_or_none(first(fields, "sport"))
        row["destination_port"] = int_or_none(first(fields, "dport"))
        row["protocol"] = first(fields, "proto", "protocol") or None

    row["result"] = normalize_result(first(fields, "res", "success", "result", "response")) or None
    if type_name == "AVC" and not row.get("result"):
        row["result"] = "permissive" if fields.get("permissive") == "1" else (normalize_result(fields.get("avc", "")) or None)

    used = {"pid", "uid", "auid", "ses", "exe", "res", "success", "result", "response"}
    if category in {"authentication_session", "account_security_change"}:
        used.update({"op", "hostname", "addr", "terminal"})
    if category == "file_object_access":
        used.update({"op", "action", "fan_type", "syscall"})
    elif category == "network_ipc_communication":
        used.update({"op", "action", "cmd", "hook"})
    elif category == "system_service_audit_lifecycle":
        used.update({"op", "action"})
    elif category == "security_policy_config_change":
        used.update({"op", "action", "avc", "event"})
    if category == "authentication_session":
        used.add("acct")
    if category == "file_object_access":
        used.update({"path", "name", "filename", "inode", "ino"})
    if category in {"system_service_audit_lifecycle", "security_policy_config_change"}:
        used.update({"unit", "feature", "policy", "bool", "rule", "path", "name", "obj"})
    if category == "system_service_audit_lifecycle":
        used.add("hostname")
    if category in {"process_command_execution", "file_object_access", "network_ipc_communication"}:
        used.update({"ppid", "comm", "cmd"})
    if category == "network_ipc_communication":
        used.update({"saddr", "src", "addr", "daddr", "dst", "sport", "dport", "proto", "protocol"})
    if type_name == "EXECVE":
        used.update(k for k in fields if k.startswith("a") and k[1:].isdigit())
    # 注意：这里的 used 必须按分类构造。像 process_command_execution 这类没有
    # hostname/source_address 列的表，对应键不能算“已使用”，否则字段会静默丢失。
    row["tips"] = tips_string(fields, used)
    return row


def syslog_row(record: dict[str, Any], log_message: str | None, timestamp: datetime) -> dict[str, Any]:
    """普通 syslog 行（非 auditd）：归入 authentication_session。"""
    host = process = None
    message = log_message
    if log_message:
        match = SYSLOG_RE.match(log_message)
        if match:
            host = match.group("host")
            process = match.group("process")
            message = match.group("message") or None
    lowered = (log_message or "").lower()
    operation = "session_open" if "session opened" in lowered else "session_close" if "session closed" in lowered else "syslog"
    account = None
    if log_message:
        # pam/sudo 用 "for user X"；sshd 用 "Accepted password for bob from ..."
        user = re.search(r"for (?:invalid )?user ([^\s(]+)", log_message, re.IGNORECASE) \
            or re.search(r"for ([^\s(]+)(?: from |$)", log_message, re.IGNORECASE)
        if user:
            account = user.group(1)
    return {
        "event_time": timestamp,
        "event_id": stable_event_id(record.get("client_id"), timestamp.isoformat(), log_message),
        "type": "SYSLOG",
        "operation": operation,
        "account": account,
        "executable": process,
        "hostname": host,
        "result": None,
        "tips": message or log_message or "",
    }


def finalize_linux(row: dict[str, Any], table: str) -> dict[str, Any]:
    """按目标表真实列裁剪 + 类型转换；被裁掉的非空字段并入 tips。"""
    fields = LINUX_TABLE_FIELDS.get(table)
    if fields is None:
        raise ValueError(f"未知的 Linux 表：{table}")
    final: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in row.items():
        if key in fields:
            final[key] = value
        elif value not in (None, ""):
            dropped.append(f"{key}={value}")

    event_time = final.get("event_time")
    if not isinstance(event_time, datetime):
        event_time = parse_datetime(event_time)
    if event_time is None:
        raise ValueError("缺少可解析的时间戳（timestamp / event_time / syslog 行首）")
    final["event_time"] = event_time
    final["event_id"] = int_or_none(final.get("event_id")) or stable_event_id(table, event_time.isoformat(), row.get("tips"))
    final["category"] = table
    final["type"] = truncate(text(final.get("type")) or "UNKNOWN", LINUX_TEXT_LIMITS["type"])

    for key in LINUX_INT_FIELDS:
        if key in fields:
            value = int_or_none(final.get(key))
            if key in {"source_port", "destination_port"} and value is not None and value < 0:
                value = None
            final[key] = value
    for key, limit in LINUX_TEXT_LIMITS.items():
        if key in fields and final.get(key) is not None:
            final[key] = truncate(str(final[key]), limit)
    for key in ("executable", "command", "tips"):
        if key in fields and final.get(key) is not None:
            final[key] = str(final[key])
    if dropped:
        existing = final.get("tips") or ""
        extra = "; ".join(dropped)
        final["tips"] = (existing + ("; " if existing else "") + extra)[:60000]

    return {key: value for key, value in final.items() if key in fields}


def normalize_linux(record: Any) -> tuple[dict[str, Any] | None, str | None]:
    """原始记录 → 可直接插入分类表的行。返回 (row, error)。"""
    if not isinstance(record, dict):
        return None, "记录不是 JSON 对象"

    source = record.get("normalized") if isinstance(record.get("normalized"), dict) else None
    log_message = text(record.get("log"))
    fallback_year = None
    epoch = parse_datetime(record.get("date"))
    if epoch:
        fallback_year = epoch.year

    if source is not None:
        row = dict(source)
        if row.get("event_time") is None:
            row["event_time"] = record.get("timestamp") or record.get("event_time")
        category = text(row.get("category")) or "authentication_session"
        try:
            return finalize_linux(row, category), None
        except ValueError as exc:
            return None, str(exc)

    timestamp = parse_datetime(record.get("timestamp") or record.get("event_time"))
    if log_message:
        parsed = parse_audit_line(log_message)
        if parsed is not None:
            type_name, stamp, event_id, fields = parsed
            category = category_for(type_name, fields)
            if category is None:
                return None, f"未收录的 audit 类型：{type_name}"
            row = audit_row(category, type_name, stamp, event_id, fields)
            if row.get("event_time") is None:
                row["event_time"] = timestamp
            if not row.get("hostname"):
                match = SYSLOG_RE.match(log_message)
                if match:
                    row["hostname"] = match.group("host")
            try:
                return finalize_linux(row, category), None
            except ValueError as exc:
                return None, str(exc)

    if timestamp is None:
        timestamp = parse_syslog_time(log_message, fallback_year)
    if timestamp is None:
        return None, "缺少可解析的时间戳（timestamp / event_time / syslog 行首）"
    row = syslog_row(record, log_message, timestamp)
    try:
        return finalize_linux(row, "authentication_session"), None
    except ValueError as exc:
        return None, str(exc)


# --- Windows ------------------------------------------------------------------

def windows_table_for(channel: Any) -> str:
    key = (text(channel) or "application").lower().replace("-", "")
    return WINDOWS_TABLE_MAP.get(key, DEFAULT_WINDOWS_TABLE)


def normalize_windows(record: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Windows 事件 JSON → `{channel}_logs` 的行（含 eventdata / system_extra JSON）。"""
    if not isinstance(record, dict):
        return None, "记录不是 JSON 对象"

    channel_raw = text(record.get("Channel") or record.get("channel"))
    table = windows_table_for(channel_raw)

    timestamp = parse_datetime(
        record.get("TimeGenerated") or record.get("TimeWritten")
        or record.get("event_time") or record.get("date")
    )
    if timestamp is None:
        return None, "缺少可解析的时间戳（TimeGenerated / TimeWritten / date）"

    client_id = text(record.get("client_id")) or "unknown"
    data = record.get("Data", record.get("StringInserts"))
    eventdata: dict[str, Any] = {}
    if data not in (None, "", []):
        eventdata["Data"] = data
    for key in ("Message", "EventType", "EventCategory", "Qualifiers", "Sid", "log_source"):
        if record.get(key) not in (None, ""):
            eventdata[key] = record.get(key)
    if not eventdata:
        eventdata = {"Data": None}

    system_extra = {key: value for key, value in record.items() if key not in WINDOWS_KNOWN_KEYS}

    row: dict[str, Any] = {
        "source_year": timestamp.year,
        "source_file": f"fluent-bit://{client_id}/{table.replace('_logs', '')}",
        "provider_name": text(record.get("SourceName") or record.get("provider_name")),
        "provider_guid": text(record.get("ProviderGuid") or record.get("provider_guid")),
        "event_id": text(record.get("EventID") or record.get("event_id")),
        "event_version": text(record.get("Version")),
        "level": text(record.get("Level")),
        "task": text(record.get("Task")),
        "opcode": text(record.get("Opcode")),
        "keywords": text(record.get("Keywords")),
        "time_created": timestamp,
        "event_record_id": int_or_none(record.get("RecordNumber") or record.get("EventRecordID")),
        "correlation_activity_id": text(record.get("ActivityID")),
        "correlation_related_activity_id": text(record.get("RelatedActivityID")),
        "execution_process_id": int_or_none(record.get("ExecutionProcessID") or record.get("ProcessID")),
        "execution_thread_id": int_or_none(record.get("ExecutionThreadID") or record.get("ThreadID")),
        "channel": channel_raw or "Application",
        "computer": text(record.get("ComputerName") or record.get("computer")),
        "security_user_id": text(record.get("Sid")),
        "system_extra": json_text(system_extra),
        "eventdata": json_text(eventdata),
    }

    final: dict[str, Any] = {}
    for key in WINDOWS_TABLE_FIELDS:
        value = row.get(key)
        if key in WINDOWS_INT_FIELDS:
            final[key] = int_or_none(value)
        elif key in WINDOWS_TEXT_LIMITS:
            final[key] = truncate(text(value), WINDOWS_TEXT_LIMITS[key])
        else:
            final[key] = value
    final["source_file"] = truncate(final.get("source_file") or f"fluent-bit://{client_id}/unknown", 500)
    if not final.get("eventdata"):
        final["eventdata"] = json_text({"Data": None})
    if final.get("system_extra") is None:
        final["system_extra"] = json_text({})
    return final, None
