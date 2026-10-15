"""原始日志存证层：对采集到的原始日志计算 SHA-256 并构造存证行。

调用时机是 `main.persist_logs` 中**业务表写入之后、返回响应之前**；存证失败不回滚已入库的日志。

设计要点（与 `parsers.py` 一样是纯函数层，便于单测）：

1. **哈希输入固定为「原始 timestamp 字符串 + 0x1F + 存证原文」的 UTF-8 字节**。
   存证原文按平台不同，见第 2 条。
   - 两个字段都取采集端发来的**原始值**：不 trim、不归一化、不做大小写转换。
   - 分隔符用 US（`\\x1f`，Unit Separator）而不是直接拼接，避免
     `timestamp="...Z" + log="abc"` 与 `timestamp="...Zabc" + log=""` 撞成同一个摘要。
   - 时间戳必须参与哈希，否则同一台机器在不同时刻产生的相同日志行（syslog/auditd
     里很常见）会得到同一个摘要，被唯一索引挡掉，存证就无法与日志一一对应。
   - 存证的意义是「事后能用同样的输入复算出同样的摘要」，因此**任何一步加工都会让存证失效**。
     想要复算，请直接取存证行的 `log_timestamp` 与 `raw_log` 两列重新计算。

2. **存证原文按平台取不同字段**（见 `evidence_input`）：
   - **Linux**：取 JSON 的 `log` 字段（Fluent Bit 的采集内容），不是解析后的归一化行。
     归一化行会随解析规则调整而变化，用它做存证会在升级解析器后无法复现。
   - **Windows**：winlog 载荷**没有 `log` 字段**（只有 `TimeGenerated`/`EventID`/`Channel`/
     `Data` 等，见 `docs/API.md` 的 Windows 样例），所以存证原文改用**整条记录的规范化
     JSON**（键排序 + 紧凑分隔符 + 不转义非 ASCII）。
     ⚠ 两侧语义并不等价：Linux 存的是采集端发来的字节原文，Windows 存的是服务端
     反序列化之后的规范化形态——键序、空白、数字字面量与重复键在 `json` 解析时已定型。
     因此 Windows 存证证明的是「服务端收到的这条事件内容」，**不能**证明「采集端发出的
     字节流未经中间环节改动」。要覆盖后者需改采集端（让 winlog 输出带 `log` 字段），
     属未实现项，见 `docs/TODO.md`。

3. **存证原文为空时不写存证**（Linux 缺 `log`、Windows 为空对象），返回错误交给调用方计入
   `errors[]`（`stage="hash"`），但**不影响日志入库**：存证是附加能力，不应该让业务写入失败。
"""

from __future__ import annotations

import hashlib
import json
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
# Windows 原始时间戳字段的取值优先级。**必须与 `parsers.normalize_windows` 的时间字段
# 顺序一致**，否则存证里记的时间和业务表里入库的时间会来自不同字段。
WINDOWS_TIMESTAMP_KEYS = ("TimeGenerated", "TimeWritten", "event_time", "timestamp", "date")


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
    """计算存证摘要：sha256(timestamp + 0x1F + 存证原文)，返回 64 位小写十六进制。"""
    payload = raw_text(timestamp) + HASH_FIELD_SEPARATOR + raw_text(log_message)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_json(record: dict) -> str:
    """把整条记录序列化为**确定性** JSON 文本，用作 Windows 存证原文。

    - `sort_keys=True`：键序不影响摘要（Fluent Bit / 不同版本输出顺序不稳定）。
    - `separators=(",", ":")`：不留空白，避免格式化差异改变摘要。
    - `ensure_ascii=False`：中文与控制字符按原字符写出，再整体 UTF-8 编码参与哈希。

    复算时**不需要**重新序列化：`raw_log` 列存的就是这个字符串本身，
    直接 `sha256(log_timestamp + 0x1F + raw_log)` 即可（校验端正是这么做的）。
    """
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_input(platform: str, record: dict) -> tuple[str | None, Any, str]:
    """取一条记录的「存证原文」与「参与哈希的原始时间戳」。

    返回 `(raw_log, timestamp_raw, skip_reason)`。`raw_log` 为 None 表示这条不该存证，
    第三个返回值是给 `errors[]` 的原因文本。
    """
    if platform == "windows":
        if not record:
            return None, None, "记录为空对象，无内容可存证"
        timestamp_raw = next((record.get(key) for key in WINDOWS_TIMESTAMP_KEYS
                              if raw_text(record.get(key)).strip()), None)
        return canonical_json(record), timestamp_raw, ""

    log_message = raw_text(record.get("log"))
    if not log_message.strip():
        return None, None, "记录缺少 log 字段或内容为空，跳过存证"
    return log_message, record.get("timestamp"), ""


def build_evidence_row(platform: str, record: Any, event_time: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """把一条采集记录转成 `log_hash` 存证行。返回 (row, error)。

    - `record` 不是对象、或存证原文为空 → 返回 `(None, 原因)`，调用方计入 `stage="hash"`。
    - 存证原文与时间戳的取法见 `evidence_input`（Linux 用 `log`，Windows 用规范化 JSON）。
    - `event_time` 是解析后的 naive UTC `datetime`，仅用于查询排序，**不参与哈希**；
      参与哈希的是原始字符串 `log_timestamp`。
    """
    if not isinstance(record, dict):
        return None, "记录不是 JSON 对象，无法生成存证"

    log_message, timestamp_raw, skip_reason = evidence_input(platform, record)
    if log_message is None:
        return None, skip_reason

    # Windows 载荷没有 Linux 那样的 `source`，用 winlog 的 Provider 名占位，
    # 否则存证台账的「来源」列会对全部 Windows 记录显示为空。
    source = record.get("source")
    if source in (None, "") and platform == "windows":
        source = record.get("SourceName") or record.get("provider_name")

    row: dict[str, Any] = {
        "log_hash": compute_log_hash(timestamp_raw, log_message),
        "raw_log": log_message,
        "log_timestamp": truncate(raw_text(timestamp_raw) or None, HASH_TEXT_LIMITS["log_timestamp"]),
        "platform": truncate(platform, HASH_TEXT_LIMITS["platform"]),
        "source": truncate(text(source), HASH_TEXT_LIMITS["source"]),
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
