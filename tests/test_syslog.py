"""Product fixtures, real loopback UDP/TCP and durable replay, with MySQL hard-disabled by conftest."""
import asyncio
import base64
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import threading

import pytest

from app.device_log_catalog import BUSINESS, TABLES, WAF_TYPES, mysql_ddl
from app.syslog_parsers import ParseError, decoded_waf_fields, parse_message
from app.syslog_protocol import ProtocolError, TCPFramer, device_timezone, envelope
from app.syslog_server import Receiver, drain_once, main
from app.syslog_store import Inbox, MySQLDeviceStore, SpoolFull


def parse(raw, product="waf", **kwargs):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return parse_message(raw, product=product, source_instance="test-device", peer_ip="127.0.0.1",
                         transport="UDP", **kwargs)


@pytest.mark.parametrize("tag,table", list(WAF_TYPES.items()))
def test_all_fifteen_waf_tags_route_and_preserve_original(tag, table):
    raw = f"<134>Oct 28 12:00:00 waf-a waf: tag:{tag} stat_time:2026-10-28 12:00:00  "
    # webaccess uses the key:value branch when stat_time is the first field.
    item = parse(raw)
    assert item.table == table
    assert item.row["raw_log"] == raw
    assert item.row["raw_bytes"] == raw.encode()
    assert item.row["raw_sha256"] == hashlib.sha256(raw.encode()).digest()
    assert item.row["event_time"] == datetime(2026, 10, 28, 4)
    assert item.row["device_hostname"] == "waf-a"
    assert item.row["parse_status"] == "ok"
    assert set(item.row) <= set(TABLES[table])


def test_waf_spaces_colons_ipv6_base64_and_zero_values():
    encoded = base64.b64encode("攻击详情\nHTTP: test".encode()).decode()
    raw = ("waf-a waf: tag:waf_log_websec stat_time:2026-10-28 12:00:00  "
           "src_ip:2001:db8::1  dst_ip:192.0.2.1  dst_port:0  event_type:WEB Access Logs  "
           f"rule_id:0  action:ENTER DEFEND MODE  uri:/a?next=http://test/a  alertinfo:{encoded}  "
           "charaters:SGVsbG8=  api_event_typ:0  count_num:0")
    row = parse(raw).row
    assert row["src_ip"] == "2001:db8::1" and row["dst_port"] == 0
    assert row["event_type"] == "WEB Access Logs" and row["action"] == "ENTER DEFEND MODE"
    assert row["uri"] == "/a?next=http://test/a" and row["count_num"] == 0
    assert row["rule_id"] == "0" and row["api_event_type"] == "0"
    assert row["alertinfo"] == encoded and row["characters"] == "SGVsbG8="
    payload = json.loads(row["payload"])
    assert decoded_waf_fields(payload)["alertinfo"] == "攻击详情\nHTTP: test"
    assert decoded_waf_fields(payload)["charaters"] == "Hello"


def test_waf_login_native_fields_and_system_status():
    row = parse('localhost waf: tag:waf_log_login stat_time:2026-10-28 12:00:00  user:admin  '
                'password:  session_id:abc  desc:  op_type: Logout  result:success  role:{"SYS ADMINISTRATORS"}').row
    assert row["user"] == "admin" and row["op_type"] == "Logout"
    assert row["role"] == '{"SYS ADMINISTRATORS"}'
    row = parse("tag:waf_log_wafstat stat_time:2026-10-28 12:00:00 cpu:0 mem:42.5").row
    assert row["cpu"] == Decimal("0") and row["mem"] == Decimal("42.5")


def test_ddos_multiple_sources_do_not_invent_single_source():
    row = parse("tag:waf_log_ddos stat_time:2026-10-28 12:00:00 src_ip:192.0.2.1,2001:db8::2 action:ENTER DEFEND MODE").row
    assert row["src_ip"] is None
    assert json.loads(row["src_ips"]) == ["192.0.2.1", "2001:db8::2"]


def test_waf_eclf_access_format():
    row = parse('waf-g2 waf: tag:waf_log_webaccess 192.0.2.1 - - [24/Oct/2026:15:15:41 +0800] '
                '"GET /py/ HTTP/1.1" 200 123 "-" "Mozilla/5.0 test"').row
    assert row["event_time"] == datetime(2026, 10, 24, 7, 15, 41)
    assert row["src_ip"] == "192.0.2.1" and row["agent"] == "Mozilla/5.0 test"
    assert row["url"] == "/py/" and row["res_content_len"] == 123


