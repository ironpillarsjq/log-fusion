from datetime import datetime, timedelta, timezone
from .catalog import LINUX_CATEGORIES, WINDOWS_CATEGORIES, LINUX_TABLE_COLUMNS
from .db import linux_engine, windows_engine, rows, one


def categories() -> list[dict]:
    return ([{"key": k, "label": v, "platform": "Linux"} for k, v in LINUX_CATEGORIES.items()] +
            [{"key": k, "label": v, "platform": "Windows"} for k, v in WINDOWS_CATEGORIES.items()])


def _safe_name(value: str) -> str:
    # 只允许 ASCII 字母、数字和下划线：str.isalnum() 对中文等非 ASCII 字符也为 True，
    # 若不额外限制，非 ASCII 表名会通过校验并被拼进 SQL。
    if not value.replace("_", "").isalnum() or not value.isascii():
        raise ValueError("invalid category")
    return value


def query_category(
    category: str,
    platform: str,
    page: int = 1,
    size: int = 20,
    keyword: str | None = None,
    size_cap: int = 100,
) -> dict:
    """分类分页查询。

    `size_cap` 是单次返回上限：分类列表接口用默认 100，CSV 导出传 10000，
    否则导出结果会被列表接口的上限静默截断成 100 行。
    """
    page, size = max(1, page), min(max(1, size_cap), max(1, size))
    category = _safe_name(category.lower())
    if platform.lower() == "windows":
        table = f"{category}_logs"
        columns = ["id", "time_created", "event_id", "level", "channel", "computer", "provider_name", "eventdata", "imported_at"]
        engine = windows_engine
        condition = ""
        params = {"limit": size, "offset": (page - 1) * size}
        if keyword:
            condition = " WHERE computer LIKE :keyword OR provider_name LIKE :keyword OR event_id LIKE :keyword"
            params["keyword"] = f"%{keyword}%"
    else:
        table = category
        columns = LINUX_TABLE_COLUMNS.get(category, ["event_time", "event_id", "category", "type", "result", "tips"])
        engine = linux_engine
        condition = ""
        params = {"limit": size, "offset": (page - 1) * size}
        if keyword:
            # 只有实际存在的列能参与关键词匹配：process_command_execution、
            # file_object_access、security_policy_config_change 没有 hostname/
            # source_address，硬拼这些列会让接口直接 500。
            searchable = [field for field in ("hostname", "source_address", "type") if field in columns]
            if searchable:
                condition = " WHERE " + " OR ".join(f"{field} LIKE :keyword" for field in searchable)
                params["keyword"] = f"%{keyword}%"
    selected = ", ".join(f"`{c}`" for c in columns)
    total = one(engine, f"SELECT COUNT(*) AS n FROM `{table}`{condition}", params)["n"]
    data = rows(engine, f"SELECT {selected} FROM `{table}`{condition} ORDER BY 1 DESC LIMIT :limit OFFSET :offset", params)
    return {"items": data, "page": page, "size": size, "total": total, "category": category, "platform": platform}


def summary() -> dict:
    result = {"total": 0, "by_platform": {"Linux": 0, "Windows": 0}, "categories": []}
    for platform, mapping in (("Linux", LINUX_CATEGORIES), ("Windows", WINDOWS_CATEGORIES)):
        engine = linux_engine if platform == "Linux" else windows_engine
        for key, label in mapping.items():
            table = key if platform == "Linux" else f"{key}_logs"
            count = one(engine, f"SELECT COUNT(*) AS n FROM `{table}`")["n"]
            result["total"] += count
            result["by_platform"][platform] += count
            result["categories"].append({"key": key, "label": label, "platform": platform, "count": count})
    return result


RECENT_LINUX_FIELDS = ("event_time", "category", "type", "result", "hostname", "source_address")


def _linux_recent_select(table: str) -> str:
    """按表实际拥有的列生成 SELECT，缺失列用 NULL 占位。

    7 张 Linux 表都有 event_time/category/type/result，但 hostname 与
    source_address 只在部分表存在（真实列见 docs/DATABASE.md §4.3）。
    """
    available = set(LINUX_TABLE_COLUMNS.get(table, []))
    exprs = [f"{field} AS {field}" if field in available else f"NULL AS {field}" for field in RECENT_LINUX_FIELDS]
    return f"SELECT {', '.join(exprs)} FROM `{table}`"


def _event_time_key(item: dict) -> str:
    """最近日志的排序键：统一转成 ISO 字符串。

    两个库的时间列类型尚未核验，若某列返回字符串而另一列返回 datetime，
    直接用 datetime 做键会抛 `TypeError: '<' not supported between
    instances of 'datetime.datetime' and 'str'`。统一转字符串后仍按时间
    先后排序，缺失值排在最后。
    """
    value = item.get("event_time")
    if value is None:
        return ""
    return value.isoformat() if isinstance(value, datetime) else str(value)


def recent_rows(limit: int = 20) -> list[dict]:
    # The two logical databases use separate SQLAlchemy engines, so merge the
    # small recent windows in Python instead of issuing a cross-database UNION.
    linux_parts = [_linux_recent_select(name) for name in LINUX_CATEGORIES]
    windows_parts = [f"SELECT time_created AS event_time, channel AS category, event_id AS type, level AS result, computer AS hostname, NULL AS source_address FROM `{name}_logs`" for name in WINDOWS_CATEGORIES]
    result = rows(linux_engine, " UNION ALL ".join(linux_parts) + " ORDER BY event_time DESC LIMIT :limit", {"limit": limit})
    result += rows(windows_engine, " UNION ALL ".join(windows_parts) + " ORDER BY event_time DESC LIMIT :limit", {"limit": limit})
    return sorted(result, key=_event_time_key, reverse=True)[:limit]
