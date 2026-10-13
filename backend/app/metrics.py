"""图表聚合层：时间桶趋势、排行统计与心跳历史序列。

设计见 docs/PLAN-frontend-charts.md §3，核心约束：

1. **时间桶一律 UTC**：DB 内即 UTC naive datetime，本模块只做分桶不做时区转换，
   响应里的桶键统一是 `...Z` 结尾的 UTC ISO 字符串；本地时间由前端渲染时转换，
   否则趋势图/热力图会偏 8 小时。
2. **进程内 TTL 缓存**：12 张表的 GROUP BY 不能被高频轮询反复触发；
   单 worker 部署约束（ADR-015）决定了进程内缓存即可，响应必带
   `generatedAt`/`cached`/`ageMs`，让「数据有多新」可观测。
3. **心跳历史是环形缓冲，不查库不建表**：watchdog 每 5 秒采样一次，
   容量 720 点（1 小时）。代价是进程重启后历史清零——页面必须能显示空态，
   不能把「重启过」伪装成「从来没有心跳」。
"""

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from .catalog import LINUX_CATEGORIES, LINUX_TABLE_COLUMNS, WINDOWS_CATEGORIES
from .db import linux_engine, rows, windows_engine

# --- 时间窗口 / 分桶 -----------------------------------------------------------

RANGE_SECONDS = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
DEFAULT_BUCKET = {"1h": "minute", "24h": "hour", "7d": "day", "30d": "day"}
BUCKET_STEP_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}
# 分桶表达式通过绑定参数传入（DATE_FORMAT 的 % 字面量若直接拼进 SQL，
# 在 pymysql 的 format 参数风格下会被当成占位符）。
BUCKET_SQL_FORMAT = {"minute": "%Y-%m-%dT%H:%i:00", "hour": "%Y-%m-%dT%H:00:00", "day": "%Y-%m-%dT00:00:00"}
# 缓存 TTL：窗口越大结果变化越慢，缓存可以越久
CACHE_TTL_SECONDS = {"1h": 5, "24h": 15, "7d": 60, "30d": 60}
STATS_CACHE_TTL_SECONDS = 60
MAX_TREND_POINTS = 2000

LINUX_TIME_COLUMN = "event_time"
WINDOWS_TIME_COLUMN = "time_created"
# 只有带 hostname 的 Linux 表能参与主机排行（真实列见 docs/DATABASE.md §4.3）
LINUX_HOST_TABLES = tuple(name for name, columns in LINUX_TABLE_COLUMNS.items() if "hostname" in columns)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime) -> str:
    """UTC naive datetime → `...Z` 结尾的 ISO 字符串（后端唯一的时间出口格式）。"""
    return value.isoformat(timespec="seconds") + "Z"


def _floor(value: datetime, bucket: str) -> datetime:
    if bucket == "minute":
        return value.replace(second=0, microsecond=0)
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_keys(start: datetime, end: datetime, bucket: str) -> list[str]:
    step = timedelta(seconds=BUCKET_STEP_SECONDS[bucket])
    keys: list[str] = []
    cursor = start
    while cursor <= end and len(keys) <= MAX_TREND_POINTS:
        keys.append(_iso(cursor))
        cursor += step
    return keys


def _normalize_range(value: str) -> str:
    if value not in RANGE_SECONDS:
        raise ValueError(f"invalid range: {value}（可选 1h/24h/7d/30d）")
    return value


def _normalize_bucket(value: str | None, range_key: str) -> str:
    bucket = value or DEFAULT_BUCKET[range_key]
    if bucket not in BUCKET_STEP_SECONDS:
        raise ValueError(f"invalid bucket: {bucket}（可选 minute/hour/day）")
    # 单次响应的桶数上限：minute × 30d = 43200 个点，任何图表都画不下，
    # 更重要的是空桶填充本身就会放大响应——直接在查库前拒绝。
    estimated = RANGE_SECONDS[range_key] // BUCKET_STEP_SECONDS[bucket] + 2
    if estimated > MAX_TREND_POINTS:
        raise ValueError(
            f"range={range_key} 配 bucket={bucket} 会产生约 {estimated} 个数据点，超过上限 {MAX_TREND_POINTS}"
        )
    return bucket


