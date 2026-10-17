"""图表聚合接口契约：参数校验、TTL 缓存、心跳历史环形缓冲。

硬约束：conftest 已禁用 Engine.connect/begin，任何漏掉 mock 的数据库访问都会
直接 AssertionError——因此这些用例天然验证「校验发生在查库之前」。
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, metrics


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean_state():
    """聚合缓存与心跳采样都是进程内状态，用例之间必须隔离。"""
    metrics.clear_cache()
    metrics.clear_samples()
    yield
    metrics.clear_cache()
    metrics.clear_samples()


# --- 参数校验必须在查库之前返回 400/422 -----------------------------------------

@pytest.mark.parametrize("url", [
    "/api/v1/logs/trend?range=nope",
    "/api/v1/logs/trend?bucket=fortnight",
    "/api/v1/logs/trend?range=30d&bucket=minute",   # 43202 个点，超上限
    "/api/v1/logs/trend?platform=mac",
    "/api/v1/logs/stats?range=1h",
    "/api/v1/logs/stats?range=7d&platform=mac",
])
def test_trend_and_stats_reject_invalid_params_before_db(client, url):
    """非法参数在触碰数据库之前就返回 400（conftest 禁连库，越权会直接失败）。"""
    assert client.get(url).status_code == 400


@pytest.mark.parametrize("url", [
    "/api/v1/logs/stats?limit=0",
    "/api/v1/logs/stats?limit=51",
    "/api/v1/monitor/history?minutes=0",
    "/api/v1/monitor/history?minutes=61",
])
def test_chart_query_bounds_return_422(client, url):
    assert client.get(url).status_code == 422


# --- 趋势：UTC 桶键与空桶填充 ---------------------------------------------------

def test_trend_points_are_utc_zulu_and_zero_filled(monkeypatch):
    """桶键必须以 Z 结尾（UTC）：偏 8 小时的趋势图/热力图就是漏了这一步。"""
    monkeypatch.setattr(metrics, "_bucket_counts", lambda engine, table_columns, bucket, start: {})
    data = metrics.logs_trend("1h")
    assert data["bucket"] == "minute" and data["range"] == "1h"
    assert 60 <= len(data["points"]) <= 62
    assert all(point["bucket"].endswith("Z") for point in data["points"])
    assert all(point["bucket"] == point["bucket"].replace(" ", "") for point in data["points"])
    assert all(point["total"] == point["linux"] + point["windows"] == 0 for point in data["points"])
    assert data["generatedAt"].endswith("Z")
    assert data["cached"] is False and data["ageMs"] == 0


def test_trend_bucket_padding_matches_query_result(monkeypatch):
    """查询命中的桶必须落到对应时间点上，其余补 0；序列必须单调递增。"""
    captured = {}

    def fake_counts(engine, table_columns, bucket, start):
        captured["start"] = start
        captured["bucket"] = bucket
        # 只给「最后一个桶」造数据，验证补 0 不会把命中值挤掉
        return {}

    monkeypatch.setattr(metrics, "_bucket_counts", fake_counts)
    data = metrics.logs_trend("24h", bucket="hour")
    keys = [point["bucket"] for point in data["points"]]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)          # 无重复桶
    assert data["bucket"] == "hour"
    assert captured["bucket"] == "hour"


def test_trend_cache_hit_avoids_recompute(monkeypatch):
    """同一参数在 TTL 内只允许触发一次真实聚合（否则 2 秒轮询会打爆 12 张表）。"""
    calls = []

    def fake_counts(engine, table_columns, bucket, start):
        calls.append(bucket)
        return {}

    monkeypatch.setattr(metrics, "_bucket_counts", fake_counts)
    first = metrics.logs_trend("24h")
    second = metrics.logs_trend("24h")

    assert len(calls) == 2                     # 第一次 Linux + Windows 各一次；第二次全命中
    assert first["cached"] is False
    assert second["cached"] is True and second["ageMs"] >= 0
    assert second["generatedAt"] == first["generatedAt"]
    assert second["points"] == first["points"]
    # platform 维度分开缓存
    metrics.logs_trend("24h", platform="Linux")
    assert len(calls) == 3


def test_trend_query_failure_returns_500_not_empty(monkeypatch):
    """查询失败必须是 500，不能降级成「全 0 趋势」——那是把故障伪装成没有数据。"""
    def boom(*args, **kwargs):
        raise RuntimeError("mysql down")

    monkeypatch.setattr(metrics, "_bucket_counts", boom)
    failing = TestClient(main.app, raise_server_exceptions=False)
    assert failing.get("/api/v1/logs/trend").status_code == 500


# --- 排行 -----------------------------------------------------------------------

def test_stats_shape_and_ordering(monkeypatch):
    monkeypatch.setattr(
        metrics, "_category_counts",
        lambda *args, **kwargs: [
            {"key": "a", "label": "A", "platform": "Linux", "count": 9},
            {"key": "b", "label": "B", "platform": "Windows", "count": 5},
        ],
    )
    monkeypatch.setattr(metrics, "_host_counts", lambda *args, **kwargs: {"server-a": 7})
    # platform 过滤只查一侧引擎，假数据不会被注入两遍
    data = metrics.logs_stats("7d", 10, platform="Linux")
    assert [item["count"] for item in data["topCategories"]] == [9, 5]
    assert data["topHosts"] == [{"host": "server-a", "count": 7}]
    assert data["range"] == "7d" and data["cached"] is False
    assert data["generatedAt"].endswith("Z")


def test_stats_platform_filter_skips_other_engine(monkeypatch):
    seen = []

    def fake_category_counts(engine, tables, column, labels, platform, start):
        seen.append(platform)
        return []

    monkeypatch.setattr(metrics, "_category_counts", fake_category_counts)
    monkeypatch.setattr(metrics, "_host_counts", lambda *args, **kwargs: {})
    metrics.logs_stats("7d", 10, platform="windows")
    assert seen == ["Windows"]


def test_stats_limit_is_applied(monkeypatch):
    monkeypatch.setattr(
        metrics, "_category_counts",
        lambda *args, **kwargs: [{"key": str(i), "label": str(i), "platform": "Linux", "count": 100 - i}
                                 for i in range(20)],
    )
    monkeypatch.setattr(metrics, "_host_counts", lambda *args, **kwargs: {f"h{i}": i for i in range(20)})
    data = metrics.logs_stats("7d", 5)
    assert len(data["topCategories"]) == 5
    assert len(data["topHosts"]) == 5


# --- 心跳历史：环形缓冲 ---------------------------------------------------------

def _snapshot(delays: dict[str, int]) -> dict:
    clients = [
        {"client_id": client_id, "heartbeatDelaySec": delay,
         "status": "ONLINE" if delay <= 10 else ("DELAYED" if delay <= 30 else "OFFLINE")}
        for client_id, delay in delays.items()
    ]
    online = sum(1 for c in clients if c["status"] == "ONLINE")
    delayed = sum(1 for c in clients if c["status"] == "DELAYED")
    return {
        "total_clients": len(clients), "online_clients": online,
        "delayed_clients": delayed, "offline_clients": len(clients) - online - delayed,
        "clients": clients,
    }


def test_history_empty_returns_empty_series_not_error(client):
    """刚重启时历史为空：必须是空序列（页面显示空态），不能是 500。"""
    payload = client.get("/api/v1/monitor/history").json()
    assert payload["code"] == 0
    assert payload["data"]["points"] == []
    assert payload["data"]["clients"] == []
    assert payload["data"]["intervalSec"] == 5


def test_record_sample_builds_global_and_client_series():
    now = metrics._now_utc()
    metrics.record_sample(_snapshot({"a": 4, "b": 35}), sampled_at=now - timedelta(seconds=5))
    metrics.record_sample(_snapshot({"a": 6, "b": 40}), sampled_at=now)

    data = metrics.heartbeat_history(15)
    assert data["samples"] == 2
    assert data["points"][0]["avgDelay"] == 19.5          # (4 + 35) / 2
    assert data["points"][1]["online"] == 1 and data["points"][1]["offline"] == 1

    by_id = {item["clientId"]: item for item in data["clients"]}
    assert by_id["a"]["status"] == "ONLINE"
    assert by_id["a"]["points"] == [
        [data["points"][0]["t"], 4],
        [data["points"][1]["t"], 6],
    ]
    assert by_id["b"]["points"][-1][1] == 40
    assert all(t.endswith("Z") for t, _delay in by_id["a"]["points"])


def test_history_window_filters_old_samples():
    now = metrics._now_utc()
    metrics.record_sample(_snapshot({"a": 1}), sampled_at=now - timedelta(minutes=30))
    metrics.record_sample(_snapshot({"a": 2}), sampled_at=now - timedelta(minutes=1))
    assert metrics.heartbeat_history(15)["samples"] == 1
    assert metrics.heartbeat_history(60)["samples"] == 2


def test_watchdog_feeds_history_buffer():
    """接线守卫：历史缓冲的数据源是 watchdog 每轮巡检，断了它图表就永远是空的。"""
    import inspect

    assert "record_sample" in inspect.getsource(main._status_watchdog)


# --- 分类日志的事件维度统计 -----------------------------------------------------

@pytest.fixture
def event_stats_db(monkeypatch):
    """替换 metrics 的数据访问层：记录 SQL，并按查询形状返回固定结果。"""
    calls = {"one": [], "rows": []}

    def fake_one(engine, sql, params=None):
        calls["one"].append(sql)
        return {"n": 7} if "DISTINCT" in sql else {"n": 100}

    def fake_rows(engine, sql, params=None):
        calls["rows"].append((sql, dict(params or {})))
        if "provider_name" in sql:
            return [{"k": 4107, "p": "Microsoft-Windows-Example", "n": 40}]
        if "`event_id` AS k" in sql:
            return [{"k": 4107, "n": 40}, {"k": 4624, "n": 30}, {"k": None, "n": 5}]
        return [{"k": "SYSLOG", "n": 80}, {"k": "USER_AUTH", "n": 20}]

    monkeypatch.setattr(metrics, "one", fake_one)
    monkeypatch.setattr(metrics, "rows", fake_rows)
    return calls


def test_event_stats_windows_groups_by_event_id(event_stats_db):
    data = metrics.category_event_stats("application", "Windows")
    assert data["groupBy"] == "event_id"
    assert [item["key"] for item in data["items"]] == ["4107", "4624", "（空）"]
    # 未收录编号回退到该事件最常见的 provider：这是数据事实，不臆造含义
    assert data["items"][0]["label"] == "Microsoft-Windows-Example"
    assert data["items"][1]["label"] == "登录成功"
    assert data["items"][2]["label"] == ""


def test_event_stats_linux_groups_by_type(event_stats_db):
    """Linux 的 event_id 是审计流水号，事件维度必须用 type。"""
    data = metrics.category_event_stats("authentication_session", "Linux")
    assert data["groupBy"] == "type"
    assert data["items"][0] == {"key": "SYSLOG", "label": "系统日志", "count": 80}
    assert data["items"][1]["label"] == "用户认证"
    assert all("`type`" in sql for sql, _ in event_stats_db["rows"])


def test_event_stats_limit_is_capped_at_ten(monkeypatch):
    """需求：饼图/柱状图最多展示 10 类，接口也不允许超出。"""
    seen = {}
    monkeypatch.setattr(metrics, "one", lambda engine, sql, params=None: {"n": 1})

    def fake_rows(engine, sql, params=None):
        if "provider_name" in sql:
            return []
        seen["limit"] = params["limit"]
        return []

    monkeypatch.setattr(metrics, "rows", fake_rows)
    metrics.category_event_stats("security", "Windows", limit=50)
    assert metrics.EVENT_STATS_MAX_LIMIT == 10
    assert seen["limit"] == 10
