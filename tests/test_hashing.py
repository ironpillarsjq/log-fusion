"""hashing.py 的存证契约：哈希输入定义、原文保真、缺字段降级。

这些用例锁住的是「存证能不能复算」这件事：只要哈希输入的定义被改动
（例如对 log 做 strip、把 timestamp 归一化、换分隔符），这里就会失败。
"""

import hashlib

import pytest

from app import hashing

# 用户提供的真实 Fluent Bit 采集样例（`log` 内含 auditd 的 0x1D 分隔符）
REAL_LINUX_RECORD = {
    "timestamp": "2026-10-02T07:56:17.924265Z",
    "file": "/var/log/audit/audit.log",
    "log": "type=SERVICE_STOP msg=audit(1790927777.918:62477): pid=1 uid=0 auid=4294967295 "
           "ses=4294967295 subj=unconfined msg='unit=NetworkManager-dispatcher comm=\"systemd\" "
           "exe=\"/usr/lib/systemd/systemd\" hostname=? addr=? terminal=? res=success'"
           "\x1dUID=\"root\" AUID=\"unset\"",
    "collector": "fluent-bit",
    "source": "ubuntu-audit",
    "client_id": "wlh-virtual-machine",
    "os": "linux",
}


def _expected(timestamp: str, log: str) -> str:
    payload = timestamp + hashing.HASH_FIELD_SEPARATOR + log
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- 哈希输入定义 -------------------------------------------------------------

def test_hash_is_sha256_of_timestamp_separator_and_log():
    digest = hashing.compute_log_hash("2026-10-02T07:56:17.924265Z", "hello")
    assert digest == _expected("2026-10-02T07:56:17.924265Z", "hello")
    assert len(digest) == 64
    assert digest == digest.lower()


def test_same_log_different_timestamp_yields_different_hash():
    """同一内容在不同时刻产生（syslog/auditd 常见）必须得到不同摘要，否则会被唯一索引吞掉。"""
    first = hashing.compute_log_hash("2026-10-02T07:56:17.924265Z", "pam_unix: session opened")
    second = hashing.compute_log_hash("2026-10-02T07:56:22.924265Z", "pam_unix: session opened")
    assert first != second


def test_separator_prevents_concatenation_collision():
    """直接拼接会让 (a|bc) 与 (ab|c) 撞成同一摘要；使用 US 分隔符避免歧义。"""
    left = hashing.compute_log_hash("2026", "abc")
    right = hashing.compute_log_hash("2026a", "bc")
    assert left != right


def test_leading_trailing_whitespace_is_part_of_the_hash():
    """哈希覆盖原文，不做 trim：带空格与不带空格是两条不同的原始日志。"""
    assert hashing.compute_log_hash("t", "line\n") != hashing.compute_log_hash("t", "line")


def test_control_characters_are_hashed_verbatim():
    """auditd 的 0x1D 分隔符按原始字节参与哈希，不能被转义成字面量 \\u001d。"""
    with_gs = hashing.compute_log_hash("t", "a\x1db")
    escaped = hashing.compute_log_hash("t", "a\\u001db")
    assert with_gs != escaped
    assert with_gs == _expected("t", "a\x1db")


def test_none_inputs_are_treated_as_empty_strings():
    assert hashing.compute_log_hash(None, None) == _expected("", "")


# --- 存证行构造 ---------------------------------------------------------------

def test_build_evidence_row_from_real_record():
    row, error = hashing.build_evidence_row("linux", REAL_LINUX_RECORD)
    assert error is None
    assert row["log_hash"] == _expected(REAL_LINUX_RECORD["timestamp"], REAL_LINUX_RECORD["log"])
    assert row["raw_log"] == REAL_LINUX_RECORD["log"]
    assert row["log_timestamp"] == REAL_LINUX_RECORD["timestamp"]
    assert row["platform"] == "linux"
    assert row["source"] == "ubuntu-audit"
    assert row["client_id"] == "wlh-virtual-machine"
    # event_time 由调用方传入（解析后的 UTC 时间），不参与哈希
    assert row["event_time"] is None


def test_verify_round_trip_from_stored_row():
    from datetime import datetime

    row, _ = hashing.build_evidence_row("linux", REAL_LINUX_RECORD, datetime(2026, 10, 2, 7, 56, 17))
    assert hashing.verify_log_hash(row) is True
    tampered = dict(row, raw_log=row["raw_log"] + " injected")
    assert hashing.verify_log_hash(tampered) is False


