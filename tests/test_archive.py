"""存证归档（导出 + 删除）测试。

核心不变量（这些是删除不可逆场景下必须守住的）：

1. **落盘失败绝不删除**：CSV 写不出来时一条记录都不能删。
2. **先导出后删除**：删除用的 id 集合必须等于 CSV 里的记录集合。
3. **空结果不产生文件**：没有可归档记录时既不建文件也不删除。
4. **按 id 删除**，而不是按时间条件删——避免归档期间新写入的记录被误删。
5. **日历月回退**：3 个月前是月份算术，不是固定 90 天。

所有用例都 patch 数据访问层，不连接数据库（见 conftest.py）。
"""

from datetime import datetime
from pathlib import Path

import pytest

from app import archive


@pytest.fixture
def fake_db(monkeypatch):
    """用内存列表模拟 log_hash 表，记录所有发生的写操作。"""
    state = {"rows": [], "deleted": [], "ledger": []}

    def _where(start, end, before):
        # 复用被测代码自己的条件构造，保证过滤语义一致
        return archive._where_clause(start, end, before)

    def _match(row, start, end, before):
        created = row["created_at"]
        if before is not None and not created < before:
            return False
        if start is not None and not created >= start:
            return False
        if end is not None and not created <= end:
            return False
        return True

    def fake_count(start=None, end=None, before=None):
        return sum(1 for row in state["rows"] if _match(row, start, end, before))

    def fake_select_ids(start=None, end=None, before=None, limit=None):
        limit = limit if limit is not None else archive.settings.archive_batch_limit
        picked = [row["id"] for row in state["rows"] if _match(row, start, end, before)]
        return picked[:limit]

    def fake_fetch(ids):
        wanted = set(ids)
        return [row for row in state["rows"] if row["id"] in wanted]

    def fake_delete(ids):
        wanted = set(ids)
        before = len(state["rows"])
        state["rows"] = [row for row in state["rows"] if row["id"] not in wanted]
        removed = before - len(state["rows"])
        state["deleted"].extend(ids)
        return removed

    def fake_ledger(entry):
        state["ledger"].append(entry)

    monkeypatch.setattr(archive, "count_candidates", fake_count)
    monkeypatch.setattr(archive, "select_ids", fake_select_ids)
    monkeypatch.setattr(archive, "_fetch_rows", fake_fetch)
    monkeypatch.setattr(archive, "_delete_ids", fake_delete)
    monkeypatch.setattr(archive, "_record_ledger", fake_ledger)
    monkeypatch.setattr(archive, "_where_clause", _where)
    state["where"] = _where
    return state


def _row(id_, created_at, log="line", platform="linux"):
    return {
        "id": id_, "log_hash": f"{id_:064x}", "log_timestamp": "2026-07-01T00:00:00Z",
        "platform": platform, "source": "ubuntu-audit", "client_id": "host-1",
        "event_time": created_at, "created_at": created_at, "raw_log": log,
    }


# --- 日历月计算 ---------------------------------------------------------------

def test_months_ago_uses_calendar_months():
    """3 个月前 = 月份减 3，不是固定 90 天。"""
    assert archive.months_ago(3, datetime(2026, 10, 11, 12, 30)) == datetime(2026, 7, 11, 12, 30)


def test_months_ago_clamps_day_to_month_length():
    """3-31 减一个月时没有 2-31，收敛到当月最后一天（2-28）。"""
    assert archive.months_ago(1, datetime(2026, 3, 31)) == datetime(2026, 2, 28)


def test_months_ago_crosses_year_boundary():
    assert archive.months_ago(3, datetime(2026, 1, 15)) == datetime(2025, 10, 15)


def test_months_ago_handles_long_retention():
    assert archive.months_ago(25, datetime(2026, 3, 10)) == datetime(2024, 2, 10)


# --- 导出并删除 ---------------------------------------------------------------