def _normalize_platform(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip().lower()
    if normalized == "linux":
        return "Linux"
    if normalized == "windows":
        return "Windows"
    raise ValueError(f"invalid platform: {value}（可选 Linux/Windows）")


# --- TTL 缓存 ------------------------------------------------------------------

_cache: dict[str, tuple[float, str, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX_KEYS = 256


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: str, ttl_seconds: float, builder) -> tuple[dict, bool, int]:
    """缓存未命中或过期时执行 builder 并写入；返回 (载荷, 是否命中, 年龄毫秒)。"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl_seconds:
        return hit[2], True, int((now - hit[0]) * 1000)
    payload = builder()
    stamp = time.monotonic()
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_KEYS:
            _cache.clear()
        _cache[key] = (stamp, _iso(_now_utc()), payload)
    return payload, False, 0


# --- 趋势：时间桶计数 -----------------------------------------------------------


def _bucket_counts(engine, table_columns: list[tuple[str, str]], bucket: str, start: datetime) -> dict[str, int]:
    """按表分桶计数后合并：一个引擎一条 UNION ALL 查询，避免 12 次往返。"""
    fmt = BUCKET_SQL_FORMAT[bucket]
    parts = [
        f"SELECT DATE_FORMAT(`{column}`, :fmt) AS b, COUNT(*) AS n FROM `{table}` "
        f"WHERE `{column}` >= :start GROUP BY b"
        for table, column in table_columns
    ]
    sql = "SELECT b, SUM(n) AS n FROM (" + " UNION ALL ".join(parts) + ") x GROUP BY b"
    # SUM(...) 返回 Decimal，必须转 int，否则 JSON 序列化会失败
    return {f"{row['b']}Z": int(row["n"]) for row in rows(engine, sql, {"fmt": fmt, "start": start})}


def _compute_trend(range_key: str, bucket: str, platform: str | None) -> dict:
    now = _now_utc()
    start = _floor(now - timedelta(seconds=RANGE_SECONDS[range_key]), bucket)
    linux_counts: dict[str, int] = {}
    windows_counts: dict[str, int] = {}
    if platform in (None, "Linux"):
        linux_counts = _bucket_counts(
            linux_engine, [(name, LINUX_TIME_COLUMN) for name in LINUX_CATEGORIES], bucket, start
        )
    if platform in (None, "Windows"):
        windows_counts = _bucket_counts(
            windows_engine, [(f"{name}_logs", WINDOWS_TIME_COLUMN) for name in WINDOWS_CATEGORIES], bucket, start
        )
    points = []
    for key in _bucket_keys(start, now, bucket):
        linux = linux_counts.get(key, 0)
        windows = windows_counts.get(key, 0)
        points.append({"bucket": key, "linux": linux, "windows": windows, "total": linux + windows})
    return {"points": points}


def logs_trend(range_key: str = "24h", bucket: str | None = None, platform: str | None = None) -> dict:
    """GET /api/v1/logs/trend 的数据体：UTC 时间桶 + 平台分序列，带 TTL 缓存。"""
    range_key = _normalize_range(range_key)
    bucket = _normalize_bucket(bucket, range_key)
    platform = _normalize_platform(platform)
    cache_key = f"trend:{range_key}:{bucket}:{platform or 'all'}"
    payload, cached, age_ms = _cached(cache_key, CACHE_TTL_SECONDS[range_key],
                                      lambda: _compute_trend(range_key, bucket, platform))
    with _cache_lock:
        generated_at = _cache.get(cache_key, (None, _iso(_now_utc()), None))[1]
    return dict(payload, bucket=bucket, range=range_key, generatedAt=generated_at, cached=cached, ageMs=age_ms)


# --- 排行：TOP 类别 / TOP 主机 ---------------------------------------------------


def _category_counts(engine, table_keys: list[tuple[str, str]], column: str, labels: dict[str, str], platform: str, start: datetime) -> list[dict]:
    """按窗口计数每个类别。

    `table_keys` 是 (表名, 类别键) 对：Windows 的表名是 `{key}_logs`，而 label
    映射按类别键索引，二者不能混用（曾因此 KeyError：`labels['application_logs']`）。
    """
    parts = [
        f"SELECT '{key}' AS k, COUNT(*) AS n FROM `{table}` WHERE `{column}` >= :start"
        for table, key in table_keys
    ]
    result = [
        {"key": row["k"], "label": labels[row["k"]], "platform": platform, "count": int(row["n"])}
        for row in rows(engine, " UNION ALL ".join(parts), {"start": start})
    ]
    result.sort(key=lambda item: item["count"], reverse=True)
    return result


def _host_counts(engine, tables: list[str], column: str, start: datetime) -> dict[str, int]:
    host_column = "hostname" if column == LINUX_TIME_COLUMN else "computer"
    parts = [
        f"SELECT `{host_column}` AS host, COUNT(*) AS n FROM `{table}` "
        f"WHERE `{column}` >= :start AND `{host_column}` IS NOT NULL AND `{host_column}` <> '' "
        f"GROUP BY `{host_column}`"
        for table in tables
    ]
    sql = "SELECT host, SUM(n) AS n FROM (" + " UNION ALL ".join(parts) + ") x GROUP BY host"
    return {row["host"]: int(row["n"]) for row in rows(engine, sql, {"start": start})}


def _compute_stats(range_key: str, limit: int, platform: str | None) -> dict:
    start = _now_utc() - timedelta(seconds=RANGE_SECONDS[range_key])
    categories: list[dict] = []
    hosts: dict[str, int] = {}
    if platform in (None, "Linux"):
        categories += _category_counts(
            linux_engine, [(name, name) for name in LINUX_CATEGORIES], LINUX_TIME_COLUMN,
            LINUX_CATEGORIES, "Linux", start,
        )
        hosts.update(_host_counts(linux_engine, list(LINUX_HOST_TABLES), LINUX_TIME_COLUMN, start))
    if platform in (None, "Windows"):
        windows_tables = [(f"{name}_logs", name) for name in WINDOWS_CATEGORIES]
        categories += _category_counts(
            windows_engine, windows_tables, WINDOWS_TIME_COLUMN, WINDOWS_CATEGORIES, "Windows", start
        )
        merged = _host_counts(windows_engine, [table for table, _key in windows_tables], WINDOWS_TIME_COLUMN, start)
        for host, count in merged.items():
            hosts[host] = hosts.get(host, 0) + count
    categories.sort(key=lambda item: item["count"], reverse=True)
    top_hosts = sorted(hosts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return {
        "topCategories": categories[:limit],
        "topHosts": [{"host": host, "count": count} for host, count in top_hosts],
    }


def logs_stats(range_key: str = "7d", limit: int = 10, platform: str | None = None) -> dict:
    """GET /api/v1/logs/stats 的数据体：窗口内 TOP 类别与 TOP 主机（按本地时区无关的窗口计数）。"""
    if range_key not in ("24h", "7d", "30d"):
        raise ValueError(f"invalid range: {range_key}（可选 24h/7d/30d）")
    if not 1 <= int(limit) <= 50:
        raise ValueError("limit 必须在 1～50 之间")
    platform = _normalize_platform(platform)
    cache_key = f"stats:{range_key}:{int(limit)}:{platform or 'all'}"
    payload, cached, age_ms = _cached(cache_key, STATS_CACHE_TTL_SECONDS,
                                      lambda: _compute_stats(range_key, int(limit), platform))
    with _cache_lock:
        generated_at = _cache.get(cache_key, (None, _iso(_now_utc()), None))[1]
    return dict(payload, range=range_key, generatedAt=generated_at, cached=cached, ageMs=age_ms)


# --- 心跳历史：进程内环形缓冲 -----------------------------------------------------

HISTORY_MAX_SAMPLES = 720          # 5 秒 × 720 = 1 小时
SAMPLE_INTERVAL_SECONDS = 5        # 与 _status_watchdog 的巡检周期一致

_samples: deque[dict] = deque(maxlen=HISTORY_MAX_SAMPLES)
_samples_lock = threading.Lock()


def record_sample(snapshot: dict, sampled_at: datetime | None = None) -> None:
    """由 watchdog 每 5 秒调用：把当前心跳快照压成一个采样点（不触碰数据库）。"""
    now = sampled_at or _now_utc()
    clients = snapshot.get("clients") or []
    delays = [int(client.get("heartbeatDelaySec") or 0) for client in clients]
    sample = {
        "t": _iso(now),
        "total": int(snapshot.get("total_clients") or 0),
        "online": int(snapshot.get("online_clients") or 0),
        "delayed": int(snapshot.get("delayed_clients") or 0),
        "offline": int(snapshot.get("offline_clients") or 0),
        "avgDelay": round(sum(delays) / len(delays), 1) if delays else 0.0,
        "clients": {
            str(client.get("client_id")): {
                "delay": int(client.get("heartbeatDelaySec") or 0),
                "status": str(client.get("status") or "OFFLINE"),
            }
            for client in clients
        },
    }
    with _samples_lock:
        _samples.append(sample)


def clear_samples() -> None:
    with _samples_lock:
        _samples.clear()


def heartbeat_history(minutes: int = 15) -> dict:
    """GET /api/v1/monitor/history 的数据体：窗口内的全局序列 + 每客户端延迟序列。

    纯内存读取：数据库故障时该接口依然可用（它本来就不查库）。
    采样点不足或进程刚重启时返回空序列，由页面显示空态——
    绝不能把「没有历史」和「查询失败」混为一谈（本仓库反复强调的反模式）。
    """
    minutes = int(minutes)
    if not 1 <= minutes <= 60:
        raise ValueError("minutes 必须在 1～60 之间")
    now = _now_utc()
    cutoff = _iso(now - timedelta(minutes=minutes))
    with _samples_lock:
        window = [dict(sample, clients=dict(sample["clients"])) for sample in _samples if sample["t"] >= cutoff]

    points = [
        {"t": sample["t"], "total": sample["total"], "online": sample["online"],
         "delayed": sample["delayed"], "offline": sample["offline"], "avgDelay": sample["avgDelay"]}
        for sample in window
    ]
    series: dict[str, list] = {}
    latest_status: dict[str, str] = {}
    for sample in window:
        for client_id, info in sample["clients"].items():
            series.setdefault(client_id, []).append([sample["t"], info["delay"]])
            latest_status[client_id] = info["status"]
    clients = [
        {"clientId": client_id, "status": latest_status[client_id], "points": points_series}
        for client_id, points_series in sorted(series.items())
    ]
    return {
        "intervalSec": SAMPLE_INTERVAL_SECONDS,
        "windowMinutes": minutes,
        "samples": len(points),
        "points": points,
        "clients": clients,
    }
