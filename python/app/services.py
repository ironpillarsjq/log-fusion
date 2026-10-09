from datetime import datetime, timedelta, timezone
from .catalog import LINUX_CATEGORIES, WINDOWS_CATEGORIES, LINUX_TABLE_COLUMNS
from .db import linux_engine, windows_engine, rows, one


def categories() -> list[dict]:
    return ([{"key": k, "label": v, "platform": "Linux"} for k, v in LINUX_CATEGORIES.items()] +
            [{"key": k, "label": v, "platform": "Windows"} for k, v in WINDOWS_CATEGORIES.items()])


def _safe_name(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError("invalid category")
    return value


def query_category(category: str, platform: str, page: int = 1, size: int = 20, keyword: str | None = None) -> dict:
    page, size = max(1, page), min(100, max(1, size))
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
            condition = " WHERE hostname LIKE :keyword OR source_address LIKE :keyword OR type LIKE :keyword"
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


def recent_rows(limit: int = 20) -> list[dict]:
    # The two logical databases use separate SQLAlchemy engines, so merge the
    # small recent windows in Python instead of issuing a cross-database UNION.
    linux_parts = [f"SELECT event_time AS event_time, category, type, result, hostname, source_address FROM `{name}`" for name in LINUX_CATEGORIES]
    windows_parts = [f"SELECT time_created AS event_time, channel AS category, event_id AS type, level AS result, computer AS hostname, NULL AS source_address FROM `{name}_logs`" for name in WINDOWS_CATEGORIES]
    result = rows(linux_engine, " UNION ALL ".join(linux_parts) + " ORDER BY event_time DESC LIMIT :limit", {"limit": limit})
    result += rows(windows_engine, " UNION ALL ".join(windows_parts) + " ORDER BY event_time DESC LIMIT :limit", {"limit": limit})
    return sorted(result, key=lambda item: item.get("event_time") or datetime.min, reverse=True)[:limit]
