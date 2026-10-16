#!/usr/bin/env python3
"""Create reviewed tables, initialize a resumable three-month import, or run its worker."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings
from app.db import monitor_engine
from app.security_logs import CATALOG, PROJECT, SecurityStore, SecurityWorker
from sqlalchemy import text


def migrate():
    settings = get_settings()
    if settings.log_fusion_db != "log_fusion":
        raise ValueError("DDL targets log_fusion; configured database must match")
    # CREATE is additive. Existing tables must match catalog columns, types and unique key.
    with monitor_engine.connect() as connection:
        for filename in ("log_fusion_nsfocus_tables.sql", "nsfocus_sync_state.sql"):
            sql = (PROJECT / "sql" / filename).read_text(encoding="utf-8")
            sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
            for statement in sql.split(";"):
                if not statement.strip():
                    continue
                import re
                table = re.search(r"CREATE TABLE(?: IF NOT EXISTS)? `log_fusion`\.`([^`]+)`", statement).group(1)
                exists = connection.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='log_fusion' AND table_name=:name"), {"name": table}).scalar()
                if not exists:
                    connection.execute(text(statement))
                    connection.commit()  # MySQL DDL commits implicitly.
                    print(json.dumps({"created": table}), flush=True)
                else:
                    print(json.dumps({"existing": table}), flush=True)
        for spec in CATALOG.values():
            columns = list(connection.execute(text("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.columns WHERE TABLE_SCHEMA='log_fusion' AND TABLE_NAME=:name"), {"name": spec["table"]}))
            actual = {r.COLUMN_NAME for r in columns}
            expected = {f["field"] for f in spec["fields"]} | {"id", "source_instance", "upstream_index", "event_time", "received_time", "ingested_at", "raw_record", "record_sha256"}
            if actual != expected:
                raise ValueError("existing schema columns mismatch: " + spec["table"])
            types = {f["field"]: (f["sql_type"].lower(), "YES" if f["nullable"] else "NO") for f in spec["fields"]}
            types.update(id=("bigint unsigned", "NO"), source_instance=("varchar(64)", "NO"),
                         upstream_index=("varchar(255)", "NO"), event_time=("datetime(3)", "NO"),
                         received_time=("datetime(3)", "YES"), ingested_at=("datetime(3)", "NO"),
                         raw_record=("json", "NO"), record_sha256=("binary(32)", "NO"))
            if any((r.COLUMN_TYPE.lower(), r.IS_NULLABLE) != types[r.COLUMN_NAME] for r in columns):
                raise ValueError("existing schema types/nullability mismatch: " + spec["table"])
            keys = connection.execute(text("SELECT INDEX_NAME,NON_UNIQUE,GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cols FROM information_schema.statistics WHERE TABLE_SCHEMA='log_fusion' AND TABLE_NAME=:name GROUP BY INDEX_NAME,NON_UNIQUE"), {"name": spec["table"]}).mappings().all()
            if not any(k["NON_UNIQUE"] == 0 and k["cols"] == "source_instance,upstream_index,log_id" for k in keys):
                raise ValueError("missing security log identity unique key: " + spec["table"])
        print(json.dumps({"schema_columns_types_unique_key_verified": True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "init", "run", "status"))
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--history-only", action="store_true", help="只补录历史，不执行实时巡检")
    parser.add_argument("--until-complete", action="store_true", help="三类历史断点全部完成后退出")
    args = parser.parse_args()
    if args.command == "run" and args.until_complete and not args.history_only:
        parser.error("--until-complete requires --history-only; default run only polls recent logs")
    settings = get_settings()
    store = SecurityStore(monitor_engine, settings.security_source_instance)
    if args.command == "migrate":
        migrate()
    elif args.command == "init":
        print(json.dumps({"initialized": store.initialize(args.months, end=int(time.time()), extend=True)}, ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(store.summary(), ensure_ascii=False, indent=2))
    else:
        config = Path(settings.security_config_file)
        if not config.is_absolute():
            config = ROOT / config
        worker = SecurityWorker(store, config, page_size=settings.security_page_size,
                                poll_seconds=settings.security_poll_seconds,
                                overlap=settings.security_overlap_seconds,
                                lag=settings.security_lag_seconds, timeout=settings.security_timeout_seconds,
                                history_only=args.history_only, live_only=not args.history_only,
                                live_window=settings.security_live_window_seconds)
        initialize = (lambda: store.initialize(args.months, end=int(time.time()), extend=True)) if args.history_only else (lambda: store.initialize_live(overlap=settings.security_overlap_seconds, lag=settings.security_lag_seconds))
        while not initialize():
            time.sleep(1)
        while True:
            event = worker.step()
            if event:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            if args.once:
                break
            if args.until_complete and all(t.get("history", {}).get("status") == "complete" for t in store.summary()["types"]):
                print(json.dumps({"history_complete": True}), flush=True)
                break
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. Committed logs and checkpoints are preserved; rerun the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
