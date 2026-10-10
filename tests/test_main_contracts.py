"""main.py 的接口契约：路由与文档一致、心跳状态判定、采集降级行为、SSE 摘要字段。

硬约束：所有用例都 monkeypatch 掉落库函数，且 conftest 已禁用 Engine.connect/begin，
因此整个文件不会连接任何数据库，也不会写入真实数据。

注意：这里刻意不使用 `with TestClient(...)`，以免触发 lifespan（建表 + 启动 watchdog）。
"""

import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

REPO_ROOT = Path(__file__).resolve().parents[1]


class RecordingQueue:
    """替代 main.event_queue，记录 SSE 事件而不涉及真实协程队列。"""

    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def queue(monkeypatch):
    recorder = RecordingQueue()
    monkeypatch.setattr(main, "event_queue", recorder)
    return recorder


# --- 路由与文档一致性 ---------------------------------------------------------

DOC_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "WebSocket")
SKIP_PATHS = ("/docs", "/redoc", "/openapi.json", "/static")


def _documented_routes() -> set[tuple[str, str]]:
    text = (REPO_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    pattern = r"^\|\s*(" + "|".join(DOC_METHODS) + r")\s*\|\s*`([^`]+)`\s*\|"
    return {(method, path) for method, path in re.findall(pattern, text, re.MULTILINE)}


def _app_routes() -> set[tuple[str, str]]:
    found = set()
    for route in main.app.routes:
        path = getattr(route, "path", None)
        if not path or path.startswith(SKIP_PATHS):
            continue
        methods = getattr(route, "methods", None)
        if methods:
            for method in methods - {"HEAD", "OPTIONS"}:
                found.add((method.upper(), path))
        elif path.startswith("/ws"):
            found.add(("WebSocket", path))
    return found


def test_documented_routes_match_application():
    documented = _documented_routes()
    assert documented, "docs/API.md 的接口总表解析为空，测试本身失效"
    assert documented == _app_routes()


def test_documented_route_count():
    """docs/API.md §2 的接口总表当前有 19 条；新增/删除接口时同步更新此断言。"""
    assert len(_documented_routes()) == 19


# --- 心跳状态阈值与载荷 -------------------------------------------------------

@pytest.mark.parametrize(
    "age,expected",
    [(0, "ONLINE"), (10, "ONLINE"), (10.001, "DELAYED"), (30, "DELAYED"), (30.001, "OFFLINE"), (3600, "OFFLINE")],
)
def test_assess_status_thresholds(age, expected):
    assert main._assess_status(age) == expected


def test_client_payload_recomputes_status_and_delay():
    from app.models import ClientStatus

    client = ClientStatus(
        client_id="sim-linux-001", hostname="server-a", ip="10.0.0.9",
        system="Linux", latency=3, last_heartbeat=datetime(2026, 1, 1, 0, 0, 0), status="OFFLINE",
    )
    payload = main._client_payload(client, datetime(2026, 1, 1, 0, 0, 5))
    assert payload["status"] == "ONLINE"
    assert payload["heartbeatDelaySec"] == 5
    assert payload["client_id"] == "sim-linux-001"


def test_build_snapshot_counts_states(monkeypatch):
    from app.models import ClientStatus

    now = main._utcnow()
    rows = [
        ClientStatus(client_id="a", hostname="a", ip="", system="Linux", latency=0, last_heartbeat=now, status="OFFLINE"),
        ClientStatus(client_id="b", hostname="b", ip="", system="Linux", latency=0, last_heartbeat=now, status="OFFLINE"),
    ]

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return rows

    class FakeSession:
        def execute(self, stmt):
            return FakeResult()

    snapshot = main.build_snapshot(FakeSession())
    assert snapshot["total_clients"] == 2
    assert snapshot["online_clients"] == 2
    assert snapshot["offline_clients"] == 0


# --- 采集接口 -----------------------------------------------------------------

@pytest.fixture
def no_db_write(monkeypatch):
    """只跳过真正的写库：解析仍走真实实现，用于验证解析→响应→SSE 的契约。"""
    monkeypatch.setattr(main, "_persist_rows", lambda engine, entries: (len(entries), []))


def test_unsupported_platform_returns_404(client):
    response = client.post("/api/v1/logs/foo", json=[])
    assert response.status_code == 404
    assert response.json()["detail"] == "unsupported platform"


def test_platform_is_case_insensitive(client, no_db_write):
    assert client.post("/api/v1/logs/LINUX", json=[]).status_code == 200


def test_ingest_normalized_payload_is_parsed_and_streamed(client, queue, no_db_write):
    response = client.post("/api/v1/logs/linux", json=[{
        "client_id": "linux-001",
        "normalized": {
            "event_time": "2026-10-10T08:00:00",
            "event_id": 1001,
            "category": "file_object_access",
            "type": "FILE_ACCESS",
        },
    }])
    assert response.status_code == 200
    assert response.json() == {
        "accepted": 1, "persisted": True, "inserted": 1, "skipped": 0, "failed": 0, "errors": [],
    }
    assert queue.items == [{
        "platform": "linux",
        "category": "file_object_access",
        "eventTime": "2026-10-10T08:00:00",
    }]


def test_ingest_raw_syslog_goes_to_authentication_session(client, queue, no_db_write):
    response = client.post("/api/v1/logs/linux", json=[{
        "client_id": "linux-001",
        "timestamp": "2026-10-10T08:00:00Z",
        "log": "Oct 10 16:00:01 server-a CRON[4359]: pam_unix(cron:session): session opened for user root(uid=0)",
    }])
    assert response.json()["inserted"] == 1
    assert queue.items[0]["category"] == "authentication_session"
    assert queue.items[0]["eventTime"] == "2026-10-10T08:00:00"


@pytest.mark.parametrize("channel_key", ["channel", "Channel"])
def test_ingest_windows_event_is_parsed(client, queue, no_db_write, channel_key):
    response = client.post("/api/v1/logs/windows", json=[{
        "client_id": "windows-001",
        "TimeGenerated": "2026-10-10 08:00:00 +0800",
        "EventID": "4624",
        "RecordNumber": 88,
        "SourceName": "Microsoft-Windows-Security-Auditing",
        "ComputerName": "win-a",
        channel_key: "Security",
    }])
    assert response.json()["inserted"] == 1
    assert queue.items[0]["category"] == "Security"
    assert queue.items[0]["eventTime"] == "2026-10-10T00:00:00"   # +0800 → UTC


def test_ingest_parse_failure_is_reported_as_parse(client, queue, no_db_write):
    response = client.post("/api/v1/logs/linux", json=[{"client_id": "c", "log": "没有任何时间信息的一行"}])
    payload = response.json()
    assert payload["persisted"] is False
    assert payload["skipped"] == 1 and payload["failed"] == 0
    assert payload["errors"][0]["stage"] == "parse"


def test_ingest_insert_failure_is_reported_as_insert(client, queue):
    """不 mock 写入：conftest 已禁用 Engine.begin，因此入库必定失败且要能报出来。"""
    response = client.post("/api/v1/logs/linux", json=[{
        "client_id": "c", "timestamp": "2026-10-10T08:00:00Z", "log": "x",
    }])
    payload = response.json()
    assert response.status_code == 200
    assert payload["persisted"] is False
    assert payload["inserted"] == 0 and payload["failed"] == 1 and payload["skipped"] == 0
    assert payload["errors"][0]["stage"] == "insert"
    assert len(queue.items) == 1


@pytest.mark.parametrize("payload", [[1], ["text"], [None], [True]])
def test_ingest_non_object_records_are_reported_not_raised(client, queue, no_db_write, payload):
    response = client.post("/api/v1/logs/linux", json=payload)
    assert response.status_code == 200
    assert response.json()["skipped"] == 1
    assert response.json()["errors"][0]["stage"] == "parse"
    assert queue.items[0] == {"platform": "linux", "category": None, "eventTime": None}


def test_ingest_accepts_single_object_body(client, queue, no_db_write):
    response = client.post("/api/v1/logs/linux", json={"timestamp": "2026-10-10T08:00:00Z", "log": "x"})
    assert response.json()["inserted"] == 1


# --- 心跳接口 -----------------------------------------------------------------

def test_heartbeat_success_returns_pong_and_broadcasts(client, monkeypatch):
    snapshot = {"total_clients": 1, "online_clients": 1, "delayed_clients": 0, "offline_clients": 0, "clients": []}
    monkeypatch.setattr(main, "persist_heartbeats", lambda payload: snapshot)
    broadcasted = []

    async def fake_broadcast(message):
        broadcasted.append(message)

    monkeypatch.setattr(main.ws_manager, "broadcast", fake_broadcast)
    response = client.post("/api/v1/heartbeat", json=[{"client_id": "sim-linux-001"}])
    assert response.status_code == 200
    assert response.json() == "pong"
    assert broadcasted == [snapshot]


def test_heartbeat_persist_failure_returns_503(client, monkeypatch):
    def boom(payload):
        raise RuntimeError("mysql down")

    monkeypatch.setattr(main, "persist_heartbeats", boom)
    response = client.post("/api/v1/heartbeat", json=[{"client_id": "c"}])
    assert response.status_code == 503
    assert response.json()["detail"] == "heartbeat persist failed"


def test_heartbeat_rejects_non_list_body(client):
    assert client.post("/api/v1/heartbeat", json={"client_id": "c"}).status_code == 422


# --- 查询参数校验（在访问数据库之前返回）--------------------------------------

@pytest.mark.parametrize("url", [
    "/api/v1/logs/categories/bad-name",
    "/api/v1/logs/categories/%E4%B8%AD%E6%96%87",
    "/api/v1/logs/categories/a.b",
])
def test_invalid_category_returns_400_before_db_access(client, url):
    response = client.get(url)
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid category"


@pytest.mark.parametrize("url", [
    "/api/v1/logs/recent?limit=0",
    "/api/v1/logs/recent?limit=101",
    "/api/v1/logs/export?size=0",
    "/api/v1/logs/export?size=10001",
    "/api/v1/evidence?size=101",
])
def test_query_bounds_return_422(client, url):
    assert client.get(url).status_code == 422


def test_health_route_reports_three_databases(client, monkeypatch):
    monkeypatch.setattr(main, "db_health", lambda: {"linux_logs": True, "windows_logs": False, "log_fusion": True})
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["databases"] == {"linux_logs": True, "windows_logs": False, "log_fusion": True}


def test_health_reports_realtime_connection_counts(client, monkeypatch):
    """实时连接数必须可从 /health 看到：连接被占满时页面整体卡死，服务端却毫秒级返回。"""
    monkeypatch.setattr(main, "sse_clients", 3)
    monkeypatch.setattr(type(main.ws_manager), "connection_count", property(lambda self: 2))
    payload = client.get("/health").json()
    assert payload["realtime"] == {"sse_clients": 3, "ws_clients": 2}


def test_static_responses_disable_cache(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]


def test_vendored_libraries_are_cacheable(client):
    """自托管的版本化第三方库可以长期缓存（页面不再依赖公网 CDN）。"""
    response = client.get("/static/vendor/htmx-2.0.4.min.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_dashboard_references_only_local_libraries(client):
    """模板不得再引用公网 CDN，否则代理/DNS 异常时首屏会被拖慢或卡死。"""
    html = client.get("/").text
    assert "cdn.jsdelivr.net" not in html
    assert "/static/vendor/bootstrap-5.3.3.min.css" in html
    for name in ("htmx-2.0.4.min.js", "alpinejs-3.14.8.min.js", "echarts-5.5.1.min.js"):
        assert f"/static/vendor/{name}" in html


def test_html_pages_are_not_cached(client):
    """页面禁用缓存：否则浏览器可能继续使用引用了旧依赖列表的旧页面。"""
    response = client.get("/")
    assert "no-store" in response.headers["cache-control"]


def test_page_has_dependency_self_check(client):
    """依赖加载失败时必须给出可见提示，而不是留下被 x-cloak 隐藏的白页。"""
    html = client.get("/").text
    assert 'id="lf-boot-warning"' in html
    assert "window.Alpine" in html and "window.htmx" in html and "window.echarts" in html


def test_app_js_releases_long_lived_connections(client):
    """每个标签页只保留 1 条 WebSocket 长连接；页面卸载时要主动断开。"""
    js = client.get("/static/app.js").text
    assert "pagehide" in js
    assert "closeStreams" in js
    assert "AbortController" in js


def test_app_js_does_not_open_eventsource(client):
    """前端不再订阅 SSE。

    每页 1 条 SSE + 1 条 WebSocket 会占满浏览器同一地址的 6 条 HTTP/1.1 连接额度，
    导致所有 REST 请求（含自动刷新）永久排队；改由 WebSocket 推送 + 定时 REST 刷新。
    SSE 接口本身保留给外部消费方。
    """
    js = client.get("/static/app.js").text
    assert "EventSource" not in js
    assert "/api/v1/logs/stream" not in js
    assert "summaryTimer" in js and "pollTimer" in js


def test_app_js_is_requested_with_cache_busting_token(client):
    """app.js 带版本参数：旧浏览器缓存过无 no-store 头的副本，需要一次强制定位。"""
    assert "/static/app.js?v=" in client.get("/").text
