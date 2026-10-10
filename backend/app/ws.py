"""WebSocket 连接管理：维护管理员浏览器连接，并向所有连接广播客户端状态。"""
import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("log_fusion.ws")


class ConnectionManager:
    """集中管理所有 /ws/client-monitor 连接，提供线程安全的广播。"""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("心跳监控 WS 已连接：%s，当前连接数=%d",
                    websocket.client, len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("心跳监控 WS 已断开：%s，当前连接数=%d",
                    websocket.client, len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """给所有存活连接推送一条消息；失败连接自动清理，单连接发送超时 10 秒。

        心跳写入与后台巡检可能并发触发广播，这里用 _send_lock 串行化，
        避免同一 WebSocket 被两个协程同时发送导致帧错乱。
        """
        if not self._connections:
            return
        text = json.dumps(message, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        async with self._send_lock:
            async with self._lock:
                targets = list(self._connections)
            for websocket in targets:
                try:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(websocket.send_text(text), timeout=10)
                except (RuntimeError, WebSocketDisconnect, OSError) as exc:
                    logger.warning("WS 发送失败，移除连接：%s (%s)", websocket.client, exc)
                    dead.append(websocket)
        for websocket in dead:
            await self.disconnect(websocket)


# 全局单例：main.py 的 /ws/client-monitor 与心跳写入逻辑共用同一个实例
ws_manager = ConnectionManager()