import asyncio
import csv
import io
import json
import logging
import shutil
import subprocess
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request, Query, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler, http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import get_settings
from .db import Base, db_health, hash_engine, linux_engine, monitor_engine, windows_engine, MonitorSessionLocal, one, rows
from .models import ClientStatus, LogHash
from .services import categories, query_category, summary, recent_rows
from .metrics import category_event_stats, heartbeat_history, logs_stats, logs_trend, record_sample
from .parsers import normalize_linux, normalize_windows, windows_table_for
from .hashing import build_evidence_row
from .archive import archive_evidence, archive_root, months_ago
from .ws import ws_manager
from .security_logs import CATALOG as SECURITY_CATALOG, ROOT as SECURITY_ROOT, SecurityStore, SecurityWorker

logger = logging.getLogger("log_fusion")

# Fluent Bit 1.8 的 HTTP 客户端响应缓冲区默认为 4 KiB。采集端一次
# flush 可能包含很多条失败记录，若逐条把 300 字符的 reason 返回，响应
# 会膨胀到几十 KiB，导致 Fluent Bit 连 HTTP 状态码都读不完并反复重试。
_INGEST_ERROR_SAMPLE_LIMIT = 8
_INGEST_ERROR_REASON_LIMIT = 160


def _compact_ingest_errors(errors: list[dict]) -> tuple[list[dict], int, bool]:
    """限制采集接口响应大小，保留少量样例并返回完整错误总数。"""
    total = len(errors)
    if total == 0:
        return [], 0, False
    sample = []
    for error in errors[:_INGEST_ERROR_SAMPLE_LIMIT]:
        item = dict(error)
        if "reason" in item and item["reason"] is not None:
            item["reason"] = str(item["reason"])[:_INGEST_ERROR_REASON_LIMIT]
        sample.append(item)
    return sample, total, total > len(sample)

