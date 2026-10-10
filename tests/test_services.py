"""services.py 的查询构造契约：类别校验、取数上限、关键词列适配、缺列 NULL 占位。

这些用例对应 2026-10-10 复核中修掉的三个真实缺陷：
- 导出被列表接口 100 行上限静默截断；
- 非 ASCII 类别名通过校验并被拼进表名；
- `/recent` 与关键词查询对缺列表拼接固定列名导致 HTTP 500。
"""

import pytest

from datetime import datetime

from app import services


@pytest.fixture
def captured(monkeypatch):
    """替换数据访问层，捕获生成的 SQL 与绑定参数（不触碰数据库）。"""
    calls = {"one": [], "rows": []}

    def fake_one(engine, sql, params=None):
        calls["one"].append((sql, dict(params or {})))
        return {"n": 0}

    def fake_rows(engine, sql, params=None):
        calls["rows"].append((sql, dict(params or {})))
        return []

    monkeypatch.setattr(services, "one", fake_one)
    monkeypatch.setattr(services, "rows", fake_rows)
    return calls


def _where(sql: str) -> str:
    return sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""


# --- 类别名校验 ---------------------------------------------------------------

@pytest.mark.parametrize("value", ["authentication_session", "A1_b", "abc", "a_b_c", "123"])
def test_safe_name_accepts_ascii_alnum_underscore(value):
    assert services._safe_name(value) == value


@pytest.mark.parametrize("value", ["中文", "naïve", "bad-name", "a.b", "", "a b", "tab\tname", "a;b"])
def test_safe_name_rejects_non_ascii_and_symbols(value):
    with pytest.raises(ValueError):
        services._safe_name(value)


def test_query_category_rejects_non_ascii_without_touching_db(captured):
    with pytest.raises(ValueError):
        services.query_category("中文", "Linux")
    assert captured["rows"] == []


# --- 单次取数上限 -------------------------------------------------------------

def test_list_query_caps_size_at_100(captured):
    services.query_category("authentication_session", "Linux", 1, 1000)
    assert captured["rows"][0][1]["limit"] == 100


def test_export_cap_allows_requested_size(captured):
    services.query_category("authentication_session", "Linux", 1, 1000, size_cap=10000)
    assert captured["rows"][0][1]["limit"] == 1000


def test_export_cap_never_exceeds_its_own_ceiling(captured):
    services.query_category("authentication_session", "Linux", 1, 99999, size_cap=10000)
    assert captured["rows"][0][1]["limit"] == 10000


def test_page_lower_bound_and_offset(captured):
    services.query_category("authentication_session", "Linux", 0, 20)
    assert captured["rows"][0][1]["offset"] == 0
    services.query_category("authentication_session", "Linux", 3, 20)
    assert captured["rows"][1][1]["offset"] == 40


# --- 关键词只匹配实际存在的列 -------------------------------------------------

def test_keyword_skips_missing_columns(captured):
    services.query_category("process_command_execution", "Linux", 1, 5, keyword="x")
    where = _where(captured["rows"][0][0])
    assert "hostname" not in where
    assert "source_address" not in where
    assert "type LIKE :keyword" in where
    assert captured["rows"][0][1]["keyword"] == "%x%"


def test_keyword_uses_all_columns_when_present(captured):
    services.query_category("authentication_session", "Linux", 1, 5, keyword="x")
    where = _where(captured["rows"][0][0])
    assert "hostname LIKE :keyword" in where
    assert "source_address LIKE :keyword" in where
    assert "type LIKE :keyword" in where


def test_keyword_uses_only_source_address_for_network_category(captured):
    services.query_category("network_ipc_communication", "Linux", 1, 5, keyword="x")
    where = _where(captured["rows"][0][0])
    assert "source_address LIKE :keyword" in where
    assert "hostname" not in where


def test_windows_keyword_uses_windows_columns(captured):
    services.query_category("security", "Windows", 1, 5, keyword="a")
    sql = captured["rows"][0][0]
    assert "`security_logs`" in sql
    where = _where(sql)
    assert "computer LIKE :keyword" in where
    assert "provider_name LIKE :keyword" in where
    assert "event_id LIKE :keyword" in where


# --- /recent 缺列 NULL 占位 ---------------------------------------------------

RECENT_FIELDS = ("event_time", "category", "type", "result", "hostname", "source_address")

MISSING_BY_TABLE = {
    "authentication_session": set(),
    "account_security_change": set(),
    "process_command_execution": {"hostname", "source_address"},
    "file_object_access": {"hostname", "source_address"},
    "network_ipc_communication": {"hostname"},
    "system_service_audit_lifecycle": {"source_address"},
    "security_policy_config_change": {"hostname", "source_address"},
}


@pytest.mark.parametrize("table,missing", sorted(MISSING_BY_TABLE.items()))
def test_recent_select_placeholders_match_real_schema(table, missing):
    sql = services._linux_recent_select(table)
    for field in RECENT_FIELDS:
        expected = f"NULL AS {field}" if field in missing else f"{field} AS {field}"
        assert expected in sql, f"{table}: 期望 {expected}，实际 {sql}"
    assert sql.count(" AS ") == len(RECENT_FIELDS)


def test_recent_select_covers_every_linux_category():
    assert set(MISSING_BY_TABLE) == set(services.LINUX_CATEGORIES)


def test_recent_rows_merges_sorts_and_truncates(monkeypatch):
    linux = [{"event_time": "2026-01-01T00:00:00"}, {"event_time": "2026-03-01T00:00:00"}]
    windows = [{"event_time": "2026-02-01T00:00:00"}]

    def fake_rows(engine, sql, params=None):
        return list(windows) if engine is services.windows_engine else list(linux)

    monkeypatch.setattr(services, "rows", fake_rows)
    out = services.recent_rows(2)
    assert [row["event_time"] for row in out] == ["2026-03-01T00:00:00", "2026-02-01T00:00:00"]


def test_recent_rows_tolerates_null_event_time(monkeypatch):
    linux = [{"event_time": None}]
    windows = [{"event_time": "2026-02-01T00:00:00"}]
    monkeypatch.setattr(
        services, "rows",
        lambda engine, sql, params=None: list(windows) if engine is services.windows_engine else list(linux),
    )
    out = services.recent_rows(5)
    assert [row["event_time"] for row in out] == ["2026-02-01T00:00:00", None]


def test_recent_rows_handles_mixed_event_time_types(monkeypatch):
    """两个库的时间列类型未核验：datetime 与字符串混用时不能抛 TypeError。"""
    march = datetime(2026, 3, 1)
    linux = [{"event_time": march}]
    windows = [{"event_time": "2026-02-01T00:00:00"}]
    monkeypatch.setattr(
        services, "rows",
        lambda engine, sql, params=None: list(windows) if engine is services.windows_engine else list(linux),
    )
    out = services.recent_rows(5)
    assert out[0]["event_time"] == march
    assert out[1]["event_time"] == "2026-02-01T00:00:00"


# --- 汇总 ---------------------------------------------------------------------

def test_summary_aggregates_all_twelve_categories(monkeypatch):
    monkeypatch.setattr(services, "one", lambda engine, sql, params=None: {"n": 2})
    result = services.summary()
    assert result["total"] == 24
    assert result["by_platform"] == {"Linux": 14, "Windows": 10}
    assert len(result["categories"]) == 12


def test_categories_lists_linux_and_windows():
    data = services.categories()
    assert {item["platform"] for item in data} == {"Linux", "Windows"}
    assert len(data) == 12
