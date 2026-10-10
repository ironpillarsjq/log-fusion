import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request, Query, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session
from .config import get_settings
from .db import Base, db_health, linux_engine, monitor_engine, windows_engine, MonitorSessionLocal
from .models import ClientStatus
from .services import categories, query_category, summary, recent_rows
from .parsers import normalize_linux, normalize_windows, windows_table_for
from .ws import ws_manager

logger = logging.getLogger("log_fusion")
settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")
event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
sse_clients: int = 0  # 当前打开的日志 SSE 连接数（进程内），仅用于观测


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
        logger.exception("client_status 建表失败（请确认 MySQL 中存在 log_fusion 库）")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_tables()
    watchdog = asyncio.create_task(_status_watchdog())
    try:
        yield
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog


app.router.lifespan_context = lifespan

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


@app.get("/partials/db-status", response_class=HTMLResponse)
def db_status_partial():
    status = db_health()
    return "".join(
        f'<span class="badge rounded-pill text-bg-{"success" if connected else "danger"}">{name}: {"已连接" if connected else "不可用"}</span> '
        for name, connected in status.items()
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "databases": db_health(),
        # 实时通道的连接数：浏览器对同一地址只允许 6 条 HTTP/1.1 连接，
        # 每个页面会占 1 条 SSE + 1 条 WebSocket，这里用于排查连接被占满。
        "realtime": {
            "sse_clients": sse_clients,
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
def category_logs(category: str, platform: str = Query("Linux"), page: int = 1, size: int = 20, keyword: str | None = None):
    try:
        return {"code": 0, "message": "ok", "data": query_category(category, platform, page, size, keyword)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/logs/recent")
def recent_logs(limit: int = Query(20, ge=1, le=100)):
    return {"code": 0, "message": "ok", "data": recent_rows(limit)}


@app.get("/api/v1/logs/stream")
async def log_stream():
    async def generator():
        global sse_clients
        # 计数用于 /health 观测：连接未被及时释放会让浏览器 6 条连接额度被占满。
        sse_clients += 1
        logger.info("SSE 连接建立，当前 sse_clients=%d", sse_clients)
        try:
            yield {"event": "connected", "data": json.dumps({"serverTime": datetime.now(timezone.utc).isoformat()})}
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=20)
                    yield {"event": "log", "data": json.dumps(event, default=str, ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"serverTime": datetime.now(timezone.utc).isoformat()})}
        finally:
            sse_clients -= 1
            logger.info("SSE 连接关闭，当前 sse_clients=%d", sse_clients)
    return EventSourceResponse(generator())


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


def _summary_from_row(platform: str, table: str, row: dict) -> dict:
    event_time = row.get("event_time") or row.get("time_created")
    return {
        "platform": platform,
        "category": row.get("category") or row.get("channel") or table,
        "eventTime": event_time.isoformat() if isinstance(event_time, datetime) else event_time,
    }


def _summary_from_raw(platform: str, record) -> dict:
    if not isinstance(record, dict):
        return {"platform": platform, "category": None, "eventTime": None}
    return {
        "platform": platform,
        "category": record.get("category") or record.get("channel") or record.get("Channel"),
        "eventTime": record.get("event_time") or record.get("TimeGenerated") or record.get("timestamp"),
    }


def persist_logs(platform: str, records: list) -> dict:
    """解析 + 入库。解析失败与入库失败分开记录（errors[].stage 为 parse / insert）。"""
    normalize = normalize_windows if platform == "windows" else normalize_linux
    engine = windows_engine if platform == "windows" else linux_engine
    prepared: list[tuple[int, str, dict]] = []
    parse_errors: list[dict] = []
    summaries: list[dict] = []
    for index, record in enumerate(records):
        row, error = normalize(record)
        if row is None:
            parse_errors.append({"index": index, "stage": "parse", "reason": error})
            summaries.append(_summary_from_raw(platform, record))
            continue
        table = row["category"] if platform == "linux" else windows_table_for(row.get("channel"))
        prepared.append((index, table, row))
        summaries.append(_summary_from_row(platform, table, row))
    inserted, insert_errors = _persist_rows(engine, prepared)
    return {
        "inserted": inserted,
        "skipped": len(parse_errors),
        "failed": len(insert_errors),
        "errors": sorted(parse_errors + insert_errors, key=lambda item: item["index"]),
        "summaries": summaries,
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
        # 整体失败（连接超时/数据库不可用）：仍推送实时事件，保证页面能看到采集活动
        for record in records:
            await event_queue.put(_summary_from_raw(platform, record))
        return JSONResponse(status_code=200, content={
            "accepted": len(records), "persisted": False, "inserted": 0, "skipped": 0,
            "failed": len(records), "errors": [{"stage": "persist", "reason": (fatal or "")[:300]}],
        })

    for summary in result["summaries"]:
        await event_queue.put(summary)
    return JSONResponse(status_code=200, content={
        "accepted": len(records),
        "persisted": result["failed"] == 0 and result["skipped"] == 0,
        "inserted": result["inserted"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "errors": result["errors"],
    })


@app.get("/api/v1/logs/export")
def export_logs(platform: str = "Linux", category: str = "authentication_session", size: int = Query(1000, ge=1, le=10000)):
    # 显式放宽单次上限：分类列表接口上限是 100，导出需要真的能取到 size 条。
    result = query_category(category, platform, 1, size, size_cap=10000)
    output = io.StringIO()
    if result["items"]:
        writer = csv.DictWriter(output, fieldnames=list(result["items"][0]), extrasaction="ignore")
        writer.writeheader(); writer.writerows(result["items"])
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{category}.csv"'})


def _evidence_rows() -> list[dict]:
    try:
        with linux_engine.connect() as conn:
            return [dict(row) for row in conn.execute(text("SELECT * FROM audit_log_evidence ORDER BY received_at DESC LIMIT 1000")).mappings()]
    except Exception:
        return []


@app.get("/api/v1/evidence/summary")
def evidence_summary():
    items = _evidence_rows()
    statuses = {status: sum(1 for item in items if item.get("verify_status") == status) for status in ("VALID", "HASH_MISMATCH", "RAW_MISSING", "PENDING")}
    return {"code": 0, "message": "ok", "data": {"total": len(items), "statuses": statuses}}


@app.get("/api/v1/evidence")
def evidence_list(page: int = 1, size: int = Query(20, ge=1, le=100), status: str | None = None):
    items = _evidence_rows()
    if status:
        items = [item for item in items if item.get("verify_status") == status]
    start = (max(1, page) - 1) * size
    return {"code": 0, "message": "ok", "data": {"items": items[start:start + size], "page": page, "size": size, "total": len(items)}}


@app.get("/api/v1/evidence/export")
def evidence_export():
    items = _evidence_rows()
    output = io.StringIO()
    if items:
        writer = csv.DictWriter(output, fieldnames=list(items[0]), extrasaction="ignore")
        writer.writeheader(); writer.writerows(items)
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit-evidence.csv"'})