settings = get_settings()
security_store = SecurityStore(monitor_engine, settings.security_source_instance)
app = FastAPI(title=settings.app_name, version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


def _log_rejected_ingest(request: Request, status: int, reason: Any) -> None:
    if request.method == "POST" and request.url.path.startswith("/api/v1/logs/"):
        logger.error(
            "日志接收失败 path=%s client=%s status=%s reason=%s content_type=%s content_length=%s",
            request.url.path, request.client.host if request.client else "unknown",
            status, json.dumps(reason, ensure_ascii=False, default=str),
            request.headers.get("content-type"), request.headers.get("content-length"),
        )


@app.exception_handler(RequestValidationError)
async def _ingest_validation_error(request: Request, exc: RequestValidationError):
    # 仅打印错误位置和原因，避免将完整请求原文输出到控制台。
    reasons = [{"loc": error.get("loc"), "type": error.get("type"), "msg": error.get("msg")}
               for error in exc.errors()]
    _log_rejected_ingest(request, 422, reasons)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _ingest_http_error(request: Request, exc: StarletteHTTPException):
    _log_rejected_ingest(request, exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def _cache_policy(request: Request, call_next):
    """响应缓存策略。

    - `/static/vendor/*`：自托管的版本化第三方库，允许长期缓存；
    - 其他 `/static/*`：应用自身资源（`app.js` 等）禁用缓存，避免用到旧版本；
    - HTML 页面：禁用缓存，避免浏览器继续使用引用了旧依赖列表的旧页面。
    """
    response = await call_next(request)
    path = request.url.path
    content_type = response.headers.get("content-type", "")
    if path.startswith("/static/vendor/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif content_type.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


def _ensure_tables() -> None:
    try:
        Base.metadata.create_all(monitor_engine)
    except Exception:
        logger.exception("平台表建表失败（请确认 MySQL 中存在 log_fusion 库）")
    try:
        Base.metadata.create_all(hash_engine, tables=[LogHash.__table__])
    except Exception:
        logger.exception("log_hash 建表失败（请确认 MySQL 中存在 log_hash 库）")


# 自动归档每天跑一次的间隔（秒）；启动后先等一小段再跑第一次，避免和建表抢时间
ARCHIVE_INTERVAL_SECONDS = 24 * 60 * 60
ARCHIVE_STARTUP_DELAY_SECONDS = 60


async def _auto_archive_loop() -> None:
    """后台自动归档：把超过保留期（默认 3 个日历月）的存证导出后删除。

    只按 `created_at` 判断（入库时间），不用 `event_time`——否则客户端补传的
    历史日志会刚入库就被归档掉。导出成功并校验落盘后才删除，任何失败都不删。
    """
    await asyncio.sleep(ARCHIVE_STARTUP_DELAY_SECONDS)
    while True:
        months = settings.archive_retention_months
        if months > 0:
            cutoff = months_ago(months)
            try:
                info = await asyncio.to_thread(
                    archive_evidence, mode="auto", before=cutoff
                )
                if info.get("deleted"):
                    logger.info(
                        "自动归档完成：删除 %s 条（早于 %s），文件 %s",
                        info["deleted"], cutoff, info.get("file"),
                    )
            except Exception:
                logger.exception("自动归档失败，本次不删除任何记录")
        await asyncio.sleep(ARCHIVE_INTERVAL_SECONDS)


async def _security_sync_loop():
    config = Path(settings.security_config_file)
    if not config.is_absolute():
        config = SECURITY_ROOT / config
    worker = SecurityWorker(security_store, config, page_size=settings.security_page_size,
                            poll_seconds=settings.security_poll_seconds,
                            overlap=settings.security_overlap_seconds,
                            lag=settings.security_lag_seconds, timeout=settings.security_timeout_seconds,
                            live_only=True, live_window=settings.security_live_window_seconds)
    initialized = False
    while True:
        step = None
        try:
            if not initialized:
                initialized = await asyncio.to_thread(security_store.initialize_live,
                                                     overlap=settings.security_overlap_seconds,
                                                     lag=settings.security_lag_seconds)
                if not initialized:
                    await asyncio.sleep(1)
                    continue
            step = asyncio.create_task(asyncio.to_thread(worker.step))
            event = await asyncio.shield(step)
            if event:
                await ws_manager.broadcast(event)
        except asyncio.CancelledError:
            # Let the bounded in-flight request/transaction finish before releasing the DB lock.
            if step:
                with suppress(Exception):
                    await step
            raise
        except Exception:
            logger.error("安全日志采集不可用；请检查同步表、配置和网络，稍后重试")
            await asyncio.sleep(30)
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_tables()
    watchdog = asyncio.create_task(_status_watchdog())
    archiver = asyncio.create_task(_auto_archive_loop())
    tasks = [watchdog, archiver]
    if settings.security_sync_enabled:
        tasks.append(asyncio.create_task(_security_sync_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app.router.lifespan_context = lifespan


def _security_kind(kind: str):
    if kind not in SECURITY_CATALOG:
        raise HTTPException(422, "未知安全日志类型")
    return kind


@app.get("/api/v1/security/summary")
def security_summary():
    try:
        result = security_store.summary()
        result.update(enabled=settings.security_sync_enabled, pollSeconds=settings.security_poll_seconds,
                      overlapSeconds=settings.security_overlap_seconds, lagSeconds=settings.security_lag_seconds,
                      collectionMode="live_only", liveWindowSeconds=settings.security_live_window_seconds)
        return {"code": 0, "message": "ok", "data": result}
    except Exception:
        raise HTTPException(503, "安全日志数据库或同步表不可用") from None


@app.get("/api/v1/security/logs/{log_type}")
def security_logs(log_type: str, before: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=100)):
    _security_kind(log_type)
    try:
        return {"code": 0, "message": "ok", "data": security_store.logs(log_type, before, limit)}
    except Exception:
        raise HTTPException(503, "安全日志数据表不可用") from None


@app.get("/api/v1/security/logs/{log_type}/{record_id}")
def security_log_detail(log_type: str, record_id: int):
    _security_kind(log_type)
    if record_id < 1:
        raise HTTPException(422, "记录 ID 必须为正数")
    try:
        record = security_store.detail(log_type, record_id)
    except Exception:
        raise HTTPException(503, "安全日志数据表不可用") from None
    if record is None:
        raise HTTPException(404, "日志不存在")
    return {"code": 0, "message": "ok", "data": record}

ONLINE_MAX_AGE = 10
DELAYED_MAX_AGE = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _assess_status(age_seconds: float) -> str:
    if age_seconds <= ONLINE_MAX_AGE:
        return "ONLINE"
    if age_seconds <= DELAYED_MAX_AGE:
        return "DELAYED"
    return "OFFLINE"


def _client_payload(client: ClientStatus, now: datetime) -> dict:
    age = max(0.0, (now - client.last_heartbeat).total_seconds())
    return {
        "client_id": client.client_id,
        "hostname": client.hostname,
        "ip": client.ip,
        "system": client.system,
        "latency": client.latency,
        "status": _assess_status(age),
        "last_heartbeat": client.last_heartbeat.isoformat(),
        "heartbeatDelaySec": int(age),
    }


def build_snapshot(db: Session) -> dict:
    now = _utcnow()
    clients = [_client_payload(c, now) for c in db.execute(select(ClientStatus)).scalars().all()]
    counts = {"ONLINE": 0, "DELAYED": 0, "OFFLINE": 0}
    for client in clients:
        counts[client["status"]] += 1
    return {
        "total_clients": len(clients),
        "online_clients": counts["ONLINE"],
        "delayed_clients": counts["DELAYED"],
        "offline_clients": counts["OFFLINE"],
        "clients": clients,
    }


def _snapshot_sync() -> dict:
    with MonitorSessionLocal() as db:
        return build_snapshot(db)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # The UI is server-rendered with Jinja2; Alpine/HTMX enhance interactions
    # without turning the application into a separate SPA frontend.
    try:
        dashboard = summary()
    except Exception:
        dashboard = {"total": 0, "by_platform": {"Linux": 0, "Windows": 0}, "categories": []}
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"summary": dashboard},
    )


# 侧栏「服务连接」卡片里的库名（库名给运维看，中文名给业务看）
DB_STATUS_LABELS = (
    ("linux_logs", "Linux 日志库"),
    ("windows_logs", "Windows 日志库"),
    ("log_fusion", "融合库"),
    ("log_hash", "存证库"),
)


@app.get("/partials/db-status", response_class=HTMLResponse)
def db_status_partial():
    """侧栏「服务连接」卡片的数据库部分（HTMX 每 30 秒替换一次）。

    每个库一行：状态点 + 中文名 + 真实库名 + 状态文字，首行是「N/M 个库」汇总。
    状态只来自本次真实 `SELECT 1`：连不上就直接显示「不可用」，不保留上次结果——
    否则运维会把「已经断了」看成「一切正常」。连接失败不返回 5xx，只改变色与文案。
    """
    status = db_health()
    total = len(DB_STATUS_LABELS)
    ok = sum(1 for name, _label in DB_STATUS_LABELS if status.get(name))
    group_tone = "ok" if ok == total else ("warn" if ok else "bad")
    group_text = "正常" if ok == total else ("部分可用" if ok else "全部不可用")
    parts = [
        f'<div class="conn-row"><span class="conn-dot {group_tone}"></span>'
        f'<span class="conn-name">数据库</span><span class="conn-meta">{ok}/{total} 个库</span>'
        f'<span class="conn-state {group_tone}">{group_text}</span></div>'
    ]
    for name, label in DB_STATUS_LABELS:
        connected = bool(status.get(name))
        tone = "ok" if connected else "bad"
        # 真实库名放进 title（悬停可见）：侧栏只有 col-lg-2 宽，把 linux_logs 这类
        # 12 个字符的等宽文本塞进每一行会把中文标签挤成省略号。
        parts.append(
            f'<div class="conn-row conn-sub" title="{label}（{name}）">'
            f'<span class="conn-dot {tone}"></span>'
            f'<span class="conn-name">{label}</span>'
            f'<span class="conn-state {tone}">{"已连接" if connected else "不可用"}</span></div>'
        )
    return "".join(parts)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "databases": db_health(),
        # 实时通道（仅 WebSocket）的连接数：浏览器对同一地址只允许 6 条 HTTP/1.1 连接，
        # 长连接不释放会让后续 REST 请求排队，这里用于排查连接被占满。
        "realtime": {
            "ws_clients": ws_manager.connection_count,
        },
    }


@app.get("/api/v1/logs/categories")
def log_categories():
    return {"code": 0, "message": "ok", "data": categories()}


@app.get("/api/v1/logs/summary")
def log_summary():
    return {"code": 0, "message": "ok", "data": summary()}


@app.get("/api/v1/logs/categories/{category}")
def category_logs(category: str, platform: str = Query("Linux"), page: int = 1, size: int = 20,
                  keyword: str | None = None, full: bool = False):
    """分类分页查询。

    `full=true` 返回整行真实字段（含 Windows 的 `eventdata`），供「分类日志视图」的
    原始详情弹窗使用；默认只返回列表展示列，保持原有响应行为不变。
    """
    try:
        return {"code": 0, "message": "ok", "data": query_category(category, platform, page, size, keyword, full=full)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/logs/categories/{category}/event-stats")
def category_event_stats_api(category: str, platform: str = Query("Linux"), limit: int = Query(10, ge=1, le=10)):
    """分类日志的事件维度统计（饼图 + 横向柱状图）。

    Windows 按 `event_id` 分组；Linux 的 `event_id` 是审计流水号，改按 `type` 分组
    （原因见 `metrics._event_group_expression`）。`limit` 固定上限 10。
    """
    try:
        return {"code": 0, "message": "ok", "data": category_event_stats(category, platform, limit)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/logs/recent")
def recent_logs(limit: int = Query(20, ge=1, le=100)):
    return {"code": 0, "message": "ok", "data": recent_rows(limit)}


@app.get("/api/v1/logs/trend")
def log_trend(range_: str = Query("24h", alias="range"), bucket: str | None = None, platform: str | None = None):
    """时间桶接入趋势（图表专用）。

    桶键一律是 UTC（`...Z`）——DB 内即 UTC naive datetime，本接口不做时区转换，
    本地时间由前端渲染时转换，否则趋势图会偏 8 小时。服务端带 TTL 缓存
    （响应里的 `cached`/`ageMs`/`generatedAt` 可观测数据新鲜度）。
    """
    try:
        return {"code": 0, "message": "ok", "data": logs_trend(range_, bucket, platform)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/logs/stats")
def log_stats(range_: str = Query("7d", alias="range"), limit: int = Query(10, ge=1, le=50), platform: str | None = None):
    """窗口内 TOP 日志类别 / TOP 主机（排行图专用，服务端 TTL 缓存 60 秒）。"""
    try:
        return {"code": 0, "message": "ok", "data": logs_stats(range_, limit, platform)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/monitor/summary")
def monitor_summary():
    snapshot = _snapshot_sync()
    total = snapshot["total_clients"]
    states = {"ONLINE": snapshot["online_clients"], "DELAYED": snapshot["delayed_clients"], "OFFLINE": snapshot["offline_clients"]}
    return {"code": 0, "message": "ok", "data": {"total": total, "states": states, "onlineRate": round(states["ONLINE"] / total * 100, 2) if total else 0}}


@app.get("/api/v1/monitor/clients")
def monitor_clients(keyword: str | None = None, os_type: str | None = None):
    items = []
    for c in _snapshot_sync()["clients"]:
        if os_type and c["system"] != os_type:
            continue
        if keyword and keyword.lower() not in json.dumps(c, ensure_ascii=False).lower():
            continue
        items.append({"clientId": c["client_id"], "serverName": c["hostname"], "ipAddress": c["ip"],
                      "os_type": c["system"], "lastHeartbeatAt": c["last_heartbeat"],
                      "heartbeatDelaySec": c["heartbeatDelaySec"], "heartbeatStatus": c["status"]})
    return {"code": 0, "message": "ok", "data": {"items": items, "total": len(items)}}


@app.get("/api/v1/monitor/clients/{client_id}")
def monitor_client(client_id: str):
    with MonitorSessionLocal() as db:
        client = db.execute(select(ClientStatus).where(ClientStatus.client_id == client_id)).scalar_one_or_none()
        if not client:
            raise HTTPException(404, "client not found")
        return {"code": 0, "message": "ok", "data": _client_payload(client, _utcnow())}


@app.get("/api/v1/monitor/clients/{client_id}/logs")
def monitor_client_logs(client_id: str, limit: int = Query(20, ge=1, le=100)):
    # Existing legacy tables do not consistently carry client_id; return the
    # common recent view and let future migrations add exact source filtering.
    return {"code": 0, "message": "ok", "data": {"clientId": client_id, "items": recent_rows(limit)}}


@app.get("/api/v1/monitor/history")
def monitor_history(minutes: int = Query(15, ge=1, le=60)):
    """心跳历史序列：进程内环形缓冲（watchdog 每 5 秒采样），不查库。

    进程重启后历史清零，返回空序列而不是错误——页面用空态区分
    「刚重启还没有历史」和「查询失败」。
    """
    try:
        return {"code": 0, "message": "ok", "data": heartbeat_history(minutes)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def persist_heartbeats(payload: list[dict[str, Any]]) -> dict:
    now = _utcnow()
    with MonitorSessionLocal() as db:
        for item in payload:
            client_id = str(item.get("client_id") or "UNKNOWN_HOST")
            values = {
                "client_id": client_id,
                "hostname": str(item.get("hostname") or item.get("server_name") or client_id),
                "ip": str(item.get("ip") or item.get("ip_address") or ""),
                "system": str(item.get("system") or item.get("os_type") or "Unknown"),
                "last_heartbeat": now,
                "latency": int(item.get("latency") or 0),
                "status": "ONLINE",
            }
            stmt = mysql_insert(ClientStatus).values(**values)
            db.execute(stmt.on_duplicate_key_update(
                hostname=stmt.inserted.hostname,
                ip=stmt.inserted.ip,
                system=stmt.inserted.system,
                last_heartbeat=stmt.inserted.last_heartbeat,
                latency=stmt.inserted.latency,
                status=stmt.inserted.status,
                updated_at=func.now(),
            ))
        db.commit()
        return build_snapshot(db)


@app.post("/api/v1/heartbeat")
async def heartbeat(payload: list[dict[str, Any]]):
    try:
        snapshot = await asyncio.to_thread(persist_heartbeats, payload)
    except Exception as exc:
        logger.exception("心跳写入失败：%s", exc)
        raise HTTPException(503, "heartbeat persist failed") from exc
    await ws_manager.broadcast(snapshot)
    return "pong"


@app.websocket("/ws/client-monitor")
async def ws_client_monitor(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        snapshot = await asyncio.to_thread(_snapshot_sync)
        await websocket.send_text(json.dumps(snapshot, ensure_ascii=False, default=str))
        while True:
            await websocket.receive_text()   # 仅用于检测断开；客户端可不发消息
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("心跳监控 WS 异常")
    finally:
        await ws_manager.disconnect(websocket)


async def _status_watchdog(interval: float = 5.0) -> None:
    """每 interval 秒重算状态并推送一次快照。

    这里刻意“每轮都推”而不是“仅变化时推”：
    1) 充当应用层保活，避免代理/网关静默断开空闲连接；
    2) 浏览器端据此判断“超过 N 秒没收到消息 = 连接假死”，从而强制重连；
    3) 即便状态未变化，也保证界面数据始终新鲜。
    """
    while True:
        await asyncio.sleep(interval)
        try:
            def _refresh() -> dict:
                with MonitorSessionLocal() as db:
                    now = _utcnow()
                    changed = False
                    for client in db.execute(select(ClientStatus)).scalars().all():
                        new_status = _assess_status((now - client.last_heartbeat).total_seconds())
                        if client.status != new_status:
                            client.status = new_status
                            changed = True
                    if changed:
                        db.commit()
                    return build_snapshot(db)

            snapshot = await asyncio.to_thread(_refresh)
            # 采样进心跳历史环形缓冲（/api/v1/monitor/history 的数据源，不查库）
            record_sample(snapshot)
            await ws_manager.broadcast(snapshot)
        except Exception:
            logger.exception("心跳状态巡检失败")


def _insert_row(conn, table: str, row: dict) -> None:
    names = ", ".join(f"`{name}`" for name in row)
    placeholders = ", ".join(f":{name}" for name in row)
    conn.execute(text(f"INSERT INTO `{table}` ({names}) VALUES ({placeholders})"), row)


def _persist_rows(engine, entries: list[tuple[int, str, dict]]) -> tuple[int, list[dict]]:
    """写入解析后的行：先走整批事务，失败再逐行重试以定位到具体记录。"""
    if not entries:
        return 0, []
    try:
        with engine.begin() as conn:
            for _, table, row in entries:
                _insert_row(conn, table, row)
        return len(entries), []
    except Exception:
        logger.warning("批量入库失败，改为逐行定位（共 %d 行）", len(entries), exc_info=True)

    inserted, errors = 0, []
    for index, table, row in entries:
        try:
            with engine.begin() as conn:
                _insert_row(conn, table, row)
            inserted += 1
        except Exception as exc:
            errors.append({
                "index": index, "stage": "insert", "table": table,
                "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
            })
    return inserted, errors


def _is_duplicate_key(exc: Exception) -> bool:
    """判断 IntegrityError 是否为「唯一键冲突」（MySQL 1062）。

    MySQL 下所有完整性错误都表现为 `IntegrityError`，只有 errno 1062 才代表
    「这条记录已经存在」。若不加区分地当成重复，NOT NULL 违反等真实错误会被
    静默计为存证成功。
    """
    orig = getattr(exc, "orig", None)
    if orig is not None and getattr(orig, "args", None):
        try:
            return int(orig.args[0]) == 1062
        except (TypeError, ValueError):
            pass
    # 兜底：驱动未提供 errno 时按错误文本判断
    return "Duplicate entry" in str(exc)


def _persist_evidence(platform: str, records: list, event_times: dict[int, Any]) -> tuple[int, list[dict]]:
    """为每条采集记录写原始日志存证（log_hash 库）。

    - `log` 缺失/为空 → 记为 `stage="hash"`，**不影响业务表入库**；
    - 存证库写入失败 → 整批降级为逐行重试定位，仍然只影响存证计数；
    - `log_hash` 唯一索引命中（同一 timestamp+log 重复上报）按「已存证」处理，不计失败。
    """
    prepared: list[tuple[int, dict]] = []
    errors: list[dict] = []
    for index, record in enumerate(records):
        row, error = build_evidence_row(platform, record, event_times.get(index))
        if row is None:
            errors.append({"index": index, "stage": "hash", "reason": error})
            continue
        prepared.append((index, row))
    if not prepared:
        return 0, errors

    def insert_many(items: list[tuple[int, dict]]) -> tuple[int, list[dict]]:
        stored, failed = 0, []
        try:
            with hash_engine.begin() as conn:
                for _, row in items:
                    _insert_row(conn, settings.log_hash_table, row)
            return len(items), []
        except Exception:
            logger.warning("存证批量写入失败，改为逐行定位（共 %d 行）", len(items), exc_info=True)
        for index, row in items:
            try:
                with hash_engine.begin() as conn:
                    _insert_row(conn, settings.log_hash_table, row)
                stored += 1
            except IntegrityError as exc:
                # 只有**唯一键冲突**才是「同一条日志已存证过」（Fluent Bit 重试），才算幂等成功。
                # 其他完整性错误（NOT NULL 违反等）必须如实上报：把它们当成成功会让
                # 存证静默丢失，而接口却报 hashed 正常——正是本文件反复强调要避免的
                # 「故障被伪装成成功」。
                if _is_duplicate_key(exc):
                    stored += 1
                else:
                    failed.append({
                        "index": index, "stage": "hash",
                        "reason": f"完整性约束失败（非重复上报）：{type(exc).__name__}: {str(exc)[:300]}",
                    })
            except Exception as exc:
                failed.append({
                    "index": index, "stage": "hash",
                    "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                })
        return stored, failed

    try:
        stored, failed = insert_many(prepared)
    except Exception as exc:
        return 0, errors + [{"stage": "hash", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}]
    return stored, errors + failed


def persist_logs(platform: str, records: list) -> dict:
    """解析 + 入库 + 存证。解析失败与入库失败分开记录（errors[].stage 为 parse / insert / hash）。"""
    normalize = normalize_windows if platform == "windows" else normalize_linux
    engine = windows_engine if platform == "windows" else linux_engine
    prepared: list[tuple[int, str, dict]] = []
    parse_errors: list[dict] = []
    event_times: dict[int, Any] = {}
    for index, record in enumerate(records):
        row, error = normalize(record)
        if row is None:
            parse_errors.append({"index": index, "stage": "parse", "reason": error})
            continue
        table = row["category"] if platform == "linux" else windows_table_for(row.get("channel"))
        prepared.append((index, table, row))
        event_times[index] = row.get("event_time") or row.get("time_created")
    inserted, insert_errors = _persist_rows(engine, prepared)
    # 存证独立于业务入库：先算哈希存证，再返回结果；存证失败不回滚已入库的日志。
    hashed, hash_errors = _persist_evidence(platform, records, event_times)
    all_errors = sorted(parse_errors + insert_errors + hash_errors,
                        key=lambda item: item.get("index", -1))
    if all_errors:
        logger.error(
            "日志处理异常 platform=%s accepted=%d inserted=%d skipped=%d failed=%d hashed=%d errors=%d",
            platform, len(records), inserted, len(parse_errors), len(insert_errors), hashed, len(all_errors),
        )
        for error in all_errors:
            index = error.get("index")
            record = records[index] if isinstance(index, int) and 0 <= index < len(records) else None
            logger.error(
                "日志记录处理失败 platform=%s index=%s client_id=%s stage=%s table=%s reason=%s",
                platform, index, record.get("client_id") if isinstance(record, dict) else None,
                error.get("stage"), error.get("table"),
                json.dumps(error.get("reason"), ensure_ascii=False, default=str),
            )
    return {
        "inserted": inserted,
        "skipped": len(parse_errors),
        "failed": len(insert_errors),
        "hashed": hashed,
        "errors": all_errors,
    }


@app.post("/api/v1/logs/{platform}")
async def ingest_logs(platform: str, payload: Any = Body(...)):
    """接收采集日志：解析 → 入库 → 推送实时事件。

    始终返回 accepted / persisted，并额外返回 inserted / skipped / failed / errors，
    因此能区分“解析失败”（errors[].stage=parse）和“入库失败”（errors[].stage=insert）。
    """
    platform = platform.lower()
    if platform not in {"linux", "windows"}:
        raise HTTPException(404, "unsupported platform")
    records = payload if isinstance(payload, list) else [payload]
    result: dict | None = None
    fatal: str | None = None
    try:
        result = await asyncio.wait_for(asyncio.to_thread(persist_logs, platform, records), timeout=8)
    except Exception as exc:
        fatal = f"{type(exc).__name__}: {exc}"
        logger.exception("日志处理失败（%s）", platform)

    if result is None:
        # 整体失败（连接超时/数据库不可用）：如实返回失败结果。
        return JSONResponse(status_code=200, content={
            "accepted": len(records), "persisted": False, "inserted": 0, "skipped": 0,
            "failed": len(records), "hashed": 0,
            "errors": [{"stage": "persist", "reason": (fatal or "")[:300]}],
        })

    errors, error_count, errors_truncated = _compact_ingest_errors(result["errors"])
    response = {
        "accepted": len(records),
        "persisted": result["failed"] == 0 and result["skipped"] == 0,
        "inserted": result["inserted"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "hashed": result["hashed"],
        "errors": errors,
    }
    if error_count:
        response["error_count"] = error_count
        response["errors_truncated"] = errors_truncated
    return JSONResponse(status_code=200, content=response)


@app.get("/api/v1/logs/export")
def export_logs(platform: str = "Linux", category: str = "authentication_session", size: int = Query(1000, ge=1, le=10000)):
    # 显式放宽单次上限：分类列表接口上限是 100，导出需要真的能取到 size 条。
    result = query_category(category, platform, 1, size, size_cap=10000)
    output = io.StringIO()
    if result["items"]:
        writer = csv.DictWriter(output, fieldnames=list(result["items"][0]), extrasaction="ignore")
        writer.writeheader(); writer.writerows(result["items"])
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{category}.csv"'})


EVIDENCE_COLUMNS = ("id", "log_timestamp", "platform", "source", "client_id", "event_time", "created_at", "log_hash")

EVIDENCE_STATUSES = ("VALID", "HASH_MISMATCH", "RAW_MISSING")

# 逐条校验分类，由 MySQL 自己复算摘要，**必须与写入端 `hashing.compute_log_hash` 完全同规则**：
#   payload = raw_text(log_timestamp) + 0x1F + raw_text(raw_log)
# 其中 `raw_text(None)` 等于空串，所以这里用 `IFNULL(log_timestamp,'')` 而不是把
# 「时间戳为 NULL」当成无法校验——否则会对「采集端没带 timestamp」的合法存证（写入时
# 就是按空串算的）误报，制造假警报。比较用 `<=>` 做 NULL 安全等值判断。
# 之所以放在 SQL 里：`raw_log` 是 LONGTEXT，拉到 Python 再算会把整表原文读进内存。
#   RAW_MISSING   —— 原文为空，无从复算
#   VALID         —— 复算结果与存证摘要一致
#   HASH_MISMATCH —— 复算结果与存证摘要不一致（原文 / 时间戳 / 摘要被改动）
EVIDENCE_VERIFY_SQL = """CASE
        WHEN raw_log IS NULL OR TRIM(raw_log) = '' THEN 'RAW_MISSING'
        WHEN SHA2(CONCAT(IFNULL(log_timestamp, ''), CHAR(31), raw_log), 256) <=> log_hash THEN 'VALID'
        ELSE 'HASH_MISMATCH'
    END"""


def _evidence_where(
    platform: str | None,
    keyword: str | None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[str, dict]:
    conditions, params = [], {}
    if platform:
        conditions.append("platform = :platform")
        params["platform"] = platform
    if keyword:
        conditions.append("(raw_log LIKE :keyword OR client_id LIKE :keyword OR source LIKE :keyword OR log_hash LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    # 时间范围按入库时间 created_at 过滤（与归档口径一致）：采集端补传的历史日志
    # 其 event_time 可能很早，用 created_at 才能回答「这段时间系统存了什么」。
    start_at = _parse_date(start)
    if start_at is not None:
        conditions.append("created_at >= :start_at")
        params["start_at"] = start_at
    end_at = _parse_date(end, end_of_day=True)
    if end_at is not None:
        conditions.append("created_at <= :end_at")
        params["end_at"] = end_at
    return (" WHERE " + " AND ".join(conditions) if conditions else ""), params


def _evidence_table() -> str:
    return f"`{settings.log_hash_db}`.`{settings.log_hash_table}`"


def _evidence_source() -> dict:
    """当前存证数据来源，随响应返回。

    排查「页面没数据」时最常见的误判是「看的不是同一个库」。把实际连接目标
    一并返回，就不用靠猜（不含密码）。
    """
    return {"host": settings.db_host, "database": settings.log_hash_db, "table": settings.log_hash_table}


@app.get("/api/v1/evidence/summary")
def evidence_summary():
    """存证摘要：一次扫描同时给出总数与四类校验结果。

    查询失败时返回 **503** 而不是 0：把数据库故障降级成「0 条存证」会让
    「库连不上」和「确实没有存证」无法区分，操作者会误以为数据被清空了。

    注意：本接口对整表复算 SHA-256（单次全表扫描）。当前万级规模在百毫秒内
    完成；增长到百万级时应改为增量校验或异步任务（见 `docs/TODO.md`）。
    """
    table = _evidence_table()
    try:
        counted = rows(
            hash_engine,
            f"SELECT {EVIDENCE_VERIFY_SQL} AS verify_status, COUNT(*) AS n FROM {table} GROUP BY verify_status",
        )
    except Exception as exc:
        logger.exception("存证摘要查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    found = {item["verify_status"]: int(item["n"]) for item in counted}
    statuses = {name: found.get(name, 0) for name in EVIDENCE_STATUSES}
    total = sum(statuses.values())
    return {"code": 0, "message": "ok", "data": {
        "total": total,
        "statuses": statuses,
        "healthy": statuses["HASH_MISMATCH"] == 0 and statuses["RAW_MISSING"] == 0,
        "source": _evidence_source(),
    }}


@app.get("/api/v1/evidence")
def evidence_list(
    page: int = 1,
    size: int = Query(30, ge=1, le=100),
    platform: str | None = None,
    keyword: str | None = None,
    verify: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    """存证分页列表：数据库侧 LIMIT/OFFSET 分页，并逐条给出校验结果。

    `verify` 可传 `VALID`/`HASH_MISMATCH`/`RAW_MISSING` 只看某一类，用来直接
    筛出被改动的记录；`start`/`end` 为 `YYYY-MM-DD`，按入库时间 `created_at`
    过滤（end 含当天 23:59:59）。
    """
    page = max(1, page)
    where, params = _evidence_where(platform, keyword, start, end)
    table = _evidence_table()

    verify_filter = None
    if verify:
        verify_filter = verify.strip().upper()
        if verify_filter not in EVIDENCE_STATUSES:
            raise HTTPException(400, f"verify 只能是 {'/'.join(EVIDENCE_STATUSES)}")

    inner = (
        f"SELECT `id`, `log_timestamp`, `platform`, `source`, `client_id`, `event_time`, "
        f"`created_at`, `log_hash`, {EVIDENCE_VERIFY_SQL} AS verify_status FROM {table}{where}"
    )
    outer, outer_params = "", dict(params)
    if verify_filter:
        outer = " WHERE verify_status = :verify"
        outer_params["verify"] = verify_filter

    try:
        total = one(hash_engine, f"SELECT COUNT(*) AS n FROM ({inner}) t{outer}", outer_params)["n"]
        items = rows(
            hash_engine,
            f"SELECT * FROM ({inner}) t{outer} ORDER BY id DESC LIMIT :limit OFFSET :offset",
            dict(outer_params, limit=size, offset=(page - 1) * size),
        )
    except Exception as exc:
        logger.exception("存证列表查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    return {"code": 0, "message": "ok", "data": {
        "items": items, "page": page, "size": size, "total": total,
        "pages": (total + size - 1) // size if size else 0,
        "verify": verify_filter,
        "source": _evidence_source(),
    }}


@app.get("/api/v1/evidence/export")
def evidence_export(
    platform: str | None = None,
    keyword: str | None = None,
    verify: str | None = None,
    start: str | None = None,
    end: str | None = None,
    size: int = Query(10000, ge=1, le=100000),
):
    """存证 CSV 导出（只读，不删除）。删除请走 /api/v1/archive/manual。

    含逐条校验结果 `verify_status`：导出后可在 Excel 里直接筛出被改动的记录。
    筛选参数与 §7.2 列表一致（含入库时间范围 `start`/`end`）。
    """
    where, params = _evidence_where(platform, keyword, start, end)
    table = _evidence_table()
    verify_filter = None
    if verify:
        verify_filter = verify.strip().upper()
        if verify_filter not in EVIDENCE_STATUSES:
            raise HTTPException(400, f"verify 只能是 {'/'.join(EVIDENCE_STATUSES)}")
    inner = (
        f"SELECT {', '.join(f'`{name}`' for name in EVIDENCE_COLUMNS)}, "
        f"{EVIDENCE_VERIFY_SQL} AS verify_status FROM {table}{where}"
    )
    outer, outer_params = "", dict(params)
    if verify_filter:
        outer = " WHERE verify_status = :verify"
        outer_params["verify"] = verify_filter
    try:
        items = rows(
            hash_engine,
            f"SELECT * FROM ({inner}) t{outer} ORDER BY id DESC LIMIT :limit",
            dict(outer_params, limit=size),
        )
    except Exception as exc:
        logger.exception("存证导出查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    output = io.StringIO()
    fields = list(EVIDENCE_COLUMNS) + ["verify_status"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(items)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="log-hash-evidence.csv"'},
    )


# --- 存证归档（导出 + 删除）---------------------------------------------------

def _parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """把 `YYYY-MM-DD` 解析成当天 00:00:00 / 23:59:59（本地时间，与 created_at 同口径）。"""
    if not value:
        return None
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    return day.replace(hour=23, minute=59, second=59) if end_of_day else day


@app.get("/api/v1/archive/preview")
def archive_preview(start: str | None = None, end: str | None = None):
    """只统计将要归档的条数，不导出、不删除（前端二次确认用）。"""
    info = archive_evidence(
        mode="manual", start=_parse_date(start), end=_parse_date(end, end_of_day=True), dry_run=True
    )
    return {"code": 0, "message": "ok", "data": dict(
        info,
        retentionMonths=settings.archive_retention_months,
        archiveDir=str(archive_root()),
    )}


@app.post("/api/v1/archive/manual")
def archive_manual(payload: dict = Body(...)):
    """手动归档：导出指定时间区间的存证为 CSV，确认落盘后删除对应记录。

    请求体：`{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "confirm": true}`
    `confirm` 必须为 true，避免误触删除。
    """
    if not payload.get("confirm"):
        raise HTTPException(400, "需要 confirm=true 才会执行导出并删除")
    start = _parse_date(payload.get("start"))
    end = _parse_date(payload.get("end"), end_of_day=True)
    if start is None and end is None:
        raise HTTPException(400, "请至少指定开始或结束日期")
    if start and end and start > end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    try:
        info = archive_evidence(mode="manual", start=start, end=end)
    except Exception as exc:
        logger.exception("手动归档失败")
        raise HTTPException(500, f"归档失败（未删除任何记录）：{type(exc).__name__}: {str(exc)[:200]}")
    return {"code": 0, "message": "ok", "data": info}


@app.get("/api/v1/archive/log")
def archive_log(page: int = 1, size: int = Query(30, ge=1, le=100)):
    """归档台账列表。"""
    page = max(1, page)
    selected = ("id", "mode", "file_path", "row_count", "deleted_count", "file_sha256",
                "range_start", "range_end", "before_time", "created_at")
    columns = ", ".join(f"`{name}`" for name in selected)
    try:
        with MonitorSessionLocal() as db:
            total = db.execute(text("SELECT COUNT(*) FROM `archive_log`")).scalar() or 0
            items = [dict(row) for row in db.execute(
                text(f"SELECT {columns} FROM `archive_log` ORDER BY id DESC LIMIT :limit OFFSET :offset"),
                {"limit": size, "offset": (page - 1) * size},
            ).mappings()]
    except Exception:
        logger.exception("归档台账查询失败")
        total, items = 0, []
    return {"code": 0, "message": "ok", "data": {"items": items, "page": page, "size": size, "total": total}}


@app.post("/api/v1/archive/auto")
def archive_auto():
    """立即执行一次自动归档（等同于定时任务的逻辑），便于手工验证。"""
    months = settings.archive_retention_months
    if months <= 0:
        return {"code": 0, "message": "自动归档未启用", "data": {"enabled": False}}
    cutoff = months_ago(months)
    try:
        info = archive_evidence(mode="auto", before=cutoff)
    except Exception as exc:
        logger.exception("自动归档失败")
        raise HTTPException(500, f"自动归档失败（未删除任何记录）：{type(exc).__name__}: {str(exc)[:200]}")
    return {"code": 0, "message": "ok", "data": dict(info, enabled=True, cutoff=cutoff.isoformat())}


# ── 服务端代码更新（停服 → 原地 git 更新 → 重启）────────────────────────────────
# 高影响操作：会重启整个服务并覆盖 /opt/log-fusion。当前项目没有任何鉴权，
# 任何能打开页面的人都能触发，仅适用于受控内网/测试环境（见 docs/DEPLOY.md）。
_UPDATE_UNIT = "log-fusion-update"
_UPDATE_SCRIPT_DEST = Path("/usr/local/bin/log-fusion-update.sh")
_UPDATE_SCRIPT_SRC = Path(__file__).resolve().parents[2] / "deploy" / "update-log-fusion.sh"


def _update_in_progress() -> bool:
    """是否有一次更新单元正在运行（用于拒绝并发触发）。

    更新以独立 systemd 单元执行，所以用 `systemctl is-active` 判断；
    服务器没有 systemd（如本地开发）时返回 False，由调用方另行报错。
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", _UPDATE_UNIT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


@app.post("/api/v1/admin/update")
def admin_update(payload: dict = Body(...)):
    """触发一次服务端代码更新（从 GitHub 拉取最新代码并重启服务）。

    请求体：`{"confirm": true}`，`confirm` 必须为 true 以避免误触。
    更新是异步的：接口只负责用 systemd-run 起一个**独立**的 transient 单元后立即返回，
    真正的停服 / 克隆 / 重启由 deploy/update-log-fusion.sh 完成。
    """
    if not payload.get("confirm"):
        raise HTTPException(400, "需要 confirm=true 才会执行更新")

    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise HTTPException(500, "服务器未安装 systemd-run，无法执行更新（见 docs/DEPLOY.md）")
    if not _UPDATE_SCRIPT_SRC.is_file():
        raise HTTPException(500, f"更新脚本不存在：{_UPDATE_SCRIPT_SRC}")
    if _update_in_progress():
        raise HTTPException(409, "已有一次更新正在进行，请稍后再试")

    # 脚本会被复制到仓库之外再执行：更新过程要 git reset 仓库里的文件，
    # 而 bash 是边读边执行脚本的，正在执行的脚本文件被改写会导致执行错乱。
    try:
        shutil.copyfile(_UPDATE_SCRIPT_SRC, _UPDATE_SCRIPT_DEST)
        _UPDATE_SCRIPT_DEST.chmod(0o755)
    except OSError as exc:
        raise HTTPException(500, f"准备更新脚本失败：{exc}") from exc

    # 同名单元若以 failed 状态残留，会让 systemd-run --unit 因重名失败，先尽力清理
    with suppress(OSError):
        subprocess.run(["systemctl", "reset-failed", _UPDATE_UNIT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    # 关键：用 systemd-run 起独立 transient 单元。它由 systemd 直接托管、不属于本服务的
    # cgroup，因此脚本里执行 `systemctl stop log-fusion` 时不会把它自己一起杀掉。
    try:
        subprocess.Popen(
            [systemd_run, f"--unit={_UPDATE_UNIT}", "--collect",
             "--description=log-fusion code update",
             "/bin/bash", str(_UPDATE_SCRIPT_DEST)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HTTPException(500, f"启动更新失败：{exc}") from exc

    logger.warning("已触发服务端代码更新 unit=%s", _UPDATE_UNIT)
    return {"code": 0, "message": "更新已启动", "data": {
        "unit": _UPDATE_UNIT,
        "log": f"journalctl -u {_UPDATE_UNIT} -f",
        "note": "服务将在数秒内重启，期间页面会短暂不可用",
    }}
