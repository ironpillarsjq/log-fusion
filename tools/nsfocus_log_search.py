#!/usr/bin/env python3
"""绿盟手册 §3.1 / §3.4.1：认证并查询一页用户日志（仅 Python 标准库）。

python tools/nsfocus_log_search.py --base-url https://192.0.2.10 --username admin --type windows_log
密码交互输入；也可设置 NSFOCUS_PASSWORD 或 NSFOCUS_TOKEN。
"""

import argparse
import getpass
import json
import math
import os
from pathlib import Path
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    """不向重定向目标转发账号或 Token。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(opener, url, payload, timeout, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
    if token:
        headers["Auth-Token"] = token
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        # 不打印认证响应原文，避免设备错误响应回显凭据。
        status = exc.code
        exc.close()
        raise RuntimeError(f"HTTP {status}：{url}，请核对地址、权限和认证信息") from None
    try:
        result = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise RuntimeError(f"{url} 返回的不是 UTF-8 JSON，请确认是 API 地址而非登录页面") from None
    if not isinstance(result, dict):
        raise RuntimeError(f"{url} 返回格式异常：预期 JSON 对象")
    if "errCode" in result and result["errCode"] not in (0, "0"):
        raise RuntimeError(f"接口业务失败：errCode={result['errCode']}；请按手册错误码排查")
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="设备 HTTPS 地址，如 https://192.0.2.10:443")
    parser.add_argument("--username", default=os.getenv("NSFOCUS_USERNAME"), help="登录用户名，也可用 NSFOCUS_USERNAME")
    parser.add_argument("--type", required=True, dest="log_type", help="设备上的日志类型；多个用逗号分隔，如 windows_log")
    parser.add_argument("--query-string", default="*", help="查询字符串，默认 *")
    parser.add_argument("--start-time", type=int, help="开始时间，Unix 秒时间戳（按手册示例）")
    parser.add_argument("--end-time", type=int, help="结束时间，Unix 秒时间戳（按手册示例）")
    parser.add_argument("--page", type=int, default=1, help="当前页，默认 1")
    parser.add_argument("--size", type=int, default=10, help="每页条数，默认 10；设备上限以实机为准")
    parser.add_argument("--timeout", type=float, default=30, help="单次请求超时秒数，默认 30")
    parser.add_argument("--output", type=Path, help="另存完整响应为 UTF-8 JSON 文件")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--ca-cert", help="用于验证设备证书的 CA PEM 文件")
    tls.add_argument("--insecure", action="store_true", help="显式跳过 HTTPS 证书验证（仅用于受控测试）")
    parser.add_argument("--dry-run", action="store_true", help="只预览查询请求，不登录、不联网")
    args = parser.parse_args(argv)
    try:
        url = urlsplit(args.base_url)
        port = url.port
        if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError
        if url.path not in ("", "/") or url.query or url.fragment or (port is not None and port < 1):
            raise ValueError
    except ValueError:
        parser.error("--base-url 必须是无账号、路径及查询参数的 HTTPS 设备地址")
    args.base_url = args.base_url.rstrip("/")
    if args.page < 1 or args.size < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("page、size 必须为正整数，timeout 必须为有限正数")
    if any(t is not None and t < 0 for t in (args.start_time, args.end_time)):
        parser.error("时间戳不能为负数")
    if args.start_time is not None and args.end_time is not None and args.start_time > args.end_time:
        parser.error("start-time 不能晚于 end-time")
    if not args.log_type.strip() or any(not part.strip() for part in args.log_type.split(",")):
        parser.error("--type 不能为空，多个日志类型用逗号分隔")
    if not args.query_string.strip():
        parser.error("--query-string 不能为空")
    return args


def main(argv=None):
    args = parse_args(argv)
    payload = {"query_type": "es", "type": args.log_type, "page": args.page,
               "size": args.size, "query_string": args.query_string}
    if args.start_time is not None:
        payload["startTime"] = args.start_time
    if args.end_time is not None:
        payload["endTime"] = args.end_time
    search_url = args.base_url + "/api/audit/log/search"
    if args.dry_run:
        print(json.dumps({"method": "POST", "url": search_url,
                          "headers": {"Auth-Token": "<认证后填入>", "Content-Type": "application/json; charset=utf-8"},
                          "body": payload}, ensure_ascii=False, indent=2))
        return 0
    try:
        context = ssl.create_default_context(cafile=args.ca_cert)
        if args.insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            print("提示：本次请求已跳过 HTTPS 证书验证。", file=sys.stderr)
        # 内网设备直连，避免被系统 HTTP_PROXY / HTTPS_PROXY 转发。
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        token = os.getenv("NSFOCUS_TOKEN")
        if not token:
            if not args.username:
                raise RuntimeError("请指定 --username / NSFOCUS_USERNAME，或设置 NSFOCUS_TOKEN")
            password = os.getenv("NSFOCUS_PASSWORD") or getpass.getpass("绿盟登录密码: ")
            if not password:
                raise RuntimeError("密码不能为空")
            auth = post_json(opener, args.base_url + "/api/v2/auth",
                             {"name": args.username, "password": password}, args.timeout)
            token = auth.get("token")
            if not isinstance(token, str) or not token.strip():
                raise RuntimeError("认证响应没有有效 token，请核对设备版本与账号")
        result = post_json(opener, search_url, payload, args.timeout, token=token)
        if "data" not in result:
            raise RuntimeError("查询响应缺少 data 字段，请核对设备版本和接口返回格式")
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(f"响应已保存到：{args.output.resolve()}", file=sys.stderr)
        if result.get("partOfData"):
            print("提示：partOfData=true，设备仅返回部分数据；请缩小查询时间范围。", file=sys.stderr)
        return 0
    except (RuntimeError, URLError, OSError, ValueError, EOFError) as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
