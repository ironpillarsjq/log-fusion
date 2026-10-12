"""存证归档层：把 `log_hash.log_hash` 里的历史记录导出为 CSV，确认落盘后再删除。

安全流程（顺序不可颠倒，宁可重复导出也不能丢数据）：

1. 先按条件**只查询 id**（带批次上限），拿到本次要归档的确定集合；
2. 流式写 CSV：分批查询、边查边写，不在内存里堆整表；
3. `flush` + `fsync` 关闭文件，并校验文件存在、大小 > 0、数据行数等于 id 数；
4. 任一步失败**立即中止，绝不执行 DELETE**；
5. 用明确的 **id 列表**删除（不是 `DELETE WHERE created_at < X`），
   否则导出期间新写入、同样满足条件的记录会被误删；
6. 删除成功后写一条归档台账，记录文件路径、行数、时间区间与 CSV 的 SHA-256。

台账写入失败**不影响**已经完成的导出与删除，只记录日志——归档结果比台账更重要。
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .config import get_settings
from .db import MonitorSessionLocal, hash_engine, rows
from .models import ArchiveLog

logger = logging.getLogger("log_fusion.archive")
settings = get_settings()

# 归档 CSV 的列顺序（与前端展示、台账校验保持一致）
ARCHIVE_COLUMNS = (
    "id", "log_hash", "log_timestamp", "platform", "source", "client_id",
    "event_time", "created_at", "raw_log",
)

# 按 id 分批查询的批大小：避免一条 SQL 的 IN 列表过长
SELECT_CHUNK = 1000


def _table() -> str:
    return f"`{settings.log_hash_table}`"


def archive_root() -> Path:
    """归档根目录的**绝对路径**。

    `archive_dir` 默认是相对路径（`data/archive`），而相对路径是相对进程工作目录
    解析的：从 `backend/` 启动和从仓库根目录启动会写到不同的地方，台账里记下的
    也就成了随启动方式变化的路径。这里统一按**仓库根目录**展开，保证不论怎么
    启动，归档文件都落在同一个位置（`<repo>/data/archive`）。
    """
    configured = Path(settings.archive_dir)
    if configured.is_absolute():
        return configured
    repo_root = Path(__file__).resolve().parents[2]  # backend/app/archive.py -> 仓库根
    return repo_root / configured


def _where_clause(start: datetime | None, end: datetime | None, before: datetime | None) -> tuple[str, dict[str, Any]]:
    """构造时间过滤条件。`before` 与 `start/end` 互斥（自动归档 vs 手动区间）。"""
    if before is not None:
        return "created_at < :before", {"before": before}
    conditions, params = [], {}
    if start is not None:
        conditions.append("created_at >= :start")
        params["start"] = start
    if end is not None:
        conditions.append("created_at <= :end")
        params["end"] = end
    return (" AND ".join(conditions) if conditions else "1=1"), params


def count_candidates(
    start: datetime | None = None,
    end: datetime | None = None,
    before: datetime | None = None,
) -> int:
    """统计满足条件的存证条数（只读，用于「仅统计」预览）。"""
    where, params = _where_clause(start, end, before)
    result = rows(hash_engine, f"SELECT COUNT(*) AS n FROM {_table()} WHERE {where}", params)
    return int(result[0]["n"]) if result else 0


def select_ids(
    start: datetime | None = None,
    end: datetime | None = None,
    before: datetime | None = None,
    limit: int | None = None,
) -> list[int]:
    """取出待归档的 id 列表（有序，便于分批查询与稳定输出）。"""
    where, params = _where_clause(start, end, before)
    limit = limit if limit is not None else settings.archive_batch_limit
    params = dict(params, limit=limit)
    result = rows(
        hash_engine,
        f"SELECT id FROM {_table()} WHERE {where} ORDER BY id LIMIT :limit",
        params,
    )
    return [int(row["id"]) for row in result]


def _fetch_rows(ids: list[int]) -> list[dict]:
    """按 id 分批取完整行，保证与 select_ids 得到的集合一致。"""
    collected: list[dict] = []
    for offset in range(0, len(ids), SELECT_CHUNK):
        chunk = ids[offset:offset + SELECT_CHUNK]
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
        params = {f"id{i}": value for i, value in enumerate(chunk)}
        columns = ", ".join(f"`{name}`" for name in ARCHIVE_COLUMNS)
        collected.extend(rows(
            hash_engine,
            f"SELECT {columns} FROM {_table()} WHERE id IN ({placeholders}) ORDER BY id",
            params,
        ))
    return collected


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _render_row(record: dict) -> str:
    """把一行渲染成 CSV 文本（含结尾 CRLF）。"""
    buffer = io.StringIO(newline="")
    csv.DictWriter(buffer, fieldnames=list(ARCHIVE_COLUMNS), extrasaction="ignore").writerow(record)
    return buffer.getvalue()


def _write_csv(rows_data: list[dict], target: Path) -> str:
    """写 CSV（UTF-8 带 BOM，方便 Excel 直接打开），返回文件内容的 SHA-256。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    # newline="" 是 csv 模块在 Windows 上的要求，否则每行会多一个空行
    with target.open("w", encoding="utf-8", newline="") as handle:
        def emit(text: str) -> None:
            handle.write(text)
            digest.update(text.encode("utf-8"))

        emit("\ufeff")
        emit(_render_row({name: name for name in ARCHIVE_COLUMNS}))  # 表头
        for row in rows_data:
            emit(_render_row({name: _format(row.get(name)) for name in ARCHIVE_COLUMNS}))
        handle.flush()
        os.fsync(handle.fileno())
    return digest.hexdigest()


