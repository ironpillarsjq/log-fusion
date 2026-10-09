#!/usr/bin/env python3
"""Convert Linux Audit records to a MySQL-friendly UTF-8 CSV file.

Each input line produces at most one output row. Records are never merged by
event_id; event_id is retained only as the Audit correlation identifier.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# VS Code 直接运行时使用这里的路径。需要同时处理轮转日志时，把路径继续
# 添加到列表中即可。Windows 路径使用原始字符串，避免反斜杠转义。
INPUT_FILES = [
    r"C:\Users\Admin\Downloads\log-export\log-export\audit\audit.log",
    r"C:\Users\Admin\Downloads\log-export\log-export\audit\audit.log.1",
    r"C:\Users\Admin\Downloads\log-export\log-export\audit\audit.log.2",
    r"C:\Users\Admin\Downloads\log-export\log-export\audit\audit.log.3",
    r"C:\Users\Admin\Downloads\log-export\log-export\audit\audit.log.4",
]
OUTPUT_FILE = r"C:\Users\Admin\Documents\Codex\2026-09-22\uo\work\audit_normalized_all.csv"


FIELDNAMES = [
    "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid",
    "session_id", "operation", "account", "executable", "hostname", "source_address",
    "terminal", "command", "object_path", "object_inode", "action", "target",
    "destination_address", "source_port", "destination_port", "protocol", "result", "tips",
]

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

# Types occurring in more than one document use the more specific category for
# the type itself. SYSCALL requires contextual classification and is handled below.
TYPE_TO_CATEGORY = {
    type_name: category
    for category, type_names in CATEGORY_TYPES.items()
    for type_name in type_names
}
for _type in {"CONFIG_CHANGE", "ADD_RULE", "DEL_RULE", "LIST_RULES", "SET_FEATURE", "GET_FEATURE", "FEATURE_CHANGE", "TRIM", "MAKE_EQUIV"}:
    TYPE_TO_CATEGORY[_type] = "security_policy_config_change"
TYPE_TO_CATEGORY["FS_RELABEL"] = "file_object_access"
for _type in {"USER_MAC_POLICY_LOAD", "USER_MAC_CONFIG_CHANGE", "USER_MAC_STATUS"}:
    TYPE_TO_CATEGORY[_type] = "security_policy_config_change"

DEFAULT_OPERATIONS = {
    "LOGIN": "login",
    "USER_LOGIN": "login",
    "USER_LOGOUT": "logout",
    "USER_START": "session_open",
    "USER_END": "session_close",
    "USER_AUTH": "authentication",
    "USER_ACCT": "accounting",
    "CRED_ACQ": "credential_acquire",
    "CRED_DISP": "credential_dispose",
    "CRED_REFR": "credential_refresh",
    "CRYPTO_SESSION": "crypto_session",
    "CRYPTO_KEY_USER": "crypto_key",
    "ADD_USER": "add_user",
    "DEL_USER": "delete_user",
    "ADD_GROUP": "add_group",
    "DEL_GROUP": "delete_group",
    "ACCT_LOCK": "lock_account",
    "ACCT_UNLOCK": "unlock_account",
}

DEFAULT_ACTIONS = {
    "SERVICE_START": "start",
    "SERVICE_STOP": "stop",
    "SYSTEM_BOOT": "boot",
    "SYSTEM_SHUTDOWN": "shutdown",
    "SYSTEM_RUNLEVEL": "change_runlevel",
    "DAEMON_START": "start",
    "DAEMON_END": "stop",
    "DAEMON_ABORT": "abort",
    "DAEMON_CONFIG": "configure",
    "DAEMON_ROTATE": "rotate",
    "DAEMON_RESUME": "resume",
    "ADD_RULE": "add_rule",
    "DEL_RULE": "delete_rule",
    "LIST_RULES": "list_rules",
    "WATCH_INS": "insert_watch",
    "WATCH_REM": "remove_watch",
    "WATCH_LIST": "list_watches",
    "MAC_POLICY_LOAD": "load_policy",
    "USER_MAC_POLICY_LOAD": "load_policy",
    "MAC_CONFIG_CHANGE": "change_config",
    "USER_MAC_CONFIG_CHANGE": "change_config",
    "MAC_STATUS": "change_status",
    "USER_MAC_STATUS": "change_status",
    "AVC": "access_control_decision",
    "AVC_PATH": "identify_avc_path",
    "FS_RELABEL": "relabel",
}

OUTER_RE = re.compile(r"^type=(?P<type>\S+)\s+msg=audit\((?P<stamp>[^:()]+):(?P<event_id>\d+)\):(?P<body>.*)$")
PAIR_RE = re.compile(r"(?P<key>[A-Za-z0-9_.-]+)=(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s]+)")

PUBLIC_KEYS = {
    "pid", "ppid", "uid", "auid", "ses", "exe", "op", "acct", "hostname", "addr", "terminal",
    "comm", "path", "inode", "action", "result", "res", "success", "rport", "laddr", "lport",
    "daddr", "saddr", "sport", "dport", "proto", "protocol", "syscall", "a0", "a1", "a2", "a3",
}


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")


def parse_pairs(text: str) -> Dict[str, str]:
    return {match.group("key"): unquote(match.group("value")) for match in PAIR_RE.finditer(text)}


def parse_line(line: str) -> Optional[Tuple[str, str, str, Dict[str, str]]]:
    match = OUTER_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    body = match.group("body").strip()
    fields = parse_pairs(body)
    # The inner msg='...' is parsed separately and overrides duplicate outer keys.
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


def iso_utc(stamp: str) -> str:
    value = float(stamp)
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def first(fields: Dict[str, str], *names: str) -> str:
    for name in names:
        if fields.get(name) not in (None, ""):
            return fields[name]
    return ""


def value_or_default(fields: Dict[str, str], default: str, *names: str) -> str:
    return first(fields, *names) or default


def normalize_result(value: str) -> str:
    if value == "1":
        return "success"
    if value == "0":
        return "failed"
    if value.lower() in {"yes", "success", "ok", "true"}:
        return "success"
    if value.lower() in {"no", "failed", "fail", "false"}:
        return "failed"
    if value.lower() in {"allow", "allowed", "grant", "granted"}:
        return "allowed"
    if value.lower() in {"deny", "denied"}:
        return "denied"
    return value


def category_for(type_name: str, fields: Dict[str, str]) -> Optional[str]:
    if type_name == "SYSCALL":
        syscall = first(fields, "syscall").lower()
        if syscall in {"execve", "execveat", "59", "322"}:
            return "process_command_execution"
        return "file_object_access"
    return TYPE_TO_CATEGORY.get(type_name)


def tips_string(fields: Dict[str, str], used: Iterable[str]) -> str:
    used_set = set(used)
    items = []
    for key, value in fields.items():
        if key in used_set or key == "msg":
            continue
        safe = str(value).replace("\\", "\\\\").replace(";", r"\;").replace("=", r"\=")
        items.append(f"{key}={safe}")
    return ";".join(items)


def normalize(type_name: str, stamp: str, event_id: str, fields: Dict[str, str]) -> Optional[Dict[str, str]]:
    category = category_for(type_name, fields)
    if category is None:
        return None
    result = {name: "" for name in FIELDNAMES}
    result.update(event_time=iso_utc(stamp), event_id=event_id, category=category, type=type_name)

    for source, target in (("pid", "pid"), ("uid", "uid"), ("auid", "auid"),
                           ("ses", "session_id"), ("exe", "executable")):
        result[target] = fields.get(source, "")

    if category in {"authentication_session", "account_security_change"}:
        result["operation"] = value_or_default(fields, DEFAULT_OPERATIONS.get(type_name, ""), "op")
    if category == "file_object_access":
        result["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "fan_type", "syscall")
    elif category == "network_ipc_communication":
        result["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "cmd", "hook")
    elif category == "system_service_audit_lifecycle":
        result["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op")
    elif category == "security_policy_config_change":
        result["action"] = value_or_default(fields, DEFAULT_ACTIONS.get(type_name, ""), "action", "op", "avc", "event")
    if category == "authentication_session":
        result["account"] = first(fields, "acct")
    if category in {"authentication_session", "account_security_change"}:
        result["hostname"] = first(fields, "hostname")
        result["source_address"] = first(fields, "addr")
        result["terminal"] = first(fields, "terminal")
    if category in {"process_command_execution", "file_object_access", "network_ipc_communication"}:
        result["ppid"] = first(fields, "ppid")
    if category == "file_object_access":
        result["object_path"] = first(fields, "path", "name", "filename")
        result["object_inode"] = first(fields, "inode", "ino")
    if category in {"system_service_audit_lifecycle", "security_policy_config_change"}:
        result["target"] = first(fields, "unit", "feature", "policy", "bool", "rule", "path", "name", "obj")
    if category == "system_service_audit_lifecycle":
        result["hostname"] = first(fields, "hostname")
    if category in {"process_command_execution", "file_object_access", "network_ipc_communication"}:
        result["command"] = first(fields, "comm", "cmd")
    if type_name == "EXECVE":
        args = [fields[k] for k in sorted(fields, key=lambda k: int(k[1:]) if k.startswith("a") and k[1:].isdigit() else 999999) if k.startswith("a") and k[1:].isdigit()]
        result["command"] = " ".join(args)
    if category == "network_ipc_communication":
        result["source_address"] = first(fields, "saddr", "src", "addr")
        result["destination_address"] = first(fields, "daddr", "dst")
        result["source_port"] = first(fields, "sport")
        result["destination_port"] = first(fields, "dport")
        result["protocol"] = first(fields, "proto", "protocol")
    result["result"] = normalize_result(first(fields, "res", "success", "result", "response"))
    if type_name == "AVC" and not result["result"]:
        if fields.get("permissive") == "1":
            result["result"] = "permissive"
        else:
            result["result"] = normalize_result(fields.get("avc", ""))

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
    result["tips"] = tips_string(fields, used)
    return result


def convert(paths: List[Path], output: Path) -> Dict[str, int]:
    stats = {"input": 0, "written": 0, "discarded": 0, "malformed": 0}
    type_counts: Dict[str, int] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for path in paths:
            with path.open("r", encoding="utf-8", errors="replace") as source:
                for line in source:
                    if not line.strip():
                        continue
                    stats["input"] += 1
                    parsed = parse_line(line)
                    if parsed is None:
                        stats["malformed"] += 1
                        continue
                    type_name, stamp, event_id, fields = parsed
                    type_counts[type_name] = type_counts.get(type_name, 0) + 1
                    row = normalize(type_name, stamp, event_id, fields)
                    if row is None:
                        stats["discarded"] += 1
                        continue
                    writer.writerow(row)
                    stats["written"] += 1
    stats.update({f"type_{key}": value for key, value in sorted(type_counts.items())})
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="*", type=Path,
        help="audit.log files; omitted uses INPUT_FILES above",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path(OUTPUT_FILE),
        help="output UTF-8 CSV path; default uses OUTPUT_FILE above",
    )
    args = parser.parse_args()
    paths = args.inputs or [Path(path) for path in INPUT_FILES]
    if not paths:
        parser.error("INPUT_FILES is empty; add at least one audit log path")
    missing = [path for path in paths if not path.is_file()]
    if missing:
        parser.error("input file does not exist: " + ", ".join(str(path) for path in missing))
    stats = convert(paths, args.output)
    print(f"input={stats['input']} written={stats['written']} discarded={stats['discarded']} malformed={stats['malformed']}")
    for key, value in stats.items():
        if key.startswith("type_"):
            print(f"{key[5:]}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
