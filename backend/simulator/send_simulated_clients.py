"""模拟 Fluent Bit 客户端向 FastAPI 发送日志和心跳。

默认读取 C:\\Users\\ironp\\Desktop\\data 中的真实采集样例，发送格式保持与
客户端文件一致。启动后可在前端的“融合日志管理”和“客户端心跳监控”页面观察变化。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx


def read_samples(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        # Windows sample files can contain one JSON array larger than 50 MB.
        # Decode only the first objects so startup is fast and memory stays low.
        text = handle.read(2_000_000).lstrip()
        if text.startswith("["):
            decoder, pos = json.JSONDecoder(), 1
            while len(samples) < limit and pos < len(text):
                while pos < len(text) and text[pos] in " \r\n\t,":
                    pos += 1
                if pos >= len(text) or text[pos] == "]":
                    break
                try:
                    item, end = decoder.raw_decode(text, pos)
                except json.JSONDecodeError:
                    break
                if isinstance(item, dict):
                    samples.append(item)
                pos = end
        elif text:
            try:
                item = json.loads(text)
                samples.extend(item if isinstance(item, list) else [item])
            except json.JSONDecodeError:
                pass
    return samples


def send_logs(client: httpx.Client, url: str, platform: str, samples: list[dict[str, Any]], index: int) -> None:
    item = dict(samples[index % len(samples)])
    index += 1
    if platform == "linux":
        item["timestamp"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
        item["client_id"] = item.get("client_id") or "sim-linux-001"
    else:
        item["client_id"] = item.get("client_id") or "sim-windows-001"
    try:
        response = client.post(f"{url}/api/v1/logs/{platform}", json=[item], timeout=10)
        response.raise_for_status()
        print(f"[{platform}] sent 1 log ({response.status_code})", flush=True)
    except httpx.HTTPError as exc:
        print(f"[{platform}] send failed: {exc}", flush=True)
    return index


def send_heartbeat(client: httpx.Client, url: str, clients: list[str], interval: float) -> None:
    payload = [{"client_id": client_id, "server_name": client_id, "os_type": "Linux" if "linux" in client_id else "Windows", "heartbeat_interval_sec": int(interval)} for client_id in clients]
    try:
        response = client.post(f"{url}/api/v1/heartbeat", json=payload, timeout=10)
        response.raise_for_status()
        print(f"[heartbeat] sent {len(payload)} clients ({response.status_code})", flush=True)
    except httpx.HTTPError as exc:
        print(f"[heartbeat] send failed: {exc}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--data-dir", type=Path, default=Path(r"C:\Users\ironp\Desktop\data"))
    parser.add_argument("--interval", type=float, default=3.0, help="日志和心跳发送间隔（秒）")
    parser.add_argument("--count", type=int, default=0, help="发送轮数；0 表示持续运行")
    parser.add_argument("--clients", nargs="*", default=["sim-linux-001", "sim-windows-001"])
    args = parser.parse_args()
    linux = read_samples(args.data_dir / "linux" / "raw-logs.log")
    windows = read_samples(args.data_dir / "windows" / "raw-logs.log")
    if not linux or not windows:
        raise SystemExit("没有读取到 linux/raw-logs.log 或 windows/raw-logs.log 样例")
    print(f"loaded linux={len(linux)} windows={len(windows)}; target={args.url}", flush=True)
    linux_index = windows_index = 0
    # trust_env=False：忽略 HTTP_PROXY/HTTPS_PROXY 等环境代理。否则在开了
    # 系统代理的机器上，发往 127.0.0.1 的请求可能被送进代理并返回 502。
    with httpx.Client(trust_env=False) as client:
        round_no = 0
        while args.count == 0 or round_no < args.count:
            round_no += 1
            send_heartbeat(client, args.url, args.clients, args.interval)
            linux_index = send_logs(client, args.url, "linux", linux, linux_index)
            windows_index = send_logs(client, args.url, "windows", windows, windows_index)
            if args.count == 0 or round_no < args.count:
                time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("simulation stopped")