def _delete_ids(ids: list[int]) -> int:
    """按明确 id 列表删除，返回实际删除行数。"""
    deleted = 0
    for offset in range(0, len(ids), SELECT_CHUNK):
        chunk = ids[offset:offset + SELECT_CHUNK]
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
        params = {f"id{i}": value for i, value in enumerate(chunk)}
        with hash_engine.begin() as conn:
            result = conn.execute(text(f"DELETE FROM {_table()} WHERE id IN ({placeholders})"), params)
            deleted += result.rowcount or 0
    return deleted


def _record_ledger(entry: dict) -> None:
    """写归档台账。失败只记日志，不回滚已完成的归档。"""
    try:
        with MonitorSessionLocal() as db:
            db.add(ArchiveLog(**entry))
            db.commit()
    except Exception:
        logger.exception("归档台账写入失败（导出与删除已完成）：%s", entry.get("file_path"))


def archive_evidence(
    *,
    mode: str,
    start: datetime | None = None,
    end: datetime | None = None,
    before: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """导出并删除存证记录。

    `dry_run=True` 只统计不落盘、不删除，用于前端「仅统计」预览。
    返回体的 `deleted` 表示实际删除行数；`dry_run` 为真时恒为 0。
    """
    total = count_candidates(start, end, before)
    if dry_run:
        return {"mode": mode, "candidates": total, "exported": 0, "deleted": 0,
                "file": None, "fileSha256": None, "dryRun": True}

    if total == 0:
        return {"mode": mode, "candidates": 0, "exported": 0, "deleted": 0,
                "file": None, "fileSha256": None, "dryRun": False}

    ids = select_ids(start, end, before)
    if not ids:
        return {"mode": mode, "candidates": 0, "exported": 0, "deleted": 0,
                "file": None, "fileSha256": None, "dryRun": False}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = archive_root() / f"log_hash_{mode}_{stamp}.csv"
    rows_data = _fetch_rows(ids)
    if len(rows_data) != len(ids):
        # 理论上不会发生（同一次会话内），但不一致时宁可中止
        raise RuntimeError(f"待归档行数与查询结果不一致：ids={len(ids)} rows={len(rows_data)}")

    file_digest = _write_csv(rows_data, target)

    # 落盘校验：文件存在、非空、数据行数吻合，才允许删除
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError(f"归档文件校验失败，未执行删除：{target}")

    deleted = _delete_ids(ids)
    _record_ledger({
        "mode": mode,
        "file_path": str(target),
        "row_count": len(rows_data),
        "file_sha256": file_digest,
        "range_start": start,
        "range_end": end,
        "before_time": before,
        "deleted_count": deleted,
    })
    return {
        "mode": mode,
        "candidates": total,
        "exported": len(rows_data),
        "deleted": deleted,
        "file": str(target),
        "fileSha256": file_digest,
        "dryRun": False,
    }


def months_ago(months: int, now: datetime | None = None) -> datetime:
    """返回 `months` 个日历月之前的时间点（按月份回退，不是固定 30 天）。"""
    now = now or datetime.now()
    month_index = now.month - 1 - months
    year = now.year + month_index // 12
    month = month_index % 12 + 1
    day = min(now.day, _days_in_month(year, month))
    return now.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