def test_archive_exports_then_deletes(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1)), _row(2, datetime(2026, 1, 2))]

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert info["exported"] == 2
    assert info["deleted"] == 2
    assert fake_db["rows"] == []                       # 已删除
    assert sorted(fake_db["deleted"]) == [1, 2]        # 按 id 删除，不是按时间条件
    assert info["file"].endswith(".csv")
    assert len(info["fileSha256"]) == 64               # 文件摘要


def test_csv_contains_exactly_the_deleted_records(fake_db, tmp_path, monkeypatch):
    """CSV 的记录集合必须等于被删除的 id 集合——少一行就是永久丢数据。"""
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1)), _row(2, datetime(2026, 1, 2))]

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    # utf-8-sig 会去掉 BOM；若用 utf-8 读，首列名会带上 \ufeff 前缀
    text = open(info["file"], encoding="utf-8-sig").read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == len(fake_db["deleted"]) + 1   # 数据行 + 表头
    assert lines[0].split(",") == list(archive.ARCHIVE_COLUMNS)
    for id_ in fake_db["deleted"]:
        assert any(ln.startswith(f"{id_},") for ln in lines[1:])


def test_csv_is_utf8_with_bom_for_excel(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1), log="审计日志")]

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    raw = open(info["file"], "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf")             # BOM
    assert "审计日志" in raw.decode("utf-8-sig")


def test_filter_only_archives_older_records(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [
        _row(1, datetime(2026, 1, 1)),   # 早于截止时间，应归档
        _row(2, datetime(2026, 9, 1)),   # 晚于截止时间，应保留
    ]

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert info["deleted"] == 1
    assert [row["id"] for row in fake_db["rows"]] == [2]
    assert "2," not in open(info["file"], encoding="utf-8").read()


def test_empty_result_writes_no_file_and_deletes_nothing(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 9, 1))]

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert info["exported"] == 0 and info["deleted"] == 0
    assert info["file"] is None
    assert fake_db["deleted"] == []
    assert not list(tmp_path.glob("*.csv"))            # 没有可归档记录就不该建空文件


def test_dry_run_never_deletes(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1))]

    info = archive.archive_evidence(mode="manual", before=datetime(2026, 7, 1), dry_run=True)

    assert info["candidates"] == 1
    assert info["deleted"] == 0 and info["exported"] == 0
    assert info["dryRun"] is True
    assert len(fake_db["rows"]) == 1                   # 一条都没删
    assert not list(tmp_path.glob("*.csv"))


def test_manual_range_archives_only_that_window(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [
        _row(1, datetime(2026, 5, 31)),
        _row(2, datetime(2026, 6, 15)),
        _row(3, datetime(2026, 7, 1)),
    ]

    info = archive.archive_evidence(
        mode="manual", start=datetime(2026, 6, 1), end=datetime(2026, 6, 30, 23, 59, 59)
    )

    assert info["deleted"] == 1
    assert [row["id"] for row in fake_db["rows"]] == [1, 3]


# --- 失败时必须中止 -----------------------------------------------------------

def test_write_failure_deletes_nothing(fake_db, tmp_path, monkeypatch):
    """CSV 写失败（磁盘满/权限）时一条都不能删——这是最关键的安全不变量。"""
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1)), _row(2, datetime(2026, 1, 2))]

    def boom(rows_data, target):
        raise OSError("No space left on device")

    monkeypatch.setattr(archive, "_write_csv", boom)

    with pytest.raises(OSError):
        archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert len(fake_db["rows"]) == 2                   # 全部保留
    assert fake_db["deleted"] == []


def test_row_count_mismatch_aborts_before_writing(fake_db, tmp_path, monkeypatch):
    """取到的行数与 id 数不一致时宁可中止，也不写一个不完整的归档文件。"""
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1)), _row(2, datetime(2026, 1, 2))]
    monkeypatch.setattr(archive, "_fetch_rows", lambda ids: [_row(1, datetime(2026, 1, 1))])

    with pytest.raises(RuntimeError, match="不一致"):
        archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert len(fake_db["rows"]) == 2


