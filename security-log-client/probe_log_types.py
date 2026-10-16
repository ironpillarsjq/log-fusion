#!/usr/bin/env python3
"""逐类探测截图中完整可见的 24 种绿盟安全设备日志，保存真实样例与查询结果。"""

import argparse
import calendar
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time

from nsfocus_api import ApiError, Client, DEFAULT_CONFIG, ROOT, save_json

VISIBLE_TYPES = (
    "ipslog", "avlog", "urllog", "ips_addefencelog", "ips_reputation", "dnsblacklistlog",
    "ddoslog", "sensitivefilelog", "scm_event", "ipblacklistlog", "sensitivedatalog", "cc_event_log",
    "ips_web_secure_event_log", "ips_malicious_file_event_log", "malicious_file_callback_event_log",
    "advance_threat_file_event_log", "ips_abaction_event_log", "applog", "tac_alert", "vpnlog",
    "fwlog", "authlog", "nf_ipflow", "database_audit_log",
)
CHINA = timezone(timedelta(hours=8))


def probe_one(client, name, start, end, directory, window):
    started = time.monotonic()
    payload = {"query_type": "es", "type": name, "startTime": start, "endTime": end,
               "page": 1, "size": 1, "query_string": "*"}
    result = {"window_hours": window, "startTime": start, "endTime": end, "request": payload}
    try:
        response, meta = client.request("POST", "/api/audit/log/search", body=payload)
        result.update(meta)
        response_file = f"responses/{name}_{window}h.json"
        save_json(directory / response_file, response)
        result["response_file"] = response_file
        if not isinstance(response, dict) or not isinstance(response.get("data"), list):
            raise ApiError("返回格式异常：缺少 data 数组")
        rows = response["data"]
        total = response.get("totalSize")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ApiError("返回格式异常：totalSize 不是非负整数")
        result.update(totalSize=total, returned_records=len(rows), partOfData=response.get("partOfData", False))
        if rows:
            row = rows[0]
            if not isinstance(row, dict) or not isinstance(row.get("source"), dict):
                raise ApiError("样例记录缺少 source 对象")
            source = row["source"]
            result.update(source_field_count=len(source), source_fields=sorted(source),
                          sample_index=row.get("index"), sample_table=source.get("table"),
                          sample_log_id_present=bool(source.get("log_id")))
            # 确认返回记录属于请求类别，避免把忽略 type 的结果误判成该类有数据。
            labels = [v for v in (row.get("index"), source.get("table")) if isinstance(v, str) and v]
            if name not in labels:
                result["status"] = "record_type_unverified"
            else:
                result["status"] = "has_data_partial" if result["partOfData"] else "has_data"
        elif total == 0 and not result["partOfData"]:
            result["status"] = "no_data"
        else:
            result["status"] = "count_without_sample"
    except (ApiError, OSError, ValueError) as exc:
        result.update(status="query_failed", error=str(exc), retryable=isinstance(exc, OSError),
                      elapsed_seconds=round(time.monotonic() - started, 3))
    return result


def combine(attempts):
    found = [a for a in attempts if a["status"] in ("has_data", "has_data_partial")]
    if found:
        return found[0]["status"]
    # 同一窗口的重试以最后一次为准；保留失败历史，避免恢复成功仍被标为失败。
    latest = {a.get("window_hours", i): a for i, a in enumerate(attempts)}
    if latest and all(a["status"] == "no_data" for a in latest.values()):
        return "no_data_in_windows"
    return "inconclusive"


def calendar_month_start(end, months):
    """按北京时间回溯日历月，月末超出目标月时取该月最后一天。"""
    point = datetime.fromtimestamp(end, CHINA)
    index = point.year * 12 + point.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    day = min(point.day, calendar.monthrange(year, month)[1])
    return int(point.replace(year=year, month=month, day=day).timestamp())


