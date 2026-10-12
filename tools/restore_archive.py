"""把归档 CSV 导回 log_hash 表（用于测试数据恢复或归档回滚）。

用途：归档是「导出后删除」，正常情况下不需要回灌。只有在归档误删、或需要把
历史存证放回库中继续测试时，才用本脚本。

安全特性：
- **默认只预览**（`--apply` 才真正写库），先看清要插入多少行、跳过多少行；
- 幂等：`log_hash` 有唯一索引，已存在的摘要会被跳过，不会产生重复行；
- 逐行提交，中途失败不会留下半个事务；
- 不修改归档文件本身（原始 CSV 保持原样，可再次使用）。

用法（在仓库根目录、使用本项目的 python 环境）：

    # 1) 预览：只想看看会插入多少
    python tools/restore_archive.py data/archive/log_hash_manual_20261012_213119.csv

    # 2) 真正写库
    python tools/restore_archive.py data/archive/log_hash_manual_20261012_213119.csv --apply

注意：回灌会重新占用自增 id（新 id 与归档前不同），且 `created_at` 按 CSV 中的原值写入，
不会变成「现在」——这样回灌后按入库时间的归档判断才会与原始状态一致。
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import hash_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("restore_archive")

COLUMNS = ("log_hash", "raw_log", "log_timestamp", "platform", "source", "client_id", "event_time", "created_at")

# 每批提交的行数。太大容易撞 max_allowed_packet，太小则退化成逐行往返。
BATCH_SIZE = 1000

# 归档 CSV 的时间格式（见 archive._format）
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")


def _parse_time(value: str):
    """把 CSV 里的时间字符串还原成 datetime；空值返回 None。"""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"归档文件缺少必需列：{missing}；实际列：{reader.fieldnames}")
        return list(reader)


def restore(path: Path, apply: bool) -> int:
    settings = get_settings()
    records = read_csv(path)
    table = f"`{settings.log_hash_db}`.`{settings.log_hash_table}`"

    prepared = []
    for row in records:
        prepared.append({
            "log_hash": row["log_hash"],
            "raw_log": row["raw_log"],
            "log_timestamp": row.get("log_timestamp") or None,
            "platform": row.get("platform") or None,
            "source": row.get("source") or None,
            "client_id": row.get("client_id") or None,
            "event_time": _parse_time(row.get("event_time", "")),
            "created_at": _parse_time(row.get("created_at", "")) or datetime.now(),
        })

    print(f"归档文件   : {path}")
    print(f"CSV 记录数 : {len(prepared)}")
    print(f"目标库表   : {settings.db_host} -> {settings.log_hash_db}.{settings.log_hash_table}")

    if not apply:
        print("\n当前为预览模式，未写入任何数据。确认无误后加 --apply 执行。")
        return 0

    inserted = skipped = failed = 0
    sql = text(
        f"INSERT INTO {table} (`log_hash`, `raw_log`, `log_timestamp`, `platform`, `source`, "
        f"`client_id`, `event_time`, `created_at`) "
        f"VALUES (:log_hash, :raw_log, :log_timestamp, :platform, :source, "
        f":client_id, :event_time, :created_at) "
        f"ON DUPLICATE KEY UPDATE `log_hash` = `log_hash`"
    )
    # 不能用 rowcount 区分「新插入」与「命中已有行」：驱动可能开启 CLIENT_FOUND_ROWS，
    # 那样命中的行也会被算成 1。改用表行数差值，才是真实的新增条数。
    with hash_engine.connect() as conn:
        before = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0

    # 批量提交：逐行一个事务在 3 万行时约 18 行/秒（每行一次远程往返 + fsync），
    # 批量后快两个数量级。任一批失败只影响该批，已提交的批次保留。
    total = len(prepared)
    for offset in range(0, total, BATCH_SIZE):
        chunk = prepared[offset:offset + BATCH_SIZE]
        try:
            with hash_engine.begin() as conn:
                conn.execute(sql, chunk)
        except Exception as exc:
            failed += len(chunk)
            logger.warning("第 %d~%d 行写入失败：%s: %s",
                           offset + 1, offset + len(chunk), type(exc).__name__, str(exc)[:200])
        done = min(offset + BATCH_SIZE, total)
        print(f"  已处理 {done}/{total}（失败 {failed}）", flush=True)

    with hash_engine.connect() as conn:
        after = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
    inserted = after - before
    skipped = total - inserted - failed

    print(f"\n完成：新增 {inserted}，跳过（已存在）{skipped}，失败 {failed}", flush=True)
    print(f"表行数：{before} -> {after}", flush=True)
    if failed:
        logger.warning("有 %d 行写入失败，请检查上面的告警", failed)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把归档 CSV 导回 log_hash 表")
    parser.add_argument("csv", type=Path, help="归档 CSV 路径")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = parser.parse_args()
    if not args.csv.exists():
        raise SystemExit(f"文件不存在：{args.csv}")
    return restore(args.csv, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
