"""原始日志存证层：在写入业务表之前，对采集到的原始日志计算 SHA-256 并构造存证行。

设计要点（与 `parsers.py` 一样是纯函数层，便于单测）：

1. **哈希输入固定为「原始 timestamp 字符串 + 0x1F + 原始 log 字符串」的 UTF-8 字节**。
   - 两个字段都取采集端发来的**原始值**：不 trim、不归一化、不做大小写转换。
   - 分隔符用 US（`\\x1f`，Unit Separator）而不是直接拼接，避免
     `timestamp="...Z" + log="abc"` 与 `timestamp="...Zabc" + log=""` 撞成同一个摘要。
   - 时间戳必须参与哈希，否则同一台机器在不同时刻产生的相同日志行（syslog/auditd
     里很常见）会得到同一个摘要，被唯一索引挡掉，存证就无法与日志一一对应。
   - 存证的意义是「事后能用同样的输入复算出同样的摘要」，因此**任何一步加工都会让存证失效**。
     想要复算，请直接取存证行的 `log_timestamp` 与 `raw_log` 两列重新计算。

2. **原始日志取自 JSON 的 `log` 字段**（Fluent Bit 的采集内容），不是解析后的归一化行。
   归一化行会随解析规则的调整而变化，用它做存证会在升级解析器后无法复现。

3. **`log` 缺失或为空时不写存证**，返回错误交给调用方计入 `errors[]`（`stage="hash"`），
   但**不影响日志入库**：存证是附加能力，不应该让业务写入失败。
"""

from __future__ import annotations

import hashlib
from typing import Any

# 哈希输入的字段分隔符：US (Unit Separator)，几乎不可能自然出现在时间戳或日志正文里
HASH_FIELD_SEPARATOR = "\x1f"
# 存证表 `log_hash` 的列宽（docs/DATABASE.md）
HASH_TEXT_LIMITS = {
    "platform": 16,
    "source": 255,
    "client_id": 128,
    "log_timestamp": 64,
}


def text(value: Any) -> str | None:
    """转成字符串并去除首尾空白；空值返回 None。仅用于「非哈希输入」的元数据字段。"""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def raw_text(value: Any) -> str:
    """取原始字符串用于哈希：**不做 strip**，None 归一化为空串。

    与 `text()` 的区别是这里保留首尾空白和控制字符，因为哈希必须覆盖原文。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def compute_log_hash(timestamp: Any, log_message: Any) -> str:
    """计算存证摘要：sha256(timestamp + 0x1F + log)，返回 64 位小写十六进制。"""
    payload = raw_text(timestamp) + HASH_FIELD_SEPARATOR + raw_text(log_message)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_evidence_row(platform: str, record: Any, event_time: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """把一条采集记录转成 `log_hash` 存证行。返回 (row, error)。

    - `record` 不是对象、或 `log` 字段缺失/为空 → 返回 `(None, 原因)`，调用方计入 `stage="hash"`。
    - `event_time` 是解析后的 naive UTC `datetime`，仅用于查询排序，**不参与哈希**；
      参与哈希的是原始字符串 `log_timestamp`。
    """
    if not isinstance(record, dict):
        return None, "记录不是 JSON 对象，无法生成存证"

    log_message = raw_text(record.get("log"))
    if not log_message.strip():
        return None, "记录缺少 log 字段或内容为空，跳过存证"

    timestamp_raw = record.get("timestamp")
    row: dict[str, Any] = {
        "log_hash": compute_log_hash(timestamp_raw, log_message),
        "raw_log": log_message,
        "log_timestamp": truncate(raw_text(timestamp_raw) or None, HASH_TEXT_LIMITS["log_timestamp"]),
        "platform": truncate(platform, HASH_TEXT_LIMITS["platform"]),
        "source": truncate(text(record.get("source")), HASH_TEXT_LIMITS["source"]),
        "client_id": truncate(text(record.get("client_id")), HASH_TEXT_LIMITS["client_id"]),
        "event_time": event_time,
    }
    return row, None


def verify_log_hash(row: dict[str, Any]) -> bool:
    """用存证行自身的 `log_timestamp` 与 `raw_log` 复算摘要，供校验/测试使用。"""
    expected = row.get("log_hash")
    if not expected:
        return False
    return compute_log_hash(row.get("log_timestamp"), row.get("raw_log")) == expected