@pytest.mark.parametrize("kind,table", [("audit", "das_statement_log"), ("session", "das_session_log"), ("warn", "das_sensitive_log")])
def test_three_das_types_keep_large_ids_and_unknown_fields(kind, table):
    raw = json.dumps(dict(log_type=kind, capture_time="2026-10-28 12:00:00", device_id=999,
                          data_id="1682418786999001438", ssid="168241878600602000113999",
                          swarn_id="168241878600602000113999", risk_level=0, client_port="0",
                          unknown_extension={"x": "中文"}), ensure_ascii=False, indent=2)
    parsed = parse(raw, "das")
    assert parsed.table == table
    row = parsed.row
    assert row["device_id"] == "999" and row["risk_level"] == "0" and row["client_port"] == 0
    assert row["event_time"] == datetime(2026, 10, 28, 4)
    assert row["raw_log"] == raw and "unknown_extension" in json.loads(row["payload"])
    assert row["swarn_id"] == "168241878600602000113999" if kind == "warn" else row["ssid"] == "168241878600602000113999"


def test_das_sql_with_controls_quotes_pipes_and_duration():
    sql = 'select "中|文", "a:b"\nfrom t where x="|规则类型:内容"'
    row = parse(json.dumps(dict(log_type="audit", capture_time="2026-10-28T12:00:00+08:00",
                               sql_statement=sql, duration="1200us", ret_record_num="0",
                               resultset="a\x1db\x1ec", sensise_tag_array="[1,2,3]")), "das").row
    assert row["sql_statement"] == sql and row["duration_us"] == 1200
    assert row["duration"] == "1200us" and row["resultset"] == "a\x1db\x1ec"
    assert row["ret_record_num"] == 0 and json.loads(row["sensise_tag_array"]) == [1, 2, 3]


def test_sensitive_table_alias_and_session_missing_fields_are_partial():
    row = parse('{"log_type":"warn","capture_time":"2026-10-28 12:00:00","table_name":"t","sec_level":"3"}', "das").row
    assert row["table_names"] == "t"
    row = parse('{"log_type":"session","capture_time":"2026-10-28 12:00:00","swarn_id":"123"}', "das").row
    assert row["parse_status"] == "partial" and "session fields missing" in row["parse_error"]
    assert "ss_action" in row and row["ss_action"] is None


def test_legacy_das_alarm_keeps_chinese_and_sql_pipe():
    raw = ("<-1>Mar 22 09:42:18 localhost.localdomain est001: [CEF]:|数据库名称:tns62|规则类型:风险操作"
           "|风险级别:高风险|命中规则:测试规则|捕获时间:2019-03-22 09:37:15|服务器IP:192.0.2.1"
           "|客户端IP:192.0.2.2|SQL语句:select a|b from t\r")
    row = parse(raw, "das").row
    assert row["database_name"] == "tns62" and row["rule_name"] == "测试规则"
    assert row["rule_id"] is None and row["risk_level"] == "高风险"
    assert row["sql_statement"] == "select a|b from t\r" and row["raw_log"] == raw
    assert row["event_time"] == datetime(2019, 3, 22, 1, 37, 15)
    assert json.loads(row["syslog_meta"])["invalid_priority"] == "<-1>"


@pytest.mark.parametrize("body,product", [("tag:waf_log_new x:1", "waf"), (b"\xffbad", "das"),
    ('{"log_type":0}', "das"), ('{"log_type":"audit","log_type":"warn"}', "das"),
    ('{"log_type":"audit","x":NaN}', "das"), ('[]', "das"),
    ("tag:waf_log_op user:a user:b", "waf")])
def test_unclassifiable_messages_are_rejected_without_fabrication(body, product):
    with pytest.raises((ParseError, ProtocolError)):
        parse(body, product)


def test_invalid_columns_preserve_raw_payload_without_truncating():
    payload = dict(log_type="audit", capture_time="invalid", device_id="x" * 129,
                   client_port="67766", duration="NaN", risk_level="0", sql_statement="x" * 80000)
    row = parse(json.dumps(payload), "das").row
    assert row["device_id"] is None and row["client_port"] is None and row["event_time"] is None
    assert row["risk_level"] == "0" and row["sql_statement"] == payload["sql_statement"]
    assert json.loads(row["payload"]) == payload and row["parse_status"] == "partial"