@pytest.mark.parametrize("record", [
    {},
    {"timestamp": "2026-10-10T00:00:00Z"},
    {"log": ""},
    {"log": None},
    {"log": "   "},
])
def test_missing_or_blank_log_skips_evidence(record):
    """没有 log 就不写存证：返回原因，由调用方计入 stage=hash，不影响日志入库。"""
    row, error = hashing.build_evidence_row("linux", record)
    assert row is None
    assert "log" in error


def test_non_object_record_skips_evidence():
    row, error = hashing.build_evidence_row("linux", "not-a-dict")
    assert row is None
    assert error


def test_metadata_fields_are_truncated_to_column_widths():
    row, _ = hashing.build_evidence_row("linux", {
        "log": "x",
        "timestamp": "t",
        "source": "s" * 400,
        "client_id": "c" * 400,
    })
    assert len(row["source"]) == hashing.HASH_TEXT_LIMITS["source"]
    assert len(row["client_id"]) == hashing.HASH_TEXT_LIMITS["client_id"]


def test_raw_log_is_not_truncated():
    """raw_log 是 LONGTEXT，不能被列宽裁剪，否则存证与原文不符。"""
    long_log = "y" * 200_000
    row, _ = hashing.build_evidence_row("linux", {"log": long_log, "timestamp": "t"})
    assert row["raw_log"] == long_log
    assert row["log_hash"] == _expected("t", long_log)


# --- 缺时间戳时的哈希定义（校验端依赖这条规则）-------------------------------

def test_missing_timestamp_hashes_as_empty_string():
    """`timestamp` 缺失时按**空串**参与哈希，不是跳过、也不是用别的占位。

    这条规则被校验端依赖：SQL 侧复算用 `IFNULL(log_timestamp,'')`，
    与这里的 `raw_text(None) == ""` 必须一致。若把「时间戳为 NULL」
    当成无法校验，就会对「采集端没带 timestamp」的合法存证误报。
    """
    assert hashing.raw_text(None) == ""
    assert hashing.compute_log_hash(None, "abc") == hashing.compute_log_hash("", "abc")
    assert hashing.compute_log_hash(None, "abc") == _expected("", "abc")


def test_missing_timestamp_row_is_verifiable():
    """缺时间戳的行写出来之后，必须能被自己的校验函数复算通过。"""
    row, error = hashing.build_evidence_row("linux", {"log": "没有任何时间信息的一行"})
    assert error is None
    assert row["log_timestamp"] is None
    assert hashing.verify_log_hash(row) is True


def test_empty_timestamp_and_null_timestamp_are_the_same_hash():
    """空串时间戳与缺失时间戳产生同一摘要——两者在库里无法区分，这是刻意的。"""
    row_none, _ = hashing.build_evidence_row("linux", {"log": "x"})
    row_empty, _ = hashing.build_evidence_row("linux", {"log": "x", "timestamp": ""})
    assert row_none["log_hash"] == row_empty["log_hash"]


# --- Windows 存证原文：整条记录的规范化 JSON ------------------------------------
# 背景：winlog 载荷没有 `log` 字段，早期实现因此对**每一条** Windows 日志跳过存证，
# 造成「日志 71 万条、存证 4 万条」且 windows=0。下列用例锁住修复后的口径。

REAL_WINDOWS_RECORD = {
    "client_id": "WIN-4R3U3FDFLP6",
    "TimeGenerated": "2026-10-15T02:36:33.000000Z",
    "EventID": "4672",
    "RecordNumber": 123456,
    "Channel": "Security",
    "SourceName": "Microsoft-Windows-Security-Auditing",
    "Computer": "WIN-4R3U3FDFLP6",
    "Data": ["Special Logon", "S-1-5-21-1"],
    "Message": "",
}


def test_windows_record_without_log_field_still_gets_evidence():
    assert "log" not in REAL_WINDOWS_RECORD
    row, error = hashing.build_evidence_row("windows", REAL_WINDOWS_RECORD)
    assert error is None
    assert row["platform"] == "windows"
    assert row["raw_log"] == hashing.canonical_json(REAL_WINDOWS_RECORD)
    assert row["log_hash"] == _expected(REAL_WINDOWS_RECORD["TimeGenerated"], row["raw_log"])