def test_zero_byte_file_aborts_before_delete(fake_db, tmp_path, monkeypatch):
    """文件存在但为空也要中止：不能把"写了个空文件"当成导出成功。"""
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1))]

    def empty_file(rows_data, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
        return "0" * 64

    monkeypatch.setattr(archive, "_write_csv", empty_file)

    with pytest.raises(RuntimeError, match="校验失败"):
        archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert len(fake_db["rows"]) == 1


def test_ledger_write_failure_does_not_fail_archive(fake_db, tmp_path, monkeypatch):
    """台账写失败被吞掉：导出与删除仍然算成功。

    台账是事后追溯依据，但归档结果本身更重要——不能因为台账库不可用，
    就把已经落盘的导出和已完成的删除报成失败，让操作者以为数据还在。
    """
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1))]

    def failing_session():
        raise RuntimeError("monitor db down")

    monkeypatch.setattr(archive, "MonitorSessionLocal", failing_session)

    info = archive.archive_evidence(mode="auto", before=datetime(2026, 7, 1))

    assert info["deleted"] == 1                        # 删除照常完成
    assert info["exported"] == 1
    assert info["file"] and Path(info["file"]).exists()  # 文件照常保留
    assert fake_db["rows"] == []


# --- 台账内容 -----------------------------------------------------------------

def test_ledger_records_file_and_range(fake_db, tmp_path, monkeypatch):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    fake_db["rows"] = [_row(1, datetime(2026, 1, 1))]
    start, end = datetime(2026, 1, 1), datetime(2026, 1, 31)

    archive.archive_evidence(mode="manual", start=start, end=end)

    assert len(fake_db["ledger"]) == 1
    entry = fake_db["ledger"][0]
    assert entry["mode"] == "manual"
    assert entry["row_count"] == 1
    assert entry["deleted_count"] == 1
    assert entry["range_start"] == start and entry["range_end"] == end
    assert entry["file_path"].endswith(".csv")
    assert len(entry["file_sha256"]) == 64


# --- 表名与列顺序 -------------------------------------------------------------

def test_table_and_columns_are_fixed():
    assert archive._table() == "`log_hash`"
    assert archive.ARCHIVE_COLUMNS[0] == "id"
    assert "raw_log" in archive.ARCHIVE_COLUMNS      # 原文必须进归档文件
    assert "log_hash" in archive.ARCHIVE_COLUMNS     # 摘要必须进归档文件


def test_archive_root_anchors_relative_dir_to_repo(monkeypatch):
    """相对 archive_dir 必须锚定到仓库根，否则从不同工作目录启动会写到不同地方。"""
    monkeypatch.setattr(archive.settings, "archive_dir", "data/archive")
    root = archive.archive_root()
    assert root.is_absolute()
    assert root.parts[-2:] == ("data", "archive")
    assert (root.parent.parent / "backend").is_dir()   # 确实落在仓库根下


def test_archive_root_keeps_absolute_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(archive.settings, "archive_dir", str(tmp_path))
    assert archive.archive_root() == tmp_path


def test_format_handles_none_and_datetime():
    assert archive._format(None) == ""
    assert archive._format(datetime(2026, 7, 1, 8, 9, 10)) == "2026-07-01 08:09:10"
    assert archive._format(42) == "42"


def test_render_row_escapes_commas_and_quotes():
    """原文常含逗号与引号；转义错误会让 CSV 列数错乱。"""
    line = archive._render_row({
        "id": "1", "raw_log": 'msg="a,b" rest', "log_hash": "x",
        "log_timestamp": "", "platform": "", "source": "", "client_id": "",
        "event_time": "", "created_at": "",
    })
    assert '"msg=""a,b"" rest"' in line
    assert line.endswith("\r\n")