def test_rfc5424_structured_data_bom_and_body_base64():
    body = 'tag:waf_log_wafstat stat_time:2026-10-28 12:00:00 cpu:10 mem:20'
    header = '<134>1 2026-10-28T04:00:00Z waf-a waf - ID [x a="escaped\\] bracket"][y b="ok"] '
    row = parse(header + "\ufeff" + body).row
    assert row["parse_status"] == "ok" and row["device_hostname"] == "waf-a"
    assert json.loads(row["syslog_meta"])["structured_data"] == '[x a="escaped\\] bracket"][y b="ok"]'
    encoded = base64.b64encode(body.encode()).decode()
    row = parse('<134>1 2026-10-28T04:00:00Z waf-a waf - - - ' + encoded, body_base64=True).row
    assert row["cpu"] == Decimal(10) and row["raw_log"].endswith(encoded)


def test_header_time_fallback_year_rollover_and_no_receive_time_fabrication():
    row = parse("<134>Dec 31 23:59:59 host waf: tag:waf_log_login user:admin",
                received_at=datetime(2027, 1, 1, 0)).row
    assert row["event_time"] == datetime(2026, 12, 31, 15, 59, 59)
    row = parse("tag:waf_log_login user:admin").row
    assert row["event_time"] is None
    assert json.loads(row["syslog_meta"])["format"] == "vendor"


@pytest.mark.parametrize("text", ['<192>foo', '<134>1 bad host app - - [x a="missing"]junk body',
                                  '<134>1 bad host app - - [x a="unterminated body'])
def test_malformed_headers(text):
    with pytest.raises(ProtocolError):
        envelope(text, datetime(2026, 10, 28), device_timezone("+08:00"))


def test_framer_byte_counting_fragmentation_coalescing_and_newlines_inside_json():
    messages = [b"<1>one", '中文\nsecond'.encode(), b"three"]
    wire = b"".join(str(len(item)).encode() + b" " + item for item in messages)
    for chunk_size in (1, 2, 7, 65536):
        framer = TCPFramer()
        frames = []
        for offset in range(0, len(wire), chunk_size):
            frames.extend(framer.feed(wire[offset:offset + chunk_size]))
        framer.finish()
        assert frames == messages
    framer = TCPFramer(mode="line")
    assert list(framer.feed(b"one\r\ntwo\n\n")) == [b"one", b"two"]


@pytest.mark.parametrize("wire", [b"99999999999 ", b"0 x", b"01 x", b"x" * 12, b"11 abc"])
def test_framer_rejects_oversized_or_invalid_lengths(wire):
    with pytest.raises(ProtocolError):
        list(TCPFramer(max_bytes=10).feed(wire))


def test_framer_does_not_accept_truncated_frame_at_eof():
    framer = TCPFramer()
    assert list(framer.feed(b"10 abc")) == []
    with pytest.raises(ProtocolError):
        framer.finish()


class MemorySink:
    def __init__(self):
        self.rows = {}

    def write(self, parsed):
        self.rows.setdefault(parsed.row["ingest_id"], parsed)


def append(inbox, raw=b"tag:waf_log_login stat_time:2026-10-28 12:00:00 user:admin", **kwargs):
    return inbox.append(raw, product="waf", source_instance="waf-a", peer_ip="127.0.0.1",
                        transport="UDP", timezone_name="+08:00", **kwargs)


def test_inbox_restart_database_failure_quarantine_and_payload_budget(tmp_path):
    path = tmp_path / "inbox.sqlite3"
    inbox = Inbox(path, max_bytes=200)
    identifier = append(inbox)
    append(inbox, b"\xffbad")
    with pytest.raises(SpoolFull):
        append(inbox, b"x" * 200)
    assert inbox.status()["pending"] == 2
    inbox.close()
    inbox = Inbox(path, max_bytes=200)
    try:
        class Broken:
            def write(self, parsed):
                raise OSError("secret SQL must not be saved in error messages")
        assert drain_once(inbox, Broken())["failed"] == 1
        assert inbox.status()["pending"] == 2
        assert "secret" not in inbox.status()["lastError"]
        sink = MemorySink()
        result = drain_once(inbox, sink)
        assert result == dict(stored=1, quarantined=1, failed=0)
        assert identifier in sink.rows and inbox.status()["payloadBytes"] == 4
        inbox.retry_quarantine()
        assert inbox.status()["pending"] == 1
        assert drain_once(inbox, sink)["quarantined"] == 1
    finally:
        inbox.close()


