"""解析层单元测试（纯函数，不访问数据库）。

覆盖 2026-10-10 修复的核心问题：原始时间字符串（ISO 带 Z、带时区偏移、epoch）
必须转成 `datetime` 才能写入 MySQL，否则报 1292；以及行字段必须按目标表真实列裁剪。
"""

import json
from datetime import datetime

import pytest

from app.parsers import (
    LINUX_TABLE_FIELDS,
    WINDOWS_TABLE_FIELDS,
    normalize_linux,
    normalize_windows,
    parse_audit_line,
    parse_datetime,
    parse_syslog_time,
    stable_event_id,
    windows_table_for,
)

# --- 时间解析 -----------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2026-10-02T09:31:59.929001Z", datetime(2026, 10, 2, 9, 31, 59, 929001)),
    ("2026-10-02T09:31:59Z", datetime(2026, 10, 2, 9, 31, 59)),
    ("2026-10-02 20:03:18 +0800", datetime(2026, 10, 2, 12, 3, 18)),   # 时区偏移 → UTC
    ("2026-10-02T20:03:18+08:00", datetime(2026, 10, 2, 12, 3, 18)),
    ("2026-10-02 20:03:18", datetime(2026, 10, 2, 20, 3, 18)),
    ("2026-10-02 12:03:18,500", datetime(2026, 10, 2, 12, 3, 18, 500000)),
    ("2026/10/02 12:03:18", datetime(2026, 10, 2, 12, 3, 18)),
    (datetime(2026, 10, 2, 12, 3, 18), datetime(2026, 10, 2, 12, 3, 18)),
])
def test_parse_datetime_variants(value, expected):
    assert parse_datetime(value) == expected


def test_parse_datetime_epoch_units_agree():
    seconds = parse_datetime(1790988022)
    millis = parse_datetime(1790988022386)
    micros = parse_datetime(1790988022386657)
    assert seconds is not None and seconds.year == 2026 and seconds.month == 10
    assert millis.replace(microsecond=0) == seconds
    assert micros.replace(microsecond=0) == seconds
    assert parse_datetime("1790988022") == seconds


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45 99:99:99", True, []])
def test_parse_datetime_invalid_returns_none(value):
    assert parse_datetime(value) is None


def test_parse_syslog_time_needs_fallback_year():
    assert parse_syslog_time("Oct  2 17:30:01 host proc: msg", 2026) == datetime(2026, 10, 2, 17, 30, 1)
    assert parse_syslog_time("garbage", 2026) is None


def test_stable_event_id_is_deterministic():
    assert stable_event_id("a", "b") == stable_event_id("a", "b")
    assert stable_event_id("a", "b") != stable_event_id("b", "a")


# --- Linux：auditd ------------------------------------------------------------

def test_parse_audit_line_extracts_fields():
    parsed = parse_audit_line('type=EXECVE msg=audit(1696234567.123:456): argc=2 a0="ls" a1="-l" key="x"')
    assert parsed is not None
    type_name, stamp, event_id, fields = parsed
    assert (type_name, event_id) == ("EXECVE", "456")
    assert fields["a0"] == "ls" and fields["a1"] == "-l" and fields["key"] == "x"
    assert parse_audit_line("plain syslog line") is None


def test_normalize_linux_raw_syslog_goes_to_authentication_session():
    row, error = normalize_linux({
        "client_id": "c1",
        "timestamp": "2026-10-02T09:31:59.929001Z",
        "log": "Oct  2 17:30:01 wlh CRON[4359]: pam_unix(cron:session): session opened for user root(uid=0) by (uid=0)",
    })
    assert error is None
    assert row["category"] == "authentication_session"
    assert row["type"] == "SYSLOG" and row["operation"] == "session_open"
    assert row["account"] == "root" and row["hostname"] == "wlh" and row["executable"] == "CRON[4359]"
    assert row["event_time"] == datetime(2026, 10, 2, 9, 31, 59, 929001)
    assert isinstance(row["event_id"], int)


def test_normalize_linux_audit_execve_command_and_columns():
    row, error = normalize_linux({
        "client_id": "c1",
        "timestamp": "2026-10-02T09:00:00Z",
        "log": 'type=EXECVE msg=audit(1696234567.123:456): argc=2 a0="ls" a1="-l"',
    })
    assert error is None
    assert row["category"] == "process_command_execution"
    assert row["command"] == "ls -l"
    assert "hostname" not in row          # 该表没有 hostname 列
    assert set(row) <= set(LINUX_TABLE_FIELDS["process_command_execution"])


def test_normalize_linux_syscall_context_decides_category():
    execve, _ = normalize_linux({
        "timestamp": "2026-10-02T09:00:00Z",
        "log": 'type=SYSCALL msg=audit(1696234567.1:1): syscall=execve exe="/bin/ls" uid=0',
    })
    assert execve["category"] == "process_command_execution"
    openat, _ = normalize_linux({
        "timestamp": "2026-10-02T09:00:00Z",
        "log": 'type=SYSCALL msg=audit(1696234567.1:2): syscall=openat exe="/bin/cat"',
    })
    assert openat["category"] == "file_object_access"


