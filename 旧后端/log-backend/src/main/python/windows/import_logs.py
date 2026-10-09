"""Import all Application/Security/Setup/System/ForwardedEvents EVTX files."""
import argparse
import re
from pathlib import Path
from .config import DEFAULT_LOG_DIRS, CATEGORIES
from .db import connection
from .evtx_parser import records
from .schema import table_name, SYSTEM_COLUMNS

INSERT_COLUMNS = ["source_year", "source_file"] + [name for name, _ in SYSTEM_COLUMNS] + ["eventdata"]
SQL_CACHE = {}


def category_for(path):
    for category in CATEGORIES:
        if path.name.lower().startswith(category.lower() + "_"):
            return category
    return None


def import_file(conn, path, batch_size=500):
    category = category_for(path)
    if not category:
        return 0
    year_match = re.search(r"_(20\d{2})\.evtx$", path.name, re.I)
    year = int(year_match.group(1)) if year_match else None
    placeholders = ",".join(["%s"] * len(INSERT_COLUMNS))
    sql = SQL_CACHE.setdefault(category, f"INSERT INTO `{table_name(category)}` ({','.join(INSERT_COLUMNS)}) VALUES ({placeholders})")
    count, batch = 0, []
    with conn.cursor() as cur:
        for row in records(path, year):
            batch.append([row.get(col) for col in INSERT_COLUMNS])
            if len(batch) >= batch_size:
                cur.executemany(sql, batch); conn.commit(); count += len(batch); batch.clear()
        if batch:
            cur.executemany(sql, batch); conn.commit(); count += len(batch)
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", action="append", type=Path, dest="log_dirs")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    log_dirs = args.log_dirs or DEFAULT_LOG_DIRS
    files = [p for root in log_dirs if root.exists() for p in root.rglob("*.evtx") if category_for(p)]
    total = 0
    with connection() as conn:
        for path in sorted(files):
            n = import_file(conn, path, args.batch_size)
            print(f"{path.name}: {n} 条")
            total += n
    print(f"导入完成，共 {total} 条。")


if __name__ == "__main__":
    main()