def test_commit_before_ack_replay_uses_same_ingest_id_but_identical_new_messages_survive(tmp_path):
    inbox = Inbox(tmp_path / "inbox.sqlite3")
    sink = MemorySink()
    try:
        first, second = append(inbox), append(inbox)
        item = inbox.pending()[0]
        # Simulate crash after remote commit but before local acknowledge.
        sink.write(parse(item["raw"], ingest_id=item["ingest_id"]))
        assert drain_once(inbox, sink)["stored"] == 2
        assert set(sink.rows) == {first, second}
        assert inbox.status()["payloadBytes"] == 0
    finally:
        inbox.close()


def test_real_udp_tcp_receiver_long_messages_allowlist_and_shutdown(tmp_path):
    async def run():
        inbox = Inbox(tmp_path / "network.sqlite3")
        receiver = Receiver(inbox, product="das", source_instance="das-a", host="127.0.0.1", port=0,
                            allow_sources=("127.0.0.0/8",))
        await receiver.start()
        try:
            udp_payload = json.dumps(dict(log_type="audit", capture_time="2026-10-28 12:00:00",
                                          sql_statement="select '" + "x" * 8000 + "'"), indent=2).encode()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(udp_payload, ("127.0.0.1", receiver.port))
            reader, writer = await asyncio.open_connection("127.0.0.1", receiver.port)
            first = b'{"log_type":"session","capture_time":"2026-10-28 12:00:00","ss_action":"1"}'
            second = b'{"log_type":"warn","capture_time":"2026-10-28 12:00:00","sen_tag":"test"}'
            wire = str(len(first)).encode() + b" " + first + str(len(second)).encode() + b" " + second
            writer.write(wire[:3])
            await writer.drain()
            writer.write(wire[3:])
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            for _ in range(200):
                if inbox.status()["pending"] == 3:
                    break
                await asyncio.sleep(.01)
            assert inbox.status()["pending"] == 3
            assert {item["transport"] for item in inbox.pending()} == {"UDP", "TCP"}
            assert udp_payload in [item["raw"] for item in inbox.pending()]
            sink = MemorySink()
            assert drain_once(inbox, sink)["stored"] == 3
            assert {item.table for item in sink.rows.values()} == {"das_statement_log", "das_session_log", "das_sensitive_log"}
            assert not receiver.permits("192.0.2.1")
            await receiver.accept(b"not accepted", "192.0.2.1", "UDP")
            assert receiver.counters["rejected"] == 1
        finally:
            await receiver.close()
            inbox.close()
    asyncio.run(run())


