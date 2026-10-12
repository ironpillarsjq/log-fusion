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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import get_settings
from .db import Base, db_health, hash_engine, linux_engine, monitor_engine, windows_engine, MonitorSessionLocal, one, rows
from .models import ClientStatus, LogHash
from .services import categories, query_category, summary, recent_rows
from .parsers import normalize_linux, normalize_windows, windows_table_for
from .hashing import build_evidence_row
from .archive import archive_evidence, archive_root, months_ago
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_tables()
    watchdog = asyncio.create_task(_status_watchdog())
    archiver = asyncio.create_task(_auto_archive_loop())
    try:
        yield
    finally:
        for task in (watchdog, archiver):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


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
    summaries: list[dict] = []
    event_times: dict[int, Any] = {}
    for index, record in enumerate(records):
        row, error = normalize(record)
        if row is None:
            parse_errors.append({"index": index, "stage": "parse", "reason": error})
            summaries.append(_summary_from_raw(platform, record))
            continue
        table = row["category"] if platform == "linux" else windows_table_for(row.get("channel"))
        prepared.append((index, table, row))
        summaries.append(_summary_from_row(platform, table, row))
        event_times[index] = row.get("event_time") or row.get("time_created")
    inserted, insert_errors = _persist_rows(engine, prepared)
    # 存证独立于业务入库：先算哈希存证，再返回结果；存证失败不回滚已入库的日志。
    hashed, hash_errors = _persist_evidence(platform, records, event_times)
    return {
        "inserted": inserted,
        "skipped": len(parse_errors),
        "failed": len(insert_errors),
        "hashed": hashed,
        "errors": sorted(parse_errors + insert_errors + hash_errors, key=lambda item: item["index"]),
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
            "failed": len(records), "hashed": 0,
            "errors": [{"stage": "persist", "reason": (fatal or "")[:300]}],
        })

    for summary in result["summaries"]:
        await event_queue.put(summary)
    return JSONResponse(status_code=200, content={
        "accepted": len(records),
        "persisted": result["failed"] == 0 and result["skipped"] == 0,
        "inserted": result["inserted"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "hashed": result["hashed"],
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


EVIDENCE_COLUMNS = ("id", "log_timestamp", "platform", "source", "client_id", "event_time", "created_at", "log_hash")


def _evidence_where(platform: str | None, keyword: str | None) -> tuple[str, dict]:
    conditions, params = [], {}
    if platform:
        conditions.append("platform = :platform")
        params["platform"] = platform
    if keyword:
        conditions.append("(raw_log LIKE :keyword OR client_id LIKE :keyword OR source LIKE :keyword OR log_hash LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    return (" WHERE " + " AND ".join(conditions) if conditions else ""), params


def _evidence_table() -> str:
    return f"`{settings.log_hash_db}`.`{settings.log_hash_table}`"


@app.get("/api/v1/evidence/summary")
def evidence_summary():
    """存证摘要：直接对 log_hash 表做 COUNT，不再受单次取数上限影响。

    查询失败时返回 **503** 而不是 0：把数据库故障降级成「0 条存证」会让
    「库连不上」和「确实没有存证」无法区分，操作者会误以为数据被清空了。
    """
    try:
        total = one(hash_engine, f"SELECT COUNT(*) AS n FROM {_evidence_table()}")["n"]
    except Exception as exc:
        logger.exception("存证摘要查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    statuses = {"VALID": total, "HASH_MISMATCH": 0, "RAW_MISSING": 0, "PENDING": 0}
    return {"code": 0, "message": "ok", "data": {"total": total, "statuses": statuses}}


@app.get("/api/v1/evidence")
def evidence_list(
    page: int = 1,
    size: int = Query(30, ge=1, le=100),
    platform: str | None = None,
    keyword: str | None = None,
):
    """存证分页列表：数据库侧 LIMIT/OFFSET 分页，不再全量取回后在 Python 切片。"""
    page = max(1, page)
    where, params = _evidence_where(platform, keyword)
    table = _evidence_table()
    selected = ", ".join(f"`{name}`" for name in EVIDENCE_COLUMNS)
    try:
        total = one(hash_engine, f"SELECT COUNT(*) AS n FROM {table}{where}", params)["n"]
        items = rows(
            hash_engine,
            f"SELECT {selected} FROM {table}{where} ORDER BY id DESC LIMIT :limit OFFSET :offset",
            dict(params, limit=size, offset=(page - 1) * size),
        )
    except Exception as exc:
        logger.exception("存证列表查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    return {"code": 0, "message": "ok", "data": {
        "items": items, "page": page, "size": size, "total": total,
        "pages": (total + size - 1) // size if size else 0,
    }}


@app.get("/api/v1/evidence/export")
def evidence_export(platform: str | None = None, keyword: str | None = None, size: int = Query(10000, ge=1, le=100000)):
    """存证 CSV 导出（只读，不删除）。删除请走 /api/v1/archive/manual。"""
    where, params = _evidence_where(platform, keyword)
    table = _evidence_table()
    selected = ", ".join(f"`{name}`" for name in EVIDENCE_COLUMNS)
    try:
        items = rows(
            hash_engine,
            f"SELECT {selected} FROM {table}{where} ORDER BY id DESC LIMIT :limit",
            dict(params, limit=size),
        )
    except Exception as exc:
        logger.exception("存证导出查询失败")
        raise HTTPException(503, f"存证库查询失败：{type(exc).__name__}: {str(exc)[:200]}")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(EVIDENCE_COLUMNS), extrasaction="ignore")
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
