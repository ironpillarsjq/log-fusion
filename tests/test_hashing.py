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