def test_cancel_waits_for_inflight_durable_commit(tmp_path):
    async def run():
        inbox = Inbox(tmp_path / "cancel.sqlite3")
        original = inbox.append
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        inbox.append = slow
        receiver = Receiver(inbox, product="waf", source_instance="waf-a")
        task = asyncio.create_task(receiver.accept(b"tag:waf_log_login", "127.0.0.1", "TCP"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert inbox.status()["pending"] == 1
        finally:
            release.set()
            inbox.close()
    asyncio.run(run())


def test_six_table_ddl_matches_catalog_and_keeps_original_native_fields():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs/sql/device_syslog.sql").read_text(encoding="utf-8") == mysql_ddl()
    ddl = mysql_ddl()
    assert len(TABLES) == ddl.count("CREATE TABLE IF NOT EXISTS") == 6
    assert ddl.count("`raw_log` LONGTEXT NOT NULL") == 6
    assert ddl.count("`raw_bytes` LONGBLOB NOT NULL") == 6
    assert "`user` TEXT" in ddl and "`ss_action` TEXT" in ddl and "`sensise_tag_array` JSON" in ddl
    assert not any("acted" in columns for columns in BUSINESS.values())
    assert "UNIQUE KEY `uq_ingest_id`" in ddl
    assert "UNIQUE KEY `raw_sha256`" not in ddl


def test_writer_whitelist_and_native_column_quoting():
    statements = []
    row = parse("tag:waf_log_login stat_time:2026-10-28 12:00:00 user:admin").row
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, statement, params):
            statements.append((str(statement), params))
            return self
        def scalar_one(self):
            return row["raw_sha256"]
    class Engine:
        def begin(self):
            return Connection()
    store = MySQLDeviceStore(Engine())
    store.write(type("Parsed", (), dict(table="waf_audit_log", row=row)))
    assert "`user`" in statements[0][0] and ":user" in statements[0][0]
    assert "admin" not in statements[0][0]
    with pytest.raises(ValueError):
        store.write(type("Parsed", (), dict(table="bad; DROP TABLE x", row=row)))


def test_malformed_json_unicode_and_nesting_do_not_block_following_messages(tmp_path):
    inbox = Inbox(tmp_path / "malformed.sqlite3")
    try:
        for raw in (b'{"log_type":"audit","sql_statement":"\\ud800"}',
                    b'{"log_type":"audit","extension":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"):
            inbox.append(raw, product="das", source_instance="das-a", peer_ip="127.0.0.1",
                         transport="UDP", timezone_name="+08:00")
        append(inbox)
        sink = MemorySink()
        assert drain_once(inbox, sink) == dict(stored=1, quarantined=2, failed=0)
        assert inbox.status()["pending"] == 0
    finally:
        inbox.close()


def test_status_cli_does_not_start_receiver_or_connect_mysql(tmp_path, capsys):
    result = main(["--product", "waf", "--source-instance", "waf-a", "--status",
                   "--spool", str(tmp_path / "cli.sqlite3")])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["pending"] == 0


def test_actual_tcp_oversize_rejects_frame_and_listener_remains_usable(tmp_path):
    async def run():
        inbox = Inbox(tmp_path / "oversize.sqlite3")
        receiver = Receiver(inbox, product="waf", source_instance="waf-a", host="127.0.0.1", port=0,
                            protocols="tcp", max_bytes=64)
        await receiver.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", receiver.port)
            writer.write(b"10000000 x")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), timeout=2) == b""
            writer.close()
            await writer.wait_closed()
            assert receiver.counters["framing_errors"] == 1 and inbox.status()["pending"] == 0
            reader, writer = await asyncio.open_connection("127.0.0.1", receiver.port)
            writer.write(b"tag:waf_log_login\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            for _ in range(100):
                if inbox.status()["pending"]:
                    break
                await asyncio.sleep(.01)
            assert inbox.status()["pending"] == 1
        finally:
            await receiver.close()
            inbox.close()
    asyncio.run(run())


def lifecycle_settings(tmp_path, **overrides):
    from app.config import Settings
    # Port 0 only in tests: bind fresh ephemeral ports. User config requires 1..65535.
    values = dict(
        syslog_host="127.0.0.1", syslog_spool_dir=str(tmp_path),
        syslog_enabled=True, waf_syslog_enabled=True, das_syslog_enabled=True,
        waf_syslog_port=0, das_syslog_port=0, security_sync_enabled=False)
    values.update(overrides)
    return Settings(_env_file=None).model_copy(update=values)


def patch_lifecycle_store(monkeypatch):
    from app import syslog_server
    engine = type("Engine", (), {"disposed": False, "dispose": lambda self: setattr(self, "disposed", True)})()
    sink = MemorySink()
    monkeypatch.setattr(syslog_server, "create_store_engine", lambda url: engine)
    monkeypatch.setattr(syslog_server, "MySQLDeviceStore", lambda actual: sink)
    return engine, sink


def test_run_lifespan_starts_both_receivers_receives_and_releases_reload_ports(tmp_path, monkeypatch):
    from app import main as web
    engine, sink = patch_lifecycle_store(monkeypatch)
    config = lifecycle_settings(tmp_path)
    monkeypatch.setattr(web, "settings", config)
    async def idle():
        await asyncio.Event().wait()
    monkeypatch.setattr(web, "_status_watchdog", idle)
    monkeypatch.setattr(web, "_auto_archive_loop", idle)

    async def run():
        async with web.lifespan(web.app):
            receivers = web.app.state.syslog_receivers
            assert {r.product for r in receivers} == {"waf", "das"}
            ports = {r.product: r.port for r in receivers}
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(b"tag:waf_log_login stat_time:2026-10-28 12:00:00 user:admin", ("127.0.0.1", ports["waf"]))
                sock.sendto(b'{"log_type":"audit","capture_time":"2026-10-28 12:00:00","sql_statement":"select 1"}',
                            ("127.0.0.1", ports["das"]))
            for _ in range(300):
                if len(sink.rows) == 2:
                    break
                await asyncio.sleep(.01)
            assert {r.table for r in sink.rows.values()} == {"waf_audit_log", "das_statement_log"}
            assert all(r.row["raw_log"] for r in sink.rows.values())
        assert web.app.state.syslog_receivers == () and engine.disposed
        # Reload starts a fresh ASGI lifespan on the exact same TCP and UDP ports.
        monkeypatch.setattr(web, "settings", config.model_copy(update={"waf_syslog_port": ports["waf"], "das_syslog_port": ports["das"]}))
        async with web.lifespan(web.app):
            assert {r.product: r.port for r in web.app.state.syslog_receivers} == ports
    asyncio.run(run())


def test_all_disabled_opens_no_sockets_inboxes_or_engines(tmp_path, monkeypatch):
    from app import syslog_server
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled must not allocate resources")
    monkeypatch.setattr(syslog_server, "create_store_engine", forbidden)
    monkeypatch.setattr(syslog_server, "Inbox", forbidden)
    async def run():
        async with syslog_server.application_receivers(lifecycle_settings(tmp_path, syslog_enabled=False)) as receivers:
            assert receivers == ()
    asyncio.run(run())


def test_product_can_be_disabled_independently(tmp_path, monkeypatch):
    from app.syslog_server import application_receivers
    engine, sink = patch_lifecycle_store(monkeypatch)
    async def run():
        async with application_receivers(lifecycle_settings(tmp_path, das_syslog_enabled=False)) as receivers:
            assert len(receivers) == 1 and receivers[0].product == "waf"
    asyncio.run(run())
    assert engine.disposed
    assert not list(tmp_path.glob("das-*.sqlite3"))


def test_second_receiver_bind_failure_aborts_start_and_cleans_first_receiver(tmp_path, monkeypatch):
    from app import syslog_server
    engine, sink = patch_lifecycle_store(monkeypatch)
    started = []
    original_start = Receiver.start
    async def recording_start(self):
        result = await original_start(self)
        started.append(self)
        return result
    monkeypatch.setattr(Receiver, "start", recording_start)
    async def run():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            config = lifecycle_settings(tmp_path, das_syslog_port=occupied.getsockname()[1])
            with pytest.raises(RuntimeError, match="DAS Syslog cannot bind"):
                async with syslog_server.application_receivers(config):
                    pytest.fail("startup must not signal readiness")
        assert engine.disposed and len(started) == 1
        first = started[0]
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", first.port)
        # All local files were closed, and both transports can bind again.
        reopened = Inbox(first.inbox.path)
        try:
            replacement = Receiver(reopened, product="waf", source_instance="waf-site-a", host="127.0.0.1", port=first.port)
            await replacement.start()
            await replacement.close()
        finally:
            reopened.close()
    asyncio.run(run())


def test_syslog_environment_settings_and_bounds(monkeypatch):
    from app.config import Settings
    from pydantic import ValidationError
    defaults = Settings(_env_file=None)
    assert defaults.syslog_enabled and defaults.waf_syslog_port == 5514 and defaults.das_syslog_port == 5515
    monkeypatch.setenv("WAF_SYSLOG_ALLOW_SOURCES", '["192.0.2.10/32","198.51.100.0/24"]')
    monkeypatch.setenv("DAS_SYSLOG_ENABLED", "false")
    monkeypatch.setenv("WAF_SYSLOG_PORT", "6514")
    configured = Settings(_env_file=None)
    assert configured.waf_syslog_allow_sources == ["192.0.2.10/32", "198.51.100.0/24"]
    assert configured.waf_syslog_port == 6514 and not configured.das_syslog_enabled
    monkeypatch.setenv("WAF_SYSLOG_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_shutdown_finishes_current_write_and_retains_remaining_batch(tmp_path):
    from app.syslog_server import consume
    async def run():
        inbox = Inbox(tmp_path / "shutdown.sqlite3")
        receiver = Receiver(inbox, product="waf", source_instance="waf-a")
        for _ in range(3):
            append(inbox)
        entered, release = threading.Event(), threading.Event()
        sink = MemorySink()
        original = sink.write
        def blocking_write(parsed):
            entered.set()
            release.wait(3)
            original(parsed)
        sink.write = blocking_write
        worker = asyncio.create_task(consume(receiver, sink))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            worker.cancel()
            await asyncio.sleep(.01)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await worker
            assert len(sink.rows) == 1
            assert inbox.status()["pending"] == 2
        finally:
            release.set()
            inbox.close()
    asyncio.run(run())