def test_normalize_linux_network_record():
    row, error = normalize_linux({
        "timestamp": "2026-10-02T09:00:00Z",
        "log": "type=SOCKADDR msg=audit(1696234567.2:2): saddr=10.0.0.1 sport=1234 daddr=10.0.0.2 dport=80 proto=tcp",
    })
    assert error is None
    assert row["category"] == "network_ipc_communication"
    assert row["source_address"] == "10.0.0.1"
    assert row["destination_address"] == "10.0.0.2"
    assert row["source_port"] == 1234 and row["destination_port"] == 80
    assert row["protocol"] == "tcp"


def test_normalize_linux_user_auth_fields_and_result():
    row, error = normalize_linux({
        "timestamp": "2026-10-02T09:00:00Z",
        "log": 'type=USER_AUTH msg=audit(1696234567.3:3): pid=1 uid=0 auid=1000 acct="root" hostname=? addr=10.0.0.9 terminal=pts/0 res=success',
    })
    assert error is None
    assert row["category"] == "authentication_session"
    assert row["account"] == "root" and row["result"] == "success"
    assert row["source_address"] == "10.0.0.9" and row["terminal"] == "pts/0"
    assert row["pid"] == 1 and row["auid"] == 1000


def test_normalize_linux_unknown_audit_type_is_parse_error():
    row, error = normalize_linux({"timestamp": "2026-10-02T09:00:00Z", "log": "type=NOT_A_REAL_TYPE msg=audit(1:1): x=1"})
    assert row is None and "未收录" in error


def test_normalize_linux_missing_time_is_parse_error():
    row, error = normalize_linux({"log": "没有任何时间信息的一行"})
    assert row is None and "时间戳" in error


def test_normalize_linux_syslog_time_fallback_without_timestamp():
    row, error = normalize_linux({"log": "Oct  2 17:30:01 wlh sshd[1]: Accepted password for bob"})
    assert error is None
    assert row["event_time"] == datetime(datetime.now().year, 10, 2, 17, 30, 1)
    assert row["account"] == "bob"


@pytest.mark.parametrize("value", ["text", 123, None, [1, 2]])
def test_normalize_linux_rejects_non_object(value):
    row, error = normalize_linux(value)
    assert row is None and error == "记录不是 JSON 对象"


def test_normalize_linux_dropped_fields_are_kept_in_tips():
    """process_command_execution 没有 hostname 列，缺列字段要并入 tips 而不是报错。"""
    row, error = normalize_linux({
        "timestamp": "2026-10-02T09:00:00Z",
        "log": 'type=EXECVE msg=audit(1696234567.9:9): a0="id" hostname=server-x',
    })
    assert error is None
    assert "hostname=server-x" in row["tips"]


# --- Windows ------------------------------------------------------------------

def test_windows_table_for_channel():
    assert windows_table_for("Security") == "security_logs"
    assert windows_table_for("Forwarded-Events") == "forwardedevents_logs"
    assert windows_table_for(None) == "application_logs"
    assert windows_table_for("SomeNewChannel") == "application_logs"


def test_normalize_windows_maps_all_columns():
    row, error = normalize_windows({
        "client_id": "win-1",
        "TimeGenerated": "2026-10-02 20:03:18 +0800",
        "TimeWritten": "2026-10-02 20:03:18 +0800",
        "EventID": 5379,
        "RecordNumber": 4948413,
        "Channel": "Security",
        "ComputerName": "Pillar-Desktop",
        "SourceName": "Microsoft-Windows-Security-Auditing",
        "Data": ["a", "b"],
        "Message": "凭据已读取",
        "Sid": "S-1-5-18",
        "extra_field": {"x": 1},
    })
    assert error is None
    assert row["time_created"] == datetime(2026, 10, 2, 12, 3, 18)
    assert row["source_year"] == 2026
    assert row["event_id"] == "5379" and row["event_record_id"] == 4948413
    assert row["source_file"] == "fluent-bit://win-1/security"
    assert row["computer"] == "Pillar-Desktop" and row["security_user_id"] == "S-1-5-18"
    assert json.loads(row["eventdata"])["Data"] == ["a", "b"]
    assert json.loads(row["eventdata"])["Message"] == "凭据已读取"
    assert json.loads(row["system_extra"]) == {"extra_field": {"x": 1}}
    assert set(row) <= set(WINDOWS_TABLE_FIELDS)


def test_normalize_windows_epoch_and_minimal_payload():
    row, error = normalize_windows({"client_id": "c", "date": 1790988022, "Channel": "Application", "EventID": 1})
    assert error is None
    assert row["time_created"] is not None and row["time_created"].year == 2026
    assert json.loads(row["eventdata"]) is not None
    assert json.loads(row["system_extra"]) == {}


def test_normalize_windows_requires_time():
    row, error = normalize_windows({"Channel": "Security", "EventID": 1})
    assert row is None and "时间戳" in error


@pytest.mark.parametrize("value", ["text", 5, None])
def test_normalize_windows_rejects_non_object(value):
    row, error = normalize_windows(value)
    assert row is None and error == "记录不是 JSON 对象"


def test_normalize_windows_truncates_and_coerces_types():
    row, _ = normalize_windows({
        "client_id": "c", "TimeGenerated": "2026-10-02T00:00:00Z", "Channel": "Application",
        "EventID": 1, "ComputerName": "x" * 400, "RecordNumber": "12345",
    })
    assert len(row["computer"]) == 255
    assert row["event_record_id"] == 12345
