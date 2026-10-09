import asyncio
import csv
import io
import json
import os
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request, Query, HTTPException, Body
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text
from .config import get_settings
from .db import db_health, linux_engine, windows_engine
from .services import categories, query_category, summary, recent_rows

settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")
heartbeats: dict[str, dict[str, Any]] = {}
event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)


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
    return {"status": "ok", "databases": db_health()}


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
        yield {"event": "connected", "data": json.dumps({"serverTime": datetime.now(timezone.utc).isoformat()})}
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=20)
                yield {"event": "log", "data": json.dumps(event, default=str, ensure_ascii=False)}
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": json.dumps({"serverTime": datetime.now(timezone.utc).isoformat()})}
    return EventSourceResponse(generator())


@app.get("/api/v1/monitor/summary")
def monitor_summary():
    now = datetime.now(timezone.utc).timestamp()
    states = {"ONLINE": 0, "DELAYED": 0, "OFFLINE": 0}
    for client in heartbeats.values():
        age = now - client["received_at"]
        state = "ONLINE" if age <= 10 else "DELAYED" if age <= 30 else "OFFLINE"
        states[state] += 1
    return {"code": 0, "message": "ok", "data": {"total": len(heartbeats), "states": states, "onlineRate": round(states["ONLINE"] / len(heartbeats) * 100, 2) if heartbeats else 0}}


@app.get("/api/v1/monitor/clients")
def monitor_clients(keyword: str | None = None, os_type: str | None = None):
    now = datetime.now(timezone.utc).timestamp()
    data = []
    for client in heartbeats.values():
        if keyword and keyword.lower() not in json.dumps(client, ensure_ascii=False).lower():
            continue
        if os_type and client.get("os_type") != os_type:
            continue
        delay = max(0, int(now - client["received_at"]))
        status = "ONLINE" if delay <= 10 else "DELAYED" if delay <= 30 else "OFFLINE"
        data.append({**client, "heartbeatDelaySec": delay, "heartbeatStatus": status})
    return {"code": 0, "message": "ok", "data": {"items": data, "total": len(data)}}


@app.get("/api/v1/monitor/clients/{client_id}")
def monitor_client(client_id: str):
    client = heartbeats.get(client_id)
    if not client:
        raise HTTPException(404, "client not found")
    return {"code": 0, "message": "ok", "data": client}


@app.get("/api/v1/monitor/clients/{client_id}/logs")
def monitor_client_logs(client_id: str, limit: int = Query(20, ge=1, le=100)):
    # Existing legacy tables do not consistently carry client_id; return the
    # common recent view and let future migrations add exact source filtering.
    return {"code": 0, "message": "ok", "data": {"clientId": client_id, "items": recent_rows(limit)}}


@app.post("/api/v1/heartbeat")
def heartbeat(payload: list[dict[str, Any]]):
    now = datetime.now(timezone.utc).timestamp()
    for item in payload:
        client_id = str(item.get("client_id") or "UNKNOWN_HOST")
        heartbeats[client_id] = {"clientId": client_id, "serverName": item.get("server_name") or client_id, "ipAddress": item.get("ip_address"), "os_type": item.get("os_type") or "Unknown", "received_at": now, "lastHeartbeatAt": datetime.now(timezone.utc).isoformat()}
    return "pong"


def _insert_windows(records: list[dict]):
    table_map = {"application": "application_logs", "security": "security_logs", "setup": "setup_logs", "system": "system_logs", "forwardedevents": "forwardedevents_logs"}
    with windows_engine.begin() as conn:
        for item in records:
            if "event_time" not in item and item.get("TimeGenerated"):
                item = {**item, "event_time": item.get("TimeGenerated"), "event_id": item.get("EventID"), "record_number": item.get("RecordNumber"), "source_name": item.get("SourceName"), "computer_name": item.get("ComputerName"), "channel": item.get("Channel"), "raw": item}
            channel = str(item.get("channel") or "application").lower().replace("-", "")
            table = table_map.get(channel, "application_logs")
            conn.execute(text(f"INSERT INTO `{table}` (source_year, source_file, provider_name, event_id, time_created, event_record_id, channel, computer, system_extra, eventdata) VALUES (:year, :file, :provider, :event_id, :time_created, :record, :channel, :computer, :extra, :data)"), {"year": str(item.get("event_time", ""))[:4] or None, "file": f"fluent-bit://{item.get('client_id') or 'unknown'}/{channel}", "provider": item.get("source_name"), "event_id": item.get("event_id"), "time_created": item.get("event_time"), "record": item.get("record_number"), "channel": channel, "computer": item.get("computer_name"), "extra": "{}", "data": json.dumps(item.get("raw", item), ensure_ascii=False)})


def _insert_linux(records: list[dict]):
    with linux_engine.begin() as conn:
        for item in records:
            normalized = item.get("normalized") if isinstance(item.get("normalized"), dict) else item
            if "event_time" not in normalized and item.get("timestamp"):
                normalized = {**normalized, "event_time": item.get("timestamp"), "event_id": abs(hash(f"{item.get('timestamp')}|{item.get('log','')}")) % 2147483647, "category": "authentication_session", "type": "SYSLOG", "tips": item.get("log")}
            category = str(normalized.get("category") or item.get("category") or "authentication_session")
            if category not in {"authentication_session", "account_security_change", "process_command_execution", "file_object_access", "network_ipc_communication", "system_service_audit_lifecycle", "security_policy_config_change"}:
                category = "authentication_session"
            allowed = {"event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id", "operation", "account", "executable", "hostname", "source_address", "terminal", "result", "tips", "command", "object_path", "object_inode", "action", "target", "destination_address", "source_port", "destination_port", "protocol"}
            fields = [field for field in normalized if field in allowed and field not in {"client_id"}]
            if "event_time" not in fields or "event_id" not in fields or "category" not in fields or "type" not in fields:
                continue
            names = ",".join(f"`{field}`" for field in fields)
            values = {field: normalized.get(field) for field in fields}
            placeholders = ",".join(f":{field}" for field in fields)
            conn.execute(text(f"INSERT INTO `{category}` ({names}) VALUES ({placeholders})"), values)


@app.post("/api/v1/logs/{platform}")
async def ingest_logs(platform: str, payload: Any = Body(...)):
    if platform.lower() not in {"linux", "windows"}:
        raise HTTPException(404, "unsupported platform")
    records = payload if isinstance(payload, list) else [payload]
    persisted = True
    try:
        if platform.lower() == "windows":
            await asyncio.wait_for(asyncio.to_thread(_insert_windows, records), timeout=8)
        else:
            await asyncio.wait_for(asyncio.to_thread(_insert_linux, records), timeout=8)
    except Exception as exc:
        # Real Fluent Bit payloads must still reach the live stream when an
        # old table has a schema difference; persistence can be migrated later.
        persisted = False
        print(f"log persistence failed ({platform}): {exc}")
    for item in records:
        await event_queue.put({"platform": platform, "category": item.get("category") or item.get("channel"), "eventTime": item.get("event_time") or item.get("TimeGenerated")})
    return JSONResponse(status_code=200, content={"accepted": len(records), "persisted": persisted})


@app.get("/api/v1/logs/export")
def export_logs(platform: str = "Linux", category: str = "authentication_session", size: int = Query(1000, ge=1, le=10000)):
    result = query_category(category, platform, 1, size)
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
