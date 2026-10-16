"""main.py 的接口契约：路由与文档一致、心跳状态判定、采集解析/降级、健康检查与缓存策略。

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


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def written(monkeypatch):
    """记录「将要写入业务表」的行，替代已移除的 SSE 摘要断言（`summary` 之外的真实产物）。

    只跳过落库动作，解析仍走真实实现，因此可以断言映射结果（表名、category、时间归一化）。
    """
    captured: list[tuple[int, str, dict]] = []

    def fake_persist_rows(engine, entries):
        captured.extend(entries)
        return len(entries), []

    monkeypatch.setattr(main, "_persist_rows", fake_persist_rows)
    monkeypatch.setattr(main, "_persist_evidence", lambda platform, records, event_times: (len(records), []))
    return captured


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
    """docs/API.md §2 的接口总表当前有 29 条；新增/删除接口时同步更新此断言。"""
    assert len(_documented_routes()) == 29


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
    """只跳过真正的写库：解析仍走真实实现。"""
    monkeypatch.setattr(main, "_persist_rows", lambda engine, entries: (len(entries), []))
    monkeypatch.setattr(main, "_persist_evidence", lambda platform, records, event_times: (len(records), []))


def test_unsupported_platform_returns_404(client):
    response = client.post("/api/v1/logs/foo", json=[])
    assert response.status_code == 404
    assert response.json()["detail"] == "unsupported platform"


def test_platform_is_case_insensitive(client, no_db_write):
    assert client.post("/api/v1/logs/LINUX", json=[]).status_code == 200


def test_ingest_normalized_payload_is_parsed_and_stored(client, written):
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
        "accepted": 1, "persisted": True, "inserted": 1, "skipped": 0, "failed": 0,
        "hashed": 1, "errors": [],
    }
    # 解析结果落在「准备写入的行」上：表名由 category 决定，时间已归一化
    index, table, row = written[0]
    assert index == 0
    assert table == "file_object_access"
    assert row["category"] == "file_object_access"
    assert "2026-10-10 08:00" in str(row["event_time"])


def test_ingest_raw_syslog_goes_to_authentication_session(client, written):
    response = client.post("/api/v1/logs/linux", json=[{
        "client_id": "linux-001",
        "timestamp": "2026-10-10T08:00:00Z",
        "log": "Oct 10 16:00:01 server-a CRON[4359]: pam_unix(cron:session): session opened for user root(uid=0)",
    }])
    assert response.json()["inserted"] == 1
    _index, table, row = written[0]
    assert table == "authentication_session"      # auditd/syslog 的类型映射
    assert row["type"] == "SYSLOG"
    assert row.get("account") == "root"           # 从 pam 行里抽取的账号


@pytest.mark.parametrize("channel_key", ["channel", "Channel"])
def test_ingest_windows_event_is_parsed(client, written, channel_key):
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
    _index, table, row = written[0]
    assert table == "security_logs"
    assert "2026-10-10 00:00" in str(row["time_created"])   # +0800 → UTC


def test_ingest_parse_failure_is_reported_as_parse(client, written):
    response = client.post("/api/v1/logs/linux", json=[{"client_id": "c", "log": "没有任何时间信息的一行"}])
    payload = response.json()
    assert payload["persisted"] is False
    assert payload["skipped"] == 1 and payload["failed"] == 0
    assert payload["errors"][0]["stage"] == "parse"
    assert written == []            # 解析失败就不该有任何行进入写入路径


def test_ingest_insert_failure_is_reported_as_insert(client):
    """不 mock 写入：conftest 已禁用 Engine.begin，因此入库必定失败且要能报出来。"""
    response = client.post("/api/v1/logs/linux", json=[{
        "client_id": "c", "timestamp": "2026-10-10T08:00:00Z", "log": "x",
    }])
    payload = response.json()
    assert response.status_code == 200
    assert payload["persisted"] is False
    assert payload["inserted"] == 0 and payload["failed"] == 1 and payload["skipped"] == 0
    assert payload["errors"][0]["stage"] == "insert"


@pytest.mark.parametrize("payload", [[1], ["text"], [None], [True]])
def test_ingest_non_object_records_are_reported_not_raised(client, written, payload):
    response = client.post("/api/v1/logs/linux", json=payload)
    assert response.status_code == 200
    assert response.json()["skipped"] == 1
    assert response.json()["errors"][0]["stage"] == "parse"
    assert written == []


def test_ingest_error_response_is_compact_for_large_batches(client, written):
    response = client.post("/api/v1/logs/windows", json=[{"Channel": "System"}] * 200)
    payload = response.json()
    assert response.status_code == 200
    assert payload["error_count"] == 200
    assert payload["errors_truncated"] is True
    assert len(payload["errors"]) == 8
    assert len(response.content) < 4096


def test_ingest_accepts_single_object_body(client, written):
    response = client.post("/api/v1/logs/linux", json={"timestamp": "2026-10-10T08:00:00Z", "log": "x"})
    assert response.json()["inserted"] == 1
    assert len(written) == 1


def test_ingest_logs_parse_errors_to_console(client, written, caplog):
    response = client.post("/api/v1/logs/windows", json=[{"client_id": "win-test"}])
    assert response.json()["skipped"] == 1
    assert "platform=windows index=0 client_id=win-test stage=parse" in caplog.text
    assert "时间戳" in caplog.text


def test_ingest_logs_invalid_json_without_echoing_body(client, caplog):
    response = client.post("/api/v1/logs/windows", content='{"private-log":',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "日志接收失败" in caplog.text
    assert "json_invalid" in caplog.text
    assert "private-log" not in caplog.text


def test_ingest_logs_body_decoding_failure(client, caplog):
    response = client.post("/api/v1/logs/windows", content=b'\xff',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "There was an error parsing the body" in caplog.text


def test_ingest_logs_insert_and_hash_errors(monkeypatch, caplog):
    monkeypatch.setattr(main, "_persist_rows", lambda engine, entries:
                        (0, [{"index": 0, "stage": "insert", "table": "system_logs", "reason": "insert failed"}]))
    monkeypatch.setattr(main, "_persist_evidence", lambda *args:
                        (0, [{"stage": "hash", "reason": "hash failed"}]))
    result = main.persist_logs("windows", [{"date": 1791947047, "Channel": "System"}])
    assert result["failed"] == 1
    assert "stage=insert table=system_logs" in caplog.text
    assert "hash failed" in caplog.text


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


# --- WebSocket：唯一实时通道 --------------------------------------------------

def test_websocket_sends_snapshot_on_connect(client, monkeypatch):
    """建连立即收到一条完整心跳快照（ADR-015：SSE 移除后这是唯一的主动推送路径）。"""
    snapshot = {"total_clients": 2, "online_clients": 2, "delayed_clients": 0,
                "offline_clients": 0, "clients": [{"client_id": "a"}]}
    monkeypatch.setattr(main, "_snapshot_sync", lambda: snapshot)
    with client.websocket_connect("/ws/client-monitor") as ws:
        assert ws.receive_json() == snapshot
        assert main.ws_manager.connection_count == 1
    assert main.ws_manager.connection_count == 0


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


def test_health_reports_realtime_connection_count(client, monkeypatch):
    """实时连接数必须可从 /health 看到：连接被占满时页面整体卡死，服务端却毫秒级返回。"""
    monkeypatch.setattr(type(main.ws_manager), "connection_count", property(lambda self: 2))
    payload = client.get("/health").json()
    assert payload["realtime"] == {"ws_clients": 2}


ALL_DATABASES = ("linux_logs", "windows_logs", "log_fusion", "log_hash")


def test_db_status_partial_renders_connection_rows(client, monkeypatch):
    """侧栏「服务连接」卡片片段：每个库一行 + 一行汇总，状态只来自本次真实探测。

    片段会直接进页面，所以这里锁住三件事：四个库都出现、汇总数量正确、
    「部分可用」时仍然逐行显示真实状态（不能因为有一个库挂了就整块变色）。
    """
    monkeypatch.setattr(main, "db_health", lambda: {
        "linux_logs": True, "windows_logs": False, "log_fusion": True, "log_hash": True})
    response = client.get("/partials/db-status")
    assert response.status_code == 200
    html = response.text
    assert "3/4 个库" in html and "部分可用" in html
    for key in ALL_DATABASES:
        assert key in html, f"片段里缺少库 {key}（侧栏放不下真实库名，用 title 悬停显示）"
    assert "Windows 日志库" in html and "存证库" in html
    assert "title=" in html, "真实库名必须以悬停提示的形式保留"
    assert html.count(">已连接<") == 3
    assert html.count(">不可用<") == 1          # 只有 windows_logs 那一行
    assert "conn-dot bad" in html and "conn-dot ok" in html


def test_db_status_partial_reports_total_outage_without_5xx(client, monkeypatch):
    """全部库不可用时仍然是 HTTP 200 + 明确的「全部不可用」，不把故障变成 5xx 或旧状态。"""
    monkeypatch.setattr(main, "db_health", lambda: {name: False for name in ALL_DATABASES})
    response = client.get("/partials/db-status")
    assert response.status_code == 200
    assert "0/4 个库" in response.text and "全部不可用" in response.text
    assert response.text.count(">不可用<") == 4
    assert ">已连接<" not in response.text


def test_sse_endpoint_is_removed(client):
    """SSE 通道已于 2026-10-13 移除：实时同步统一走 WebSocket（ADR-015）。

    路径不存在时会落到 `POST /api/v1/logs/{platform}` 上，因此 GET 得到 405 而不是 404，
    两种都说明「这里没有 SSE 接口」。
    """
    assert client.get("/api/v1/logs/stream").status_code in (404, 405)
    assert "sse_clients" not in client.get("/health").json()["realtime"]


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
    """前端不使用 SSE（该通道已整体移除，见 ADR-015）。

    历史：每页 1 条 SSE + 1 条 WebSocket 会占满浏览器同一地址的 6 条 HTTP/1.1 连接额度，
    导致所有 REST 请求（含自动刷新）永久排队；现在只保留 WebSocket + 定时 REST 刷新。
    """
    js = client.get("/static/app.js").text
    assert "EventSource" not in js
    assert "/api/v1/logs/stream" not in js
    assert "summaryTimer" in js and "pollTimer" in js


def test_app_js_is_requested_with_cache_busting_token(client):
    """app.js 带版本参数：旧浏览器缓存过无 no-store 头的副本，需要一次强制定位。"""
    assert "/static/app.js?v=" in client.get("/").text


# --- 存证查询失败必须可见，不能伪装成「0 条」---------------------------------

@pytest.mark.parametrize("url", [
    "/api/v1/evidence/summary",
    "/api/v1/evidence?page=1&size=30",
])
def test_evidence_query_failure_returns_503_not_zero(client, url):
    """存证库查询失败时返回 503。

    曾经把异常吞掉并返回 `total: 0`，结果「库连不上」和「确实没有存证」
    在页面上完全一样，操作者会以为数据被清空了。conftest 已禁用 Engine.connect，
    因此这里天然模拟了数据库不可用。
    """
    response = client.get(url)
    assert response.status_code == 503
    assert "存证库查询失败" in response.json()["detail"]


def test_evidence_export_failure_returns_503(client):
    """导出同样不能把故障伪装成空 CSV（否则会得到一个看似正常的空文件）。"""
    response = client.get("/api/v1/evidence/export")
    assert response.status_code == 503


# --- 存证幂等判定：只有唯一键冲突才算「已存证过」------------------------------

class _FakeDbError(Exception):
    """模拟驱动层异常：args[0] 是 MySQL errno。"""

    def __init__(self, errno, message="boom"):
        super().__init__(errno, message)


class _FakeConnection:
    def execute(self, *args, **kwargs):
        return None


class _FakeTransaction:
    def __enter__(self):
        return _FakeConnection()

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    def begin(self):
        return _FakeTransaction()


def test_is_duplicate_key_only_accepts_errno_1062():
    from sqlalchemy.exc import IntegrityError

    assert main._is_duplicate_key(IntegrityError("s", {}, _FakeDbError(1062))) is True
    # 1048 = NOT NULL 违反：绝不能当成「已经存证过」
    assert main._is_duplicate_key(IntegrityError("s", {}, _FakeDbError(1048))) is False
    assert main._is_duplicate_key(IntegrityError("s", {}, _FakeDbError(1406))) is False


def test_is_duplicate_key_falls_back_to_message():
    from sqlalchemy.exc import IntegrityError

    assert main._is_duplicate_key(IntegrityError("s", {}, _FakeDbError(None, "Duplicate entry 'x' for key"))) is True
    assert main._is_duplicate_key(IntegrityError("s", {}, _FakeDbError(None, "Column cannot be null"))) is False


@pytest.mark.parametrize("errno,expect_stored,expect_failed", [
    (1062, 1, 0),   # 唯一键冲突 → 重复上报，幂等成功
    (1048, 0, 1),   # NOT NULL 违反 → 真实失败，必须上报
])
def test_persist_evidence_distinguishes_duplicate_from_real_failure(
    monkeypatch, errno, expect_stored, expect_failed
):
    """只有重复上报才算成功；其他完整性错误必须计入 failed。

    若不加区分地把所有 IntegrityError 当成幂等成功，存证会静默丢失而接口
    仍报 hashed 正常——故障被伪装成成功。
    """
    from sqlalchemy.exc import IntegrityError

    def boom(conn, table, row):
        raise IntegrityError("INSERT ...", {}, _FakeDbError(errno))

    monkeypatch.setattr(main, "hash_engine", _FakeEngine())
    monkeypatch.setattr(main, "_insert_row", boom)

    record = {"log": "type=PATH msg=audit(1.1:1): x", "timestamp": "2026-10-12T00:00:00Z"}
    stored, errors = main._persist_evidence("linux", [record], {0: None})

    assert stored == expect_stored
    assert len(errors) == expect_failed
    if expect_failed:
        assert errors[0]["stage"] == "hash"
        assert "完整性约束失败" in errors[0]["reason"]


# --- 存证校验规则必须与写入规则一致 -------------------------------------------

def test_verify_sql_uses_same_separator_as_writer():
    """校验用的分隔符必须等于写入端的 `HASH_FIELD_SEPARATOR`。

    分隔符是哈希定义的一部分：任一端改了，所有存证都会被判成篡改（或反之，
    真正的篡改被判成正常）。这里用 `CHAR(31)` 表达 US 字符。
    """
    from app import hashing

    assert ord(hashing.HASH_FIELD_SEPARATOR) == 31
    assert "CHAR(31)" in main.EVIDENCE_VERIFY_SQL


def test_verify_sql_treats_null_timestamp_as_empty_string():
    """NULL 时间戳必须按空串复算，否则会对合法存证误报。

    写入端 `raw_text(None) == ""`，所以 SQL 侧要用 `IFNULL(log_timestamp,'')`。
    曾经写成「NULL 时间戳 => PENDING 待校验」，结果把一条由本系统正常写入的
    「无时间戳」存证报成了异常——校验规则与写入规则不一致会制造假警报。
    """
    assert "IFNULL(log_timestamp" in main.EVIDENCE_VERIFY_SQL
    assert "log_timestamp IS NULL THEN" not in main.EVIDENCE_VERIFY_SQL


def test_verify_sql_handles_null_hash_safely():
    """摘要为 NULL 时要判为不一致，不能用 `=`（NULL 比较结果是 NULL，会被漏掉）。"""
    assert "<=>" in main.EVIDENCE_VERIFY_SQL


def test_evidence_statuses_are_all_reachable():
    """状态集合里不应保留不可达的分类。

    `PENDING` 已随「NULL 时间戳按空串处理」一并移除：若保留，它会永远是 0，
    或者更糟——把合法行算进去，成为假警报来源。
    """
    assert main.EVIDENCE_STATUSES == ("VALID", "HASH_MISMATCH", "RAW_MISSING")
    assert "PENDING" not in main.EVIDENCE_VERIFY_SQL


@pytest.mark.parametrize("value", ["HASH_MISMATCH", "RAW_MISSING", "VALID"])
def test_valid_verify_filter_reaches_database(client, value):
    """合法筛选值应通过参数校验（本环境无库，因此停在 503 而不是 400）。"""
    assert client.get(f"/api/v1/evidence?verify={value}").status_code == 503


def test_invalid_verify_filter_returns_400(client):
    """非法筛选值要明确拒绝，而不是拼进 SQL 或静默忽略。"""
    response = client.get("/api/v1/evidence?verify=NOT_A_STATUS")
    assert response.status_code == 400
    assert "verify" in response.json()["detail"]


def test_evidence_summary_exposes_data_source(client, monkeypatch):
    """响应要带实际读取的库表，避免「页面看的不是同一个库」这类误判。"""
    from app import config

    monkeypatch.setattr(main, "rows", lambda *a, **k: [
        {"verify_status": "VALID", "n": 7},
        {"verify_status": "HASH_MISMATCH", "n": 2},
    ])
    payload = client.get("/api/v1/evidence/summary").json()["data"]

    settings = config.get_settings()
    assert payload["source"] == {
        "host": settings.db_host,
        "database": settings.log_hash_db,
        "table": settings.log_hash_table,
    }
    assert payload["total"] == 9
    assert payload["statuses"] == {"VALID": 7, "HASH_MISMATCH": 2, "RAW_MISSING": 0}
    assert payload["healthy"] is False


def test_evidence_summary_reports_healthy_when_no_anomalies(client, monkeypatch):
    monkeypatch.setattr(main, "rows", lambda *a, **k: [{"verify_status": "VALID", "n": 5}])
    payload = client.get("/api/v1/evidence/summary").json()["data"]

    assert payload["total"] == 5
    assert payload["statuses"]["HASH_MISMATCH"] == 0
    assert payload["healthy"] is True


# --- 存证筛选：平台与入库时间范围（P0 页面改造新增）----------------------------

def test_evidence_where_platform_filter():
    where, params = main._evidence_where("linux", None)
    assert "platform = :platform" in where
    assert params["platform"] == "linux"


def test_evidence_where_date_range_uses_created_at():
    """时间范围按入库时间过滤：补传的历史日志 event_time 很早，不能用 event_time 口径。"""
    where, params = main._evidence_where(None, None, "2026-10-01", "2026-10-13")
    assert "created_at >= :start_at" in where
    assert "created_at <= :end_at" in where
    # 结束日期必须覆盖当天 23:59:59，否则「结束日期=今天」会漏掉今天已入库的记录
    assert params["end_at"].strftime("%H:%M:%S") == "23:59:59"
    assert params["start_at"].strftime("%H:%M:%S") == "00:00:00"


def test_evidence_where_combines_filters():
    where, params = main._evidence_where("windows", "abc", "2026-10-01", None)
    assert where.count(" AND ") == 2
    assert params["platform"] == "windows"
    assert params["keyword"] == "%abc%"
    assert "end_at" not in params


def test_evidence_where_without_filters_is_empty():
    where, params = main._evidence_where(None, None)
    assert where == ""
    assert params == {}


def test_evidence_list_accepts_date_and_platform_params(client):
    """新筛选参数必须通过 FastAPI 参数校验（无库环境应停在 503 而不是 422/400）。"""
    response = client.get(
        "/api/v1/evidence?platform=linux&start=2026-10-01&end=2026-10-13"
    )
    assert response.status_code == 503


def test_evidence_export_accepts_date_and_platform_params(client):
    response = client.get(
        "/api/v1/evidence/export?platform=windows&start=2026-10-01&end=2026-10-13"
    )
    assert response.status_code == 503


def test_evidence_bad_date_returns_400(client):
    """日期格式错误要明确拒绝，不能静默忽略导致筛选条件失效。"""
    response = client.get("/api/v1/evidence?start=2026/10/01")
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.json()["detail"]


# --- 服务端更新接口（POST /api/v1/admin/update） ------------------------------
#
# 该接口不连库；这里只验证触发链路：confirm 校验、systemd-run 调用、并发拒绝与降级报错。
# 真实执行会停服并覆盖 /opt/log-fusion，绝不能在本机测试里真的跑，故全部 patch 掉。


class _FakeCompleted:
    """替代 subprocess.run 的返回值，只需要 returncode。"""

    def __init__(self, returncode):
        self.returncode = returncode


def test_admin_update_requires_confirm(client):
    response = client.post("/api/v1/admin/update", json={})
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]


def test_admin_update_without_systemd_run_returns_500(monkeypatch, client):
    monkeypatch.setattr("shutil.which", lambda name: None)
    response = client.post("/api/v1/admin/update", json={"confirm": True})
    assert response.status_code == 500
    assert "systemd-run" in response.json()["detail"]


def test_admin_update_missing_script_returns_500(monkeypatch, client, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(main, "_UPDATE_SCRIPT_SRC", tmp_path / "absent.sh")
    response = client.post("/api/v1/admin/update", json={"confirm": True})
    assert response.status_code == 500
    assert "更新脚本不存在" in response.json()["detail"]


def test_admin_update_rejects_concurrent_run(monkeypatch, client):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    # is-active 返回 0 表示已有同名单元在运行
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _FakeCompleted(0))
    response = client.post("/api/v1/admin/update", json={"confirm": True})
    assert response.status_code == 409


def test_admin_update_launches_detached_systemd_unit(monkeypatch, client, tmp_path):
    launched: dict = {}
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )
    monkeypatch.setattr(main, "_UPDATE_SCRIPT_DEST", tmp_path / "log-fusion-update.sh")
    # is-active 返回非 0 = 当前没有更新在跑；Popen 记录实际启动的命令
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _FakeCompleted(1))
    monkeypatch.setattr(
        "subprocess.Popen", lambda args, **k: launched.setdefault("args", args)
    )

    response = client.post("/api/v1/admin/update", json={"confirm": True})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["unit"] == "log-fusion-update"

    args = launched["args"]
    assert args[0] == "/usr/bin/systemd-run"
    assert "--unit=log-fusion-update" in args
    # 脚本先被复制到仓库之外再执行——更新过程会 git reset 仓库里的文件，
    # 不能让它覆盖自己正在读取的脚本。
    assert str(tmp_path / "log-fusion-update.sh") in args
    assert (tmp_path / "log-fusion-update.sh").is_file()


def test_update_script_is_shipped_with_repo():
    """更新脚本必须随仓库提供，否则接口在服务器上无从执行。"""
    script = main._UPDATE_SCRIPT_SRC
    assert script.is_file(), f"缺少更新脚本：{script}"
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_update_script_updates_in_place_without_clean():
    """更新必须原地 git 更新：只 reset --hard，绝不 git clean、绝不删代码目录。

    `.env` / `data` / `.venv` 全靠 .gitignore 保护；`git clean` 或
    `rm -rf /opt/log-fusion` 会把它们一起删掉，所以这里逐条锁死。
    注释里的说明不算（只检查可执行的代码行）。
    """
    text = main._UPDATE_SCRIPT_SRC.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'reset --hard "origin/$BRANCH"' in code
    assert "git clean" not in code
    assert "rm -rf" not in code


def test_gitignore_protects_runtime_files_from_update():
    """原地更新的前提：运行期文件必须被 .gitignore 忽略，否则 reset 会覆盖它们。"""
    gitignore = main._UPDATE_SCRIPT_SRC.parents[1] / ".gitignore"
    entries = {
        line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()
    }
    for pattern in (".venv/", ".env", "data/", "backend/data/"):
        assert pattern in entries, f".gitignore 缺少 {pattern}，原地更新会覆盖它"


def test_service_unit_installer_and_updater_agree_on_unit_name():
    """单元文件、安装脚本、更新脚本三者的服务名必须一致。

    更新脚本靠 `systemctl stop $SERVICE_NAME` 停服；名字和实际安装的单元对不上时
    `systemctl stop` 只是「停不到任何东西」，属于静默失败，所以在这里锁死。
    """
    deploy_dir = main._UPDATE_SCRIPT_SRC.parent
    unit = deploy_dir / "log-fusion.service"
    installer = deploy_dir / "install-service.sh"
    assert unit.is_file(), f"缺少 systemd 单元：{unit}"
    assert installer.is_file(), f"缺少服务安装脚本：{installer}"
    assert installer.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert 'UNIT_NAME="log-fusion"' in installer.read_text(encoding="utf-8")
    assert 'SERVICE_NAME="log-fusion"' in main._UPDATE_SCRIPT_SRC.read_text(encoding="utf-8")
    assert main._UPDATE_UNIT == "log-fusion-update"