def test_canonical_json_is_key_order_and_whitespace_independent():
    """字段顺序与缩进不得改变摘要：Fluent Bit 输出顺序不稳定，规范化必须吸收它。"""
    import json as _json

    reordered = _json.loads(_json.dumps(dict(reversed(list(REAL_WINDOWS_RECORD.items())))))
    assert hashing.canonical_json(reordered) == hashing.canonical_json(REAL_WINDOWS_RECORD)
    packed = hashing.canonical_json(REAL_WINDOWS_RECORD)
    assert ": " not in packed and ", " not in packed  # 紧凑分隔符，不留格式化空白


def test_canonical_json_keeps_non_ascii_verbatim():
    """中文不转义成 \\uXXXX：存证原文要可读，且哈希覆盖真实 UTF-8 字节。"""
    packed = hashing.canonical_json({"Message": "登录成功", "EventID": "4624"})
    assert "登录成功" in packed
    assert "\\u" not in packed


def test_any_field_change_changes_the_hash():
    """规范化 JSON 覆盖整条记录：任一字段被改动都必须体现为摘要变化。"""
    base = hashing.build_evidence_row("windows", REAL_WINDOWS_RECORD)[0]["log_hash"]
    for field, value in (("EventID", "4625"), ("RecordNumber", 999), ("Channel", "System"),
                         ("Data", ["Tampered"]), ("client_id", "evil-host")):
        assert hashing.build_evidence_row("windows", dict(REAL_WINDOWS_RECORD, **{field: value}))[0]["log_hash"] != base, field


def test_windows_timestamp_priority_matches_parser_field_order():
    """时间戳取值优先级必须与 `normalize_windows` 一致，且跳过空值。"""
    assert hashing.WINDOWS_TIMESTAMP_KEYS == ("TimeGenerated", "TimeWritten", "event_time", "timestamp", "date")
    row, _ = hashing.build_evidence_row("windows", {"TimeGenerated": "", "TimeWritten": "2026-10-15T02:00:00Z",
                                                    "EventID": "7036"})
    assert row["log_timestamp"] == "2026-10-15T02:00:00Z"
    row, _ = hashing.build_evidence_row("windows", {"date": 1791938979, "EventID": "7036"})
    assert row["log_timestamp"] == "1791938979"


def test_windows_row_round_trips_and_detects_tampering():
    row, _ = hashing.build_evidence_row("windows", REAL_WINDOWS_RECORD)
    assert hashing.verify_log_hash(row) is True
    assert hashing.verify_log_hash(dict(row, raw_log=row["raw_log"].replace("4672", "4624"))) is False


def test_windows_evidence_matches_sql_recompute_expression():
    """与校验端 `main.EVIDENCE_VERIFY_SQL` 的复算口径对齐。

    SQL 侧是 `SHA2(CONCAT(IFNULL(log_timestamp,''), CHAR(31), raw_log), 256)`，
    这里用同一表达式在 Python 里复算一遍——任一端改规则都会让全部 Windows 存证
    被误判成 HASH_MISMATCH。
    """
    import hashlib

    row, _ = hashing.build_evidence_row("windows", REAL_WINDOWS_RECORD)
    payload = (row["log_timestamp"] or "") + hashing.HASH_FIELD_SEPARATOR + row["raw_log"]
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == row["log_hash"]


def test_windows_evidence_falls_back_to_provider_name():
    """winlog 没有 Linux 那样的 `source`，用 SourceName 占位，避免台账「来源」全空。"""
    row, _ = hashing.build_evidence_row("windows", REAL_WINDOWS_RECORD)
    assert row["source"] == "Microsoft-Windows-Security-Auditing"
    explicit, _ = hashing.build_evidence_row("windows", dict(REAL_WINDOWS_RECORD, source="win-agent-1"))
    assert explicit["source"] == "win-agent-1"


def test_windows_ignores_log_field_and_hashes_whole_record():
    """即使载荷里出现了 `log`，Windows 仍按整条记录存证——口径不能随字段有无而漂移。"""
    with_log = dict(REAL_WINDOWS_RECORD, log="ignored")
    assert hashing.build_evidence_row("windows", with_log)[0]["raw_log"] == \
        hashing.canonical_json(with_log)


def test_windows_empty_record_skips_evidence():
    """空对象没有任何内容可存证，跳过而不是写入 `{}` 这种无意义摘要。"""
    row, error = hashing.build_evidence_row("windows", {})
    assert row is None
    assert error


def test_linux_path_still_requires_log_field():
    """Linux 口径不得因为 Windows 分支的加入而放宽：没有 log 依旧跳过。"""
    row, error = hashing.build_evidence_row("linux", REAL_WINDOWS_RECORD)
    assert row is None
    assert "log" in error