def write_reports(directory, report):
    save_json(directory / "report.json", report)
    windows = report.get("window_definitions", [{"window_hours": 24, "label": "最近24小时"}, {"window_hours": 168, "label": "最近7天"}])
    scope = ("每类均独立查询" + "、".join(w["label"] for w in windows) + "（按日历月回溯），有数据也继续查询较长窗口。"
             if report.get("scan_mode") == "calendar_months" else "先查最近 24 小时，未取得确认样例时再查最近 7 天。")
    lines = ["# 绿盟安全设备日志真实数据探测", "",
             f"开始时间：{report['started_at']}；固定查询结束时间：{report['query_end_china']}。", "",
             "范围：截图中完整可见的 24 类日志，不包含截图底部被裁切的菜单。" + scope,
             "每次只取第 1 页、1 条记录；有样例且 index/table 与请求类别一致才标记有数据。匹配数来自 API totalSize，非下载条数。",
             "0 只表示当前账号、时间窗口与查询条件下返回 0，不代表历史上从未有数据；失败和字段异常不算作 0。", "",
             "| 日志名称 | API 类型 | " + " | ".join(w["label"] + "匹配数" for w in windows) + " | 状态 | 单条样例字段数 |",
             "|---|---|" + "---:|" * len(windows) + "---|---:|"]
    status_labels = {"has_data": "有真实样例", "has_data_partial": "有样例，但返回部分数据",
                     "no_data_in_windows": "查询窗口内为0", "inconclusive": "尚不能确认"}
    for item in report["results"]:
        by_window = {a["window_hours"]: a for a in item["attempts"]}
        def count(hours):
            a = by_window.get(hours)
            if a is None:
                return "未查询"
            if a["status"] == "query_failed":
                return "查询失败"
            return str(a.get("totalSize", "待确认"))
        found = next((a for a in item["attempts"] if a["status"] in ("has_data", "has_data_partial")), {})
        lines.append(f"| {item['display']} | `{item['type']}` | " + " | ".join(count(w["window_hours"]) for w in windows)
                     + f" | {status_labels[item['status']]} | {found.get('source_field_count', '—')} |")
    lines += ["", "查询窗口（北京时间，日历月不固定等于30/90天）：", ""]
    for w in windows:
        if "startTime" in w:
            lines.append(f"- {w['label']}：{datetime.fromtimestamp(w['startTime'], CHINA).isoformat()} 至 {report['query_end_china']}。")
    lines += ["", f"已完成 {len(report['results'])}/{len(report['requested_types'])} 类，扫描状态：{report['scan_status']}。",
              "", "每次响应位于 responses/；report.json 还记录请求体、耗时、样例 index/table 和字段名。凭据与 Token 不写入报告。",
              "无实际数据库结构变化，无实际接口行为变化；只查询外部设备并保存本地文件。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan(client, directory, types=VISIBLE_TYPES, end=None, months=None):
    end = int(time.time()) if end is None else end
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "report.json").exists():
        raise ValueError("输出目录已有报告，请使用新目录")
    (directory / "responses").mkdir(exist_ok=True)
    windows = ([{"label": f"最近{m}个月", "startTime": calendar_month_start(end, m),
                 "window_hours": (end - calendar_month_start(end, m)) // 3600, "calendar_months": m} for m in months]
               if months else [{"label": "最近24小时", "startTime": end - 24 * 3600, "window_hours": 24},
                               {"label": "最近7天", "startTime": end - 168 * 3600, "window_hours": 168}])
    report = {"started_at": datetime.now(CHINA).isoformat(), "query_end_china": datetime.fromtimestamp(end, CHINA).isoformat(),
              "scan_mode": "calendar_months" if months else "short_windows", "window_definitions": windows,
              "query_end_epoch": end, "requested_types": list(types), "results": [], "scan_status": "running"}
    write_reports(directory, report)
    try:
        catalog, metadata = client.request("GET", "/api/v1/events/logs")
        if not isinstance(catalog, list):
            raise ApiError("日志类型目录不是数组")
        save_json(directory / "catalog.json", catalog)
        report["catalog_request"] = metadata
        by_name = {item["name"]: item for item in catalog if isinstance(item, dict) and isinstance(item.get("name"), str)}
        for name in types:
            item = {"type": name, "display": by_name.get(name, {}).get("display", name), "attempts": []}
            if name not in by_name:
                item.update(status="inconclusive", error="当前设备的目录中没有此类型；未猜测替代类型")
            else:
                for definition in windows:
                    window, start = definition["window_hours"], definition["startTime"]
                    attempt = probe_one(client, name, start, end, directory, window)
                    item["attempts"].append(attempt)
                    if attempt["status"] == "query_failed" and attempt.get("retryable"):
                        attempt = probe_one(client, name, start, end, directory, window)
                        attempt["retry"] = True
                        item["attempts"].append(attempt)
                    print(json.dumps({"type": name, "display": item["display"], "hours": window,
                                      "status": attempt["status"], "totalSize": attempt.get("totalSize"),
                                      "elapsed_seconds": attempt.get("elapsed_seconds"), "error": attempt.get("error")}, ensure_ascii=False), flush=True)
                    if not months and attempt["status"] in ("has_data", "has_data_partial"):
                        break
                item["status"] = combine(item["attempts"])
            report["results"].append(item)
            write_reports(directory, report)
        report["scan_status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        report.update(scan_status="failed", error=str(exc) if not isinstance(exc, KeyboardInterrupt) else "用户中止")
        raise
    finally:
        report["updated_at"] = datetime.now(CHINA).isoformat()
        write_reports(directory, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--months", type=int, nargs="+", help="逐一查询指定日历月窗口，如 --months 1 3；每个窗口都查")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout 必须为有限正数")
    if args.months and (any(m < 1 or m > 120 for m in args.months) or len(set(args.months)) != len(args.months)):
        parser.error("months 必须为不重复的 1～120 月整数")
    directory = args.output_dir or ROOT / "data/type-probes" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    try:
        client = Client(args.config, args.timeout, interval=0.2)
        # 先确认登录成功，防止失效 Token 的业务错误被逐类误判；不输出凭据。
        client.login()
        report = scan(client, directory, months=args.months)
        counts = {status: sum(r["status"] == status for r in report["results"])
                  for status in ("has_data", "has_data_partial", "no_data_in_windows", "inconclusive")}
        counts["unresolved_windows"] = sum(
            a["status"] not in ("has_data", "has_data_partial", "no_data")
            for item in report["results"]
            for a in {a["window_hours"]: a for a in item["attempts"]}.values())
        print(json.dumps(counts, ensure_ascii=False))
        print(f"报告：{(directory / 'report.md').resolve()}")
        return 1 if counts["inconclusive"] or counts["unresolved_windows"] else 0
    except (ApiError, OSError, ValueError) as exc:
        print(f"扫描失败：{exc}")
        return 1
    except KeyboardInterrupt:
        print("已中止；已完成结果保留。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
