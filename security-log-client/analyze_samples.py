#!/usr/bin/env python3
"""离线分析 nsfocus_api.py 保存的日志分页，输出 JSONL 样例、JSON 统计及中文报告。"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics

CHINA = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent


def filled(value):
    return value is not None and value != "" and value != [] and value != {}


def text(value):
    return str(value) if filled(value) else "（缺失）"


def raw_field(raw, name):
    match = re.search(r"(?<!\w)" + re.escape(name) + r"=([^;]*)", raw)
    return match.group(1).strip() if match else None


def local_time(epoch):
    return datetime.fromtimestamp(epoch, CHINA).isoformat()


def number(value):
    try:
        result = float(value)
        return result if -1e15 < result < 1e15 else None
    except (ValueError, TypeError):
        return None


def analyze(input_dir, output_dir):
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    counters = {name: Counter() for name in ("event_codes", "operations", "hosts", "device_ips", "risk_values",
                                             "severity_values", "os_versions", "logon_types", "raw_audit_types")}
    fields = ("log_id", "host", "dev_ip", "timestamp", "oevent_id", "raw_data", "operation")
    missing = Counter({field: 0 for field in fields})
    checks = {key: {"compared": 0, "mismatched": 0} for key in ("event_code", "device_ip", "hostname", "timestamp")}
    hostname_pairs, failure_codes, failure_hosts = Counter(), Counter(), Counter()
    hostname_domain_only = 0
    os_devices = {}
    seen, rows, epochs, gaps = set(), [], [], []
    read_count = duplicates = missing_ids = malformed = invalid_timestamps = invalid_receive_times = 0
    eventidentifier_zero = timestamp_log_time_mismatches = 0
    for item in manifest.get("pages", []):
        page_path = (input_dir / item["file"]).resolve()
        if not page_path.is_relative_to(input_dir) or page_path == input_dir:
            raise ValueError("manifest 的分页文件必须位于输入目录内")
        page = json.loads(page_path.read_text(encoding="utf-8-sig"))
        if not isinstance(page.get("data"), list):
            raise ValueError(f"分页缺少 data 数组：{page_path.name}")
        for row in page["data"]:
            read_count += 1
            if not isinstance(row, dict) or not isinstance(row.get("source"), dict):
                malformed += 1
                continue
            source = row["source"]
            if not filled(source.get("log_id")):
                missing_ids += 1
                identity = "sha256:" + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            else:
                identity = json.dumps([row.get("index"), source["log_id"]], ensure_ascii=False)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            rows.append(row)
            for field in fields:
                missing[field] += not filled(source.get(field))
            for name, field in (("event_codes", "oevent_id"), ("operations", "operation"), ("hosts", "host"),
                                ("device_ips", "dev_ip"), ("risk_values", "risk"), ("severity_values", "severity"),
                                ("os_versions", "os_version"), ("logon_types", "logon_type")):
                counters[name][text(source.get(field))] += 1
            eventidentifier_zero += str(source.get("eventidentifier")) == "0"
            os_devices.setdefault(text(source.get("os_version")), set()).add(text(source.get("dev_ip")))
            epoch = number(source.get("timestamp"))
            if epoch is not None:
                try:
                    local_time(epoch)
                    epochs.append(epoch)
                except (ValueError, OverflowError, OSError):
                    epoch = None
            invalid_timestamps += epoch is None
            received = number(source.get("recv_time"))
            invalid_receive_times += received is None
            if epoch is not None and received is not None:
                gaps.append(received / 1000 - epoch)
            log_time = number(source.get("log_time"))
            if epoch is not None and log_time is not None:
                timestamp_log_time_mismatches += epoch != log_time
            raw = source.get("raw_data")
            raw = raw if isinstance(raw, str) else ""
            audit_type = raw_field(raw, "Type")
            counters["raw_audit_types"][text(audit_type)] += 1
            if audit_type == "审核失败":
                failure_codes[text(source.get("oevent_id"))] += 1
                failure_hosts[text(source.get("host"))] += 1
            comparisons = (("event_code", raw_field(raw, "EventCode"), source.get("oevent_id")),
                           ("device_ip", raw_field(raw, "dev_ip"), source.get("dev_ip")),
                           ("hostname", raw_field(raw, "ComputerName"), source.get("host")),
                           ("timestamp", raw_field(raw, "TimeGenerated"), source.get("timestamp")))
            for key, original, parsed in comparisons:
                if filled(original) and filled(parsed):
                    checks[key]["compared"] += 1
                    checks[key]["mismatched"] += str(original).casefold() != str(parsed).casefold()
                    if key == "hostname" and str(original).casefold() != str(parsed).casefold():
                        hostname_pairs[(str(parsed), str(original))] += 1
                        hostname_domain_only += str(original).split(".")[0].casefold() == str(parsed).casefold()
    if not rows:
        raise ValueError("没有可分析的 source 日志记录")
    total = len(rows)
    result = {
        "generated_at": datetime.now(CHINA).isoformat(), "input_directory": str(input_dir),
        "request": manifest.get("query"), "capture_status": manifest.get("status"),
        "sample": {"downloaded_rows": read_count, "unique_rows": total, "duplicate_rows": duplicates,
                   "malformed_rows": malformed, "rows_without_log_id_before_dedup": missing_ids,
                   "page_count": len(manifest.get("pages", [])),
                   "api_total_size_by_page": [p.get("totalSize") for p in manifest.get("pages", [])]},
        "event_time": {"min_china": local_time(min(epochs)) if epochs else None,
                       "max_china": local_time(max(epochs)) if epochs else None,
                       "span_seconds": max(epochs) - min(epochs) if epochs else None,
                       "outside_request_window": sum(not manifest["query"]["startTime"] <= t <= manifest["query"]["endTime"] for t in epochs)
                       if all(k in manifest.get("query", {}) for k in ("startTime", "endTime")) else None},
        "distributions": {name: [{"value": value, "count": count, "percent": round(count / total * 100, 2)}
                                  for value, count in values.most_common()] for name, values in counters.items()},
        "quality": {"missing_fields": dict(missing), "raw_parsed_checks": checks,
                    "hostname_domain_only_rows": hostname_domain_only,
                    "hostname_mismatch_pairs": [{"parsed_host": parsed, "raw_computer_name": original, "count": count}
                                                for (parsed, original), count in hostname_pairs.most_common()],
                    "eventidentifier_zero_rows": eventidentifier_zero, "invalid_event_timestamps": invalid_timestamps,
                    "invalid_receive_timestamps": invalid_receive_times,
                    "timestamp_log_time_mismatch_rows": timestamp_log_time_mismatches},
        "audit_failures": {"total": sum(failure_codes.values()), "event_codes": dict(failure_codes.most_common()),
                           "hosts": dict(failure_hosts.most_common())},
        "os_device_counts": {key: len(ips - {"（缺失）"}) for key, ips in os_devices.items()},
        "receive_minus_event_seconds": None,
        "limitations": ["指定查询范围的前几页是便利样本，不是随机样本；比例不能代表全天或全部设备。",
                        "risk、severity 的日志字段映射未经确认，不能套用事件接口的 1/3/5 风险等级。",
                        "recv_time 按毫秒、timestamp 按秒计算差值；差值包含时钟及流水线因素，不等于网络延迟。",
                        "未将 eventidentifier 当作 Windows 事件码，事件码统计使用 oevent_id 并与 raw_data.EventCode 比对。"]}
    if gaps:
        ordered = sorted(gaps)
        result["receive_minus_event_seconds"] = {"count": len(gaps), "min": round(min(gaps), 3),
                                                  "median": round(statistics.median(gaps), 3),
                                                  "p95": round(ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)], 3),
                                                  "max": round(max(gaps), 3), "negative_count": sum(g < 0 for g in gaps)}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "samples.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    write_report(result, output_dir / "analysis.md")
    return result


def write_report(result, path):
    sample, quality = result["sample"], result["quality"]
    total = sample["unique_rows"]
    lines = ["# 安全产品日志样例分析", "", f"生成时间：{result['generated_at']}（北京时间）。", "",
             "## 数据范围", "",
             f"- 来自 `{result['input_directory']}` 的 {sample['page_count']} 页真实响应。",
             f"- 下载 {sample['downloaded_rows']:,} 条，去重后 {total:,} 条，重复 {sample['duplicate_rows']} 条，格式异常 {sample['malformed_rows']} 条。",
             f"- 实际日志时间：{result['event_time']['min_china']} 至 {result['event_time']['max_china']}。",
             f"- 各页 API 匹配总数：{sample['api_total_size_by_page']}；仅分析下载样例，不是分析全部匹配日志。",
             "- 按 API 返回顺序取前几页，不是随机抽样，不能据此推算全天占比或事件发生率。", ""]
    query = result.get("request") or {}
    if "startTime" in query and "endTime" in query:
        lines += [f"请求时间范围：{local_time(query['startTime'])} 至 {local_time(query['endTime'])}。", ""]
    for title, key, limit in (("Windows 事件码（oevent_id）", "event_codes", 10), ("操作分类", "operations", 10),
                              ("TOP 主机", "hosts", 10), ("TOP 设备 IP", "device_ips", 10),
                              ("操作系统版本原值", "os_versions", 10), ("原始日志审计结果（Type）", "raw_audit_types", 10),
                              ("risk 字段原值", "risk_values", 10), ("severity 字段原值", "severity_values", 10)):
        lines += [f"## {title}", "", "| 值 | 条数 | 样例占比 |", "|---|---:|---:|"]
        for row in result["distributions"][key][:limit]:
            value = row["value"].replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            lines.append(f"| {value} | {row['count']:,} | {row['percent']:.2f}% |")
        lines.append("")
    lines += ["## 字段质量与解析一致性", "", "| 字段 | 缺失/空值 |", "|---|---:|"]
    lines += [f"| {key} | {count} |" for key, count in quality["missing_fields"].items()]
    lines += ["", "| 原文与解析字段比较 | 可比较条数 | 不一致条数 |", "|---|---:|---:|"]
    lines += [f"| {key} | {value['compared']} | {value['mismatched']} |" for key, value in quality["raw_parsed_checks"].items()]
    lines += ["", f"`eventidentifier=0` 有 {quality['eventidentifier_zero_rows']} 条；分析使用 `oevent_id`，避免误把 0 当作真实 Windows 事件码。",
              f"无效事件时间 {quality['invalid_event_timestamps']} 条，无效接收时间 {quality['invalid_receive_timestamps']} 条；`timestamp` 与 `log_time` 不一致 {quality['timestamp_log_time_mismatch_rows']} 条。",
              f"落在请求时间范围外的样例：{result['event_time']['outside_request_window']} 条。", ""]
    if quality["hostname_mismatch_pairs"]:
        lines += ["主机名差异明细（已忽略大小写）：", "", "| 解析后的 host | 原文 ComputerName | 条数 |", "|---|---|---:|"]
        lines += [f"| {pair['parsed_host']} | {pair['raw_computer_name']} | {pair['count']} |"
                  for pair in quality["hostname_mismatch_pairs"][:10]]
        lines += ["", f"其中 {quality['hostname_domain_only_rows']} 条仅涉及原文域名后缀与短名的差异；其余需核对设备别名或截断规则，不直接作为内容异常。", ""]
    failures = result["audit_failures"]
    lines += ["## 审核失败样例", "", f"原文 `Type=审核失败` 共 **{failures['total']} 条**，属于审计结果标记，不等同于已确认攻击。", "",
              "| 事件码 | 条数 |", "|---|---:|"]
    lines += [f"| {code} | {count} |" for code, count in failures["event_codes"].items()]
    lines += ["", "按主机计数：" + "，".join(f"`{host}` {count} 条" for host, count in failures["hosts"].items()) + "。", ""]
    gaps = result["receive_minus_event_seconds"]
    if gaps:
        lines += ["## 接收时间与事件时间差", "",
                  f"按 `recv_time / 1000 - timestamp` 计算：中位数 **{gaps['median']} 秒**，P95 **{gaps['p95']} 秒**，最小 {gaps['min']} 秒，最大 {gaps['max']} 秒，负值 {gaps['negative_count']} 条。",
                  "这是两个设备字段的时间差，可能包含采集、排队、转发和设备时钟因素，不能直接视为网络耗时。", ""]
    lines += ["## 可直接使用的结论与后续核对", ""]
    top = result["distributions"]["event_codes"][0]
    lines.append(f"- 样例最常见事件码是 **{top['value']}**：{top['count']} 条，占 {top['percent']:.2f}%。具体含义先结合原始日志和操作分类确认。")
    lines.append(f"- 样例包含 {sum(x['value'] != '（缺失）' for x in result['distributions']['hosts'])} 个主机名、{sum(x['value'] != '（缺失）' for x in result['distributions']['device_ips'])} 个设备 IP；主机名统计保留设备原值，未把同 IP 的不同名称自动合并。")
    if quality["missing_fields"]["operation"]:
        lines.append(f"- {quality['missing_fields']['operation']} 条日志没有操作分类；做业务统计时应保留未知分类，不能静默丢弃。")
    mismatched = sum(v["mismatched"] for v in quality["raw_parsed_checks"].values())
    lines.append(f"- 四组原文/解析字段比对累计不一致 {mismatched} 次；主机名比较忽略大小写。不一致应先核对字段口径，不能直接判定日志被篡改。")
    lines.append("- 事件查询为空与原始日志有数据是不同查询层次，不能将空事件列表等同于设备没有安全问题。")
    lines += ["", "## 分析边界", ""] + ["- " + limit for limit in result["limitations"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path, help="包含 manifest.json 和分页 JSON 的目录")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    output_dir = args.output_dir or ROOT / "reports" / args.input_dir.resolve().name
    try:
        result = analyze(args.input_dir, output_dir)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"分析失败：{exc}\n")
    print(json.dumps({"sample": result["sample"], "event_time": result["event_time"],
                      "top_event_codes": result["distributions"]["event_codes"][:5],
                      "receive_minus_event_seconds": result["receive_minus_event_seconds"],
                      "report": str((output_dir / 'analysis.md').resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
