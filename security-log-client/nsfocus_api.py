#!/usr/bin/env python3
"""绿盟 API：本地连接文件、事件查询、日志分页拉取及事件辅助接口（标准库）。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config/connection.json"
CATALOGS = {
    "type-tree": "/api/v1/events/typeTree",
    "all-types": "/api/v1/events/typesAll",
    "types": "/api/v2/events/types",
    "log-types": "/api/v1/events/logs",
}


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, config_path, timeout=30, interval=0.2):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(self.config, dict):
            raise ValueError("连接文件必须是 JSON 对象")
        self.base_url = str(self.config.get("base_url", "")).rstrip("/")
        url = urlsplit(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.path or url.query or url.fragment):
            raise ValueError("连接文件的 base_url 必须是纯 HTTPS 设备地址")
        if url.port is not None and url.port < 1:
            raise ValueError("设备端口无效")
        context = ssl.create_default_context(cafile=self.config.get("ca_cert"))
        if self.config.get("insecure") is True:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        self.timeout, self.interval = timeout, interval
        self.last_request = 0
        self.token = self.config.get("token")

    def _request(self, method, path, params=None, body=None, authenticated=True):
        delay = self.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Auth-Token"] = self.token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = self.base_url + path + ("?" + urlencode(params) if params else "")
        request = Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    status, raw = response.status, response.read()
            except HTTPError as exc:
                status, raw = exc.code, exc.read()
                exc.close()
        finally:
            self.last_request = time.monotonic()
        try:
            result = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            raise ApiError(f"HTTP {status}：{path} 未返回 UTF-8 JSON", status) from None
        if status != 200 or (isinstance(result, dict) and result.get("errCode", 0) not in (0, "0")):
            code = result.get("errCode", "未提供") if isinstance(result, dict) else "未提供"
            # 只输出状态与错误码，认证响应及 Token 不进入调试报告。
            raise ApiError(f"HTTP {status}：{path}，errCode={code}", status)
        if not isinstance(result, (dict, list)):
            raise ApiError(f"{path} 的 JSON 响应不是对象或数组", status)
        return result, {"http_status": status, "elapsed_seconds": round(time.monotonic() - started, 3)}

    def login(self):
        name, password = self.config.get("username"), self.config.get("password")
        if not isinstance(name, str) or not name or not isinstance(password, str) or not password:
            raise ValueError("连接文件缺少 username 或 password，无法登录/刷新 Token")
        result, metadata = self._request("POST", "/api/v2/auth", body={"name": name, "password": password}, authenticated=False)
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise ApiError("认证响应缺少有效 Token")
        self.token = token
        self.config.update(token=token, token_saved_at=datetime.now(timezone.utc).isoformat())
        save_json(self.config_path, self.config)
        return metadata

    def request(self, method, path, params=None, body=None):
        if not isinstance(self.token, str) or not self.token.strip():
            self.login()
        try:
            return self._request(method, path, params, body)
        except ApiError as exc:
            if exc.status != 401:
                raise
            self.login()
            return self._request(method, path, params, body)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="本地连接 JSON（含密码、Token）")
    parser.add_argument("--timeout", type=float, default=30, help="单次请求超时秒数")
    parser.add_argument("--interval", type=float, default=0.2, help="请求结束后的间隔秒数，最小 0.1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="重新登录，并保存最新 Token 到连接文件")
    for name in ("logs", "events", "related"):
        p = sub.add_parser(name, help={"logs": "3.4.1 用户日志查询", "events": "3.5.1 事件查询", "related": "3.5.5 事件关联日志"}[name])
        p.add_argument("--page", type=int, default=1)
        p.add_argument("--size", type=int, default=100, help="每页条数；设备上限未确认")
        p.add_argument("--pages", type=int, default=1, help="最多连续拉取页数，默认 1")
        p.add_argument("--output-dir", type=Path, help="新输出目录，已含 manifest.json 的目录不可复用")
        if name != "related":
            p.add_argument("--start-time", type=int, help="Unix 秒时间戳，需与 end-time 同时指定")
            p.add_argument("--end-time", type=int)
            p.add_argument("--last-hours", type=float, help="最近 N 小时，默认 24；运行开始固定时间范围")
        if name == "logs":
            p.add_argument("--type", required=True, dest="log_type")
            p.add_argument("--query-string", default="*")
        elif name == "events":
            p.add_argument("--condition-file", type=Path, help="conditionStr 的 JSON 数组文件；默认 []")
            p.add_argument("--no-time", action="store_true", help="不传时间参数，范围由设备决定")
        else:
            p.add_argument("--nsuids", required=True, help="真实事件的 eventNsuid")
    p = sub.add_parser("catalog", help="3.5.2 / 3.5.3 / 3.5.4 / 3.5.6 事件辅助目录")
    p.add_argument("name", choices=CATALOGS)
    p.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout 必须为有限正数")
    if not math.isfinite(args.interval) or args.interval < 0.1:
        parser.error("interval 必须至少为 0.1 秒")
    if args.command in ("logs", "events", "related"):
        if min(args.page, args.size, args.pages) < 1:
            parser.error("page、size、pages 必须是正整数")
    if args.command == "logs" and (not args.log_type.strip() or not args.query_string.strip()):
        parser.error("type 和 query-string 不能为空")
    if args.command in ("logs", "events"):
        explicit = args.start_time is not None or args.end_time is not None
        if explicit and (args.start_time is None or args.end_time is None):
            parser.error("start-time 与 end-time 需同时指定")
        if explicit and args.last_hours is not None:
            parser.error("last-hours 与显式时间范围不能同时使用")
        if getattr(args, "no_time", False):
            if explicit or args.last_hours is not None:
                parser.error("no-time 不能与时间范围同时使用")
        elif not explicit:
            hours = args.last_hours if args.last_hours is not None else 24
            if not math.isfinite(hours) or hours <= 0 or hours > time.time() / 3600:
                parser.error("last-hours 必须为有效正数，且不能早于 Unix 纪元")
            args.end_time = int(time.time())
            args.start_time = args.end_time - int(hours * 3600)
        if args.start_time is not None and not 0 <= args.start_time <= args.end_time:
            parser.error("时间戳必须非负，且开始不能晚于结束")
    return args


def fetch_pages(client, args, directory):
    query = {}
    if args.command in ("logs", "events") and args.start_time is not None:
        query.update(startTime=args.start_time, endTime=args.end_time)
    if args.command == "logs":
        method, path = "POST", "/api/audit/log/search"
        query.update(query_type="es", type=args.log_type, query_string=args.query_string)
    elif args.command == "events":
        method, path = "GET", "/api/v2/events/data/combinationQuery"
        conditions = json.loads(args.condition_file.read_text(encoding="utf-8-sig")) if args.condition_file else []
        if not isinstance(conditions, list) or any(not isinstance(c, dict) for c in conditions):
            raise ValueError("条件文件必须是 JSON 对象数组")
        query["conditionStr"] = json.dumps(conditions, ensure_ascii=False, separators=(",", ":"))
    else:
        method, path = "GET", "/api/v2/events/data/logList"
        query["nsuids"] = args.nsuids
    manifest = {"method": method, "path": path, "query": query, "start_page": args.page,
                "requested_pages": args.pages, "requested_size": args.size, "pages": [],
                "saved_records": 0, "status": "running"}
    save_json(directory / "manifest.json", manifest)
    previous_digest = None
    try:
        for page in range(args.page, args.page + args.pages):
            payload = {**query, "page": page, "size": args.size}
            result, metadata = client.request(method, path, **({"body": payload} if method == "POST" else {"params": payload}))
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ApiError("分页响应缺少 data 数组")
            rows = result["data"]
            filename = f"page_{page:06d}.json"
            save_json(directory / filename, result)
            if str(result.get("page", page)) != str(page) or str(result.get("size", args.size)) != str(args.size):
                raise ApiError("设备返回的 page/size 与请求不一致，已保存响应并停止，避免漏页")
            digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if rows and previous_digest == digest:
                raise ApiError("相邻两页内容完全相同，已停止，请核对设备分页行为")
            previous_digest = digest
            metadata.update(page=page, file=filename, records=len(rows), totalSize=result.get("totalSize"),
                            totalPage=result.get("totalPage"), partOfData=result.get("partOfData", False))
            manifest["pages"].append(metadata)
            manifest["saved_records"] += len(rows)
            save_json(directory / "manifest.json", manifest)
            print(json.dumps(metadata, ensure_ascii=False), flush=True)
            if result.get("partOfData"):
                raise ApiError("partOfData=true：已保存部分响应并停止，请缩小时间范围后重试")
            if not rows or len(rows) < args.size:
                total = result.get("totalSize")
                if isinstance(total, int) and total > (page - 1) * args.size + len(rows):
                    raise ApiError("本页不足请求条数，但 totalSize 表示仍有更多数据；已停止，请核对设备分页限制")
                manifest["status"] = "exhausted"
                break
        else:
            manifest["status"] = "page_limit_reached"
    except (Exception, KeyboardInterrupt) as exc:
        manifest.update(status="failed", error=str(exc) if not isinstance(exc, KeyboardInterrupt) else "用户中止")
        raise
    finally:
        save_json(directory / "manifest.json", manifest)
    return manifest


def main(argv=None):
    args = parse_args(argv)
    try:
        client = Client(args.config, args.timeout, args.interval)
        if args.command == "auth":
            metadata = client.login()
            print(json.dumps({**metadata, "message": "登录成功，Token 已保存", "config": str(args.config.resolve())}, ensure_ascii=False))
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        directory = args.output_dir or ROOT / "data/captures" / f"{args.command}_{stamp}"
        if (directory / "manifest.json").exists():
            raise ValueError("输出目录已有 manifest.json，请使用新目录以保留上次结果")
        directory.mkdir(parents=True, exist_ok=True)
        if args.command == "catalog":
            result, metadata = client.request("GET", CATALOGS[args.name])
            save_json(directory / f"{args.name}.json", result)
            save_json(directory / "manifest.json", {**metadata, "path": CATALOGS[args.name], "status": "completed"})
            print(json.dumps({**metadata, "records": len(result) if isinstance(result, list) else None}, ensure_ascii=False))
        else:
            manifest = fetch_pages(client, args, directory)
            print(f"已保存 {manifest['saved_records']} 条；状态：{manifest['status']}")
        print(f"输出目录：{directory.resolve()}")
        return 0
    except (ApiError, OSError, URLError, ValueError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中止，已完成的分页文件保留。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
