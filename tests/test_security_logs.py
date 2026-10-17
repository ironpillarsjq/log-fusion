"""Security import invariants; no DB or network access (including error paths)."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import pytest

from app import main, security_logs as security


def record(log_id="abc", **values):
    return {"index": "ipslog", "source": {"log_id": log_id, "timestamp": 1792065138,
            "recv_time": 1792065138123, "sport": "0", "dport": "443", "raw_data": "原文", **values}}


def test_normalize_preserves_zero_unknown_fields_and_utc():
    raw = record(future_field={"新": 1}, sip="", count="0")
    row = security.normalize_record("ipslog", raw, "site-a")
    assert row["sport"] == row["count"] == 0
    assert row["sip"] is None
    assert row["event_time"] == datetime.fromtimestamp(1792065138, timezone.utc).replace(tzinfo=None)
    assert row["received_time"].microsecond == 123000
    assert json.loads(row["raw_record"]) == raw
    assert "future_field" not in row


@pytest.mark.parametrize("field,value", [("sport", 65536), ("dport", -1), ("count", "1.5"),
                                         ("timestamp", True), ("log_id", ""), ("log_id", "x"*129)])
def test_bad_source_is_never_silently_truncated(field, value):
    with pytest.raises((ValueError, TypeError)):
        security.normalize_record("ipslog", record(**{field: value}), "a")


def test_identity_dedup_and_conflicting_content():
    first = record()
    reordered = {"source": dict(reversed(list(first["source"].items()))), "index": "ipslog"}
    assert len(security.unique_records("ipslog", [first, reordered], "a")) == 1
    with pytest.raises(ValueError, match="conflicting"):
        security.unique_records("ipslog", [first, record(raw_data="changed")], "a")


def test_calendar_month_end_and_safe_window_split():
    end = int(datetime(2026, 5, 31, 4, tzinfo=timezone.utc).timestamp())
    assert datetime.fromtimestamp(security.months_before(end, 3), timezone.utc).day == 28
    assert security.split_interval(10, 14) == [[10, 12], [12, 14]]
    with pytest.raises(ValueError):
        security.split_interval(10, 11)


class FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass

    def in_transaction(self):
        return False

    @contextmanager
    def begin(self):
        yield


class FakeStore:
    instance = "test"

    def __init__(self):
        self.state = security.new_state(100, 200)
        self.state["next_poll"] = 10**12
        self.saved = []
        self.inserted = []

    @contextmanager
    def locked(self):
        yield FakeConnection()

    def states(self, connection):
        return {"ipslog": deepcopy(self.state)}

    def save(self, connection, kind, state):
        self.state = deepcopy(state)
        self.saved.append(deepcopy(state))

    def insert(self, connection, kind, records):
        self.inserted.extend(records)
        return len(security.unique_records(kind, records, self.instance)), len(records)-len(security.unique_records(kind, records, self.instance))


def worker(monkeypatch, responses, **options):
    requests = []
    class FakeUpstream:
        def __init__(self, *args):
            self.calls = []

        def search(self, kind, start, end, size):
            requests.append((kind, start, end, size))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            pass

    monkeypatch.setattr(security, "Upstream", FakeUpstream)
    store = FakeStore()
    store.requests = requests
    return store, security.SecurityWorker(store, "unused", page_size=10, **options)


def test_large_window_splits_without_advancing_or_inserting(monkeypatch):
    store, sync = worker(monkeypatch, [{"totalSize": 50, "data": [record()]}])
    sync.step()
    assert store.state["history"]["pending"] == [[100, 150], [150, 200]]
    assert store.state["history"]["checked_until"] == 99
    assert store.state["history"]["expected"] == 50
    assert not store.inserted


def test_identical_upstream_duplicates_are_counted_but_stored_once(monkeypatch):
    store, sync = worker(monkeypatch, [{"totalSize": 2, "data": [record()]},
                                     {"totalSize": 2, "data": [record(), record()]}])
    sync.step()
    assert store.state["rows"] == store.state["duplicates"] == 1
    assert store.state["history"]["status"] == "complete"


def test_short_page_is_split_and_not_marked_complete(monkeypatch):
    store, sync = worker(monkeypatch, [{"totalSize": 2, "data": [record()]},
                                     {"totalSize": 2, "data": [record()]}])
    sync.step()
    assert not store.inserted
    assert store.state["history"]["checked_until"] == 99
    assert len(store.state["history"]["pending"]) == 2


def test_insert_failure_keeps_committed_checkpoint(monkeypatch):
    store, sync = worker(monkeypatch, [{"totalSize": 1, "data": [record()]},
                                     {"totalSize": 1, "data": [record()]}])
    def fail(*args):
        raise ValueError("stored log identity has conflicting upstream content; original preserved")
    store.insert = fail
    sync.step()
    assert store.state["history"]["pending"] == [[100, 200]]
    assert store.state["history"]["checked_until"] == 99
    assert store.state["rows"] == 0
    assert store.state["retry_at"] > 0
    assert store.state["history"]["status"] == "retrying"


def test_empty_success_and_network_error_are_distinct(monkeypatch):
    store, sync = worker(monkeypatch, [OSError("network")])
    sync.step()
    assert store.state["history"]["status"] == "retrying"
    store.state["retry_at"] = 0
    store, sync = worker(monkeypatch, [{"totalSize": 0, "data": []}])
    sync.step()
    assert store.state["history"]["status"] == "complete"
    assert store.state["history"]["checked_until"] == 200


def test_history_only_never_schedules_live_poll(monkeypatch):
    store, sync = worker(monkeypatch, [{"totalSize": 0, "data": []}])
    store.state["next_poll"] = 0
    sync.history_only = True
    sync.step()
    assert store.state["history"]["status"] == "complete"
    assert store.state["live"]["pending"] == []
    assert store.state["next_poll"] == 0


def test_manual_resume_extends_end_without_dropping_pending_history(monkeypatch):
    monkeypatch.setattr(security, "ORDER", ("ipslog",))
    store = FakeStore()
    end = 1792065138
    security.SecurityStore.initialize(store, months=3, end=end, extend=True)
    assert store.state["history"]["pending"] == [[100, 200], [200, end]]
    assert store.state["history"]["start"] <= security.months_before(end, 3)
    assert store.state["history"]["end"] == end


@pytest.mark.parametrize("history_status,expected", [("running", "stopped"), ("complete", "complete")])
def test_live_startup_discards_stale_live_queue_and_preserves_rows_and_history(monkeypatch, history_status, expected):
    monkeypatch.setattr(security, "ORDER", ("ipslog",))
    store = FakeStore()
    store.state.update(rows=730914, error="old failure", retry_at=999999)
    store.state["history"]["status"] = history_status
    old_pending = deepcopy(store.state["history"]["pending"])
    store.state["live"].update(pending=[[1, 200]], checked_until=100)
    security.SecurityStore.initialize_live(store, overlap=120, lag=30, now=10000)
    assert store.state["rows"] == 730914
    assert store.state["history"]["pending"] == old_pending
    assert store.state["history"]["status"] == expected
    assert store.state["live"]["pending"] == []
    assert store.state["live"]["since"] == store.state["live"]["checked_until"] == 9850
    assert store.state["collection_mode"] == "live_only"
    assert store.state["error"] == "" and store.state["retry_at"] == 0


def test_first_live_startup_creates_no_history_job(monkeypatch):
    monkeypatch.setattr(security, "ORDER", ("ipslog",))
    class CountResult:
        def scalar_one(self):
            return 5
    monkeypatch.setattr(FakeConnection, "execute", lambda *args: CountResult(), raising=False)
    store = FakeStore()
    store.states = lambda conn: {}
    security.SecurityStore.initialize_live(store, overlap=120, lag=30, now=10000)
    assert store.state["rows"] == 5
    assert store.state["history"]["pending"] == []
    assert store.state["history"]["status"] == "disabled"


def test_live_only_never_falls_back_to_unfinished_history(monkeypatch):
    store, sync = worker(monkeypatch, [], live_only=True)
    store.state["last_mode"] = "live"
    before = deepcopy(store.state)
    assert sync.step() is None
    assert store.state == before
    assert not store.requests


def test_long_outage_only_requests_the_recent_window(monkeypatch):
    monkeypatch.setattr(security.time, "time", lambda: 10000)
    store, sync = worker(monkeypatch, [{"totalSize": 0, "data": []}],
                         live_only=True, overlap=120, live_window=600)
    store.state["next_poll"] = 0
    store.state["live"].update(pending=[[1, 500]], since=1, checked_until=200)
    pending = deepcopy(store.state["history"]["pending"])
    event = sync.step()
    assert store.requests == [("ipslog", 9370, 9970, 1)]
    assert event["mode"] == "live"
    assert store.state["history"]["pending"] == pending
    assert store.state["live"]["checked_until"] == 9970


def test_stale_split_window_is_clipped_before_request(monkeypatch):
    monkeypatch.setattr(security.time, "time", lambda: 10000)
    store, sync = worker(monkeypatch, [{"totalSize": 0, "data": []}],
                         live_only=True, overlap=120, live_window=600)
    store.state["live"].update(pending=[[9000, 9800], [9800, 9950]], since=1)
    sync.step()
    assert store.requests == [("ipslog", 9370, 9800, 1)]
    assert store.state["live"]["pending"] == [[9800, 9950]]


def test_live_only_first_query_stays_within_startup_lookback(monkeypatch):
    monkeypatch.setattr(security, "ORDER", ("ipslog",))
    monkeypatch.setattr(security.time, "time", lambda: 10000)
    store, sync = worker(monkeypatch, [{"totalSize": 0, "data": []}],
                         live_only=True, overlap=120, live_window=600)
    security.SecurityStore.initialize_live(store, overlap=120, lag=30, now=10000)
    sync.step()
    assert store.requests == [("ipslog", 9850, 9970, 1)]


def test_live_and_history_only_cannot_be_combined():
    with pytest.raises(ValueError, match="mutually exclusive"):
        security.SecurityWorker(FakeStore(), "unused", live_only=True, history_only=True)


def test_backend_loop_initializes_and_runs_only_live_mode(monkeypatch):
    import asyncio
    calls = []
    def fail_history(*args):
        raise AssertionError("startup must not initialize history")
    def initialize_live(**kwargs):
        calls.append(("initialize_live", kwargs))
        return True
    class LiveWorker:
        def __init__(self, *args, **kwargs):
            assert kwargs["live_only"] is True
        def step(self):
            calls.append(("step", None))
    async def stop_after_step(seconds):
        raise asyncio.CancelledError
    monkeypatch.setattr(main.security_store, "initialize", fail_history)
    monkeypatch.setattr(main.security_store, "initialize_live", initialize_live)
    monkeypatch.setattr(main, "SecurityWorker", LiveWorker)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_step)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._security_sync_loop())
    assert [name for name, _ in calls] == ["initialize_live", "step"]


def test_security_api_whitelist_and_error_status(monkeypatch):
    client = TestClient(main.app)
    assert client.get("/api/v1/security/logs/archive_log").status_code == 422
    assert client.get("/api/v1/security/logs/ipslog?limit=101").status_code == 422
    monkeypatch.setattr(main.security_store, "detail", lambda *args: None)
    assert client.get("/api/v1/security/logs/ipslog/1").status_code == 404
    def fail():
        raise RuntimeError("DB unavailable")
    monkeypatch.setattr(main.security_store, "summary", fail)
    assert client.get("/api/v1/security/summary").status_code == 503


def test_security_api_cursor_and_raw_detail(monkeypatch):
    client = TestClient(main.app)
    seen = []
    monkeypatch.setattr(main.security_store, "logs", lambda *args: seen.append(args) or {"items": [], "nextBefore": None})
    assert client.get("/api/v1/security/logs/ipslog?before=9&limit=2").status_code == 200
    assert seen == [("ipslog", 9, 2)]
    monkeypatch.setattr(main.security_store, "detail", lambda *args: record())
    assert client.get("/api/v1/security/logs/ipslog/1").json()["data"] == record()


@pytest.mark.parametrize("kind", security.ORDER)
def test_security_list_projection_uses_existing_columns_and_keeps_cursor(kind):
    """扩展列表不能查其他分类独有的列，也不能加载完整原文；分页仍按入库 ID。"""
    from types import SimpleNamespace

    seen = []
    rows = [{"id": i, "event_time": datetime(2026, 10, 17), "severity": 0, "count": 0}
            for i in (9, 8, 7)]

    class Connection:
        def execute(self, sql, params):
            seen.append((str(sql), params))
            return SimpleNamespace(mappings=lambda: rows)

    @contextmanager
    def connect():
        yield Connection()

    store = security.SecurityStore(SimpleNamespace(connect=connect), "test-site")
    result = store.logs(kind, before=10, limit=2)
    sql, params = seen[0]
    selected = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0].replace("`", "").split(",")
    available = {field["field"] for field in security.CATALOG[kind]["fields"]} | {"id", "event_time"}
    assert set(selected) <= available
    assert {"event", "protocol", "severity", "action", "acted", "count", "dev_ip"} <= set(selected)
    if kind == "ipblacklistlog":
        assert {"domain", "ip", "engine"} <= set(selected)
    if kind == "ips_web_secure_event_log":
        assert "url" in selected
    assert "raw_record" not in selected and "rawinfo" not in selected
    assert "id<:before" in sql and "ORDER BY id DESC LIMIT :limit" in sql
    assert params == {"instance": "test-site", "before": 10, "limit": 3}
    assert [row["id"] for row in result["items"]] == [9, 8]
    assert result["nextBefore"] == 8
    assert result["items"][0]["event_time"] == "2026-10-17T00:00:00.000Z"
    assert result["items"][0]["severity"] == 0
