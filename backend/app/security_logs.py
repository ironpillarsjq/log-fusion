"""NSFOCUS polling: bounded time windows, atomic checkpoints and immutable deduplication.

DDL/field catalog live in security-log-client/sql. This module never creates tables
at import time. Network work runs outside the ASGI event loop.
"""
import calendar
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import httpx
from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "security-log-client"
CATALOG = {t["api_type"]: t for t in json.loads(
    (PROJECT / "sql/log_fusion_nsfocus_tables.fields.json").read_text(encoding="utf-8"))["tables"]}
ORDER = ("ipblacklistlog", "ips_web_secure_event_log", "ipslog")
LOCK = "log-fusion:nsfocus-sync"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def months_before(epoch, months):
    date = datetime.fromtimestamp(epoch, timezone.utc)
    # Calendar months in the configured site timezone (Beijing), including month-end clamping.
    from datetime import timedelta
    site = date.astimezone(timezone(timedelta(hours=8)))
    year, month0 = divmod(site.year * 12 + site.month - 1 - months, 12)
    return int(site.replace(year=year, month=month0 + 1,
                           day=min(site.day, calendar.monthrange(year, month0 + 1)[1])).timestamp())


def _integer(value, low=-(2**63), high=2**63 - 1):
    if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
        raise ValueError("invalid integer source field")
    number = int(value)
    if not low <= number <= high:
        raise ValueError("source integer out of column range")
    return number


def normalize_record(kind, record, instance):
    spec = CATALOG[kind]
    if not isinstance(record, dict) or not isinstance(record.get("source"), dict):
        raise ValueError("upstream record missing source")
    source = record["source"]
    index = record.get("index")
    if not isinstance(index, str) or not index or len(index) > 255:
        raise ValueError("upstream record missing valid index")
    result = {}
    for field in spec["fields"]:
        key, sql_type = field["field"], field["sql_type"]
        value = source.get(key)
        if value is None or value == "":
            if not field["nullable"]:
                raise ValueError("required source field missing: " + key)
            result[key] = None
        elif "INT" in sql_type:
            high = 65535 if sql_type.startswith("SMALLINT") else 2**64 - 1 if "UNSIGNED" in sql_type else 2**63 - 1
            result[key] = _integer(value, 0 if "UNSIGNED" in sql_type else -(2**63), high)
        else:
            value = json_text(value) if isinstance(value, (dict, list)) else str(value)
            width = re.fullmatch(r"VARCHAR\((\d+)\)", sql_type)
            if width and len(value) > int(width[1]):
                raise ValueError("source string exceeds column width: " + key)
            if sql_type == "TEXT" and len(value.encode("utf-8")) > 65535:
                raise ValueError("source text exceeds column width: " + key)
            result[key] = value
    timestamp = _integer(source.get("timestamp"), 0)
    raw = json_text(record)
    result.update(source_instance=instance, upstream_index=index,
                  event_time=datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None),
                  received_time=(datetime.fromtimestamp(_integer(source["recv_time"], 0) / 1000,
                      timezone.utc).replace(tzinfo=None) if source.get("recv_time") not in (None, "") else None),
                  ingested_at=utcnow(), raw_record=raw, record_sha256=hashlib.sha256(raw.encode("utf-8")).digest())
    return result


def unique_records(kind, records, instance):
    unique = {}
    for raw in records:
        row = normalize_record(kind, raw, instance)
        key = row["upstream_index"], row["log_id"]
        if key in unique and unique[key]["record_sha256"] != row["record_sha256"]:
            raise ValueError("same log identity has conflicting content within response")
        unique[key] = row
    return list(unique.values())


class Upstream:
    def __init__(self, config_path, timeout=90):
        self.path = Path(config_path)
        self.config = json.loads(self.path.read_text(encoding="utf-8-sig"))
        base = self.config["base_url"]
        url = httpx.URL(base)
        if url.scheme != "https" or not url.host or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("invalid upstream HTTPS base URL")
        self.client = httpx.Client(base_url=base.rstrip("/"), timeout=timeout,
                                  verify=self.config.get("ca_cert") or not self.config.get("insecure", False),
                                  trust_env=False, follow_redirects=False)

    def close(self):
        self.client.close()

    def _json(self, response):
        if response.status_code != 200:
            raise RuntimeError("upstream HTTP " + str(response.status_code))
        value = response.json()
        if not isinstance(value, dict) or value.get("errCode", 0) not in (0, "0"):
            raise RuntimeError("upstream business error")
        return value

    def login(self):
        value = self._json(self.client.post("/api/v2/auth", json={
            "name": self.config["username"], "password": self.config["password"]}))
        token = value.get("token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("upstream auth missing token")
        self.config.update(token=token, token_saved_at=datetime.now(timezone.utc).isoformat())
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json_text(self.config) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def search(self, kind, start, end, size):
        if not self.config.get("token"):
            self.login()
        body = dict(query_type="es", type=kind, query_string="*", startTime=start,
                    endTime=end, page=1, size=size)
        response = self.client.post("/api/audit/log/search", json=body,
                                    headers={"Auth-Token": self.config["token"]})
        if response.status_code == 401:
            self.login()
            response = self.client.post("/api/audit/log/search", json=body,
                                        headers={"Auth-Token": self.config["token"]})
        value = self._json(response)
        if not isinstance(value.get("data"), list) or isinstance(value.get("totalSize"), bool) or not isinstance(value.get("totalSize"), int) or value["totalSize"] < 0:
            raise ValueError("upstream missing exact count/data")
        if value.get("partOfData") not in (None, False) or value.get("page") != 1:
            raise ValueError("upstream partial data or wrong page")
        return value


def new_state(start, end, rows=0):
    return {"history": {"start": start, "end": end, "pending": [[start, end]],
                        "checked_until": start - 1, "expected": None, "status": "pending"},
            "live": {"checked_until": end, "pending": [], "status": "pending"},
            "rows": rows, "duplicates": 0, "last_success": None, "error": "",
            "failures": 0, "retry_at": 0, "next_poll": 0}


def split_interval(start, end):
    if start >= end:
        raise ValueError("single second exceeds safe API window; manual sharding required")
    midpoint = (start + end) // 2
    # Overlap the boundary second: safe for either inclusive/exclusive endTime;
    # uniqueness handles overlap. A one-second-width interval cannot shrink further.
    if end - start == 1:
        raise ValueError("one-second window exceeds safe API result limit")
    return [[start, midpoint], [midpoint, end]]


class SecurityStore:
    def __init__(self, engine, instance):
        self.engine, self.instance = engine, instance
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", instance):
            raise ValueError("invalid source instance ID")

    @contextmanager
    def locked(self):
        # Session advisory lock spans HTTP + DB transaction; crash releases it.
        with self.engine.connect() as connection:
            acquired = connection.execute(text("SELECT GET_LOCK(:name,0)"), {"name": LOCK}).scalar() == 1
            connection.commit()
            try:
                yield connection if acquired else None
            finally:
                if acquired:
                    if connection.in_transaction():
                        connection.rollback()
                    connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK})
                    connection.commit()

    def states(self, connection):
        found = connection.execute(text("SELECT log_type,state FROM nsfocus_sync_state WHERE source_instance=:instance"),
                                   {"instance": self.instance}).mappings()
        return {r["log_type"]: json.loads(r["state"]) if isinstance(r["state"], str) else r["state"] for r in found}

    def save(self, connection, kind, state):
        connection.execute(text("UPDATE nsfocus_sync_state SET state=:state,updated_at=:updated WHERE source_instance=:instance AND log_type=:kind"),
                           dict(state=json_text(state), updated=utcnow(), instance=self.instance, kind=kind))

    def initialize(self, months=3, end=None, extend=False):
        if not 1 <= months <= 36:
            raise ValueError("history months must be 1..36")
        end = int(time.time()) - 30 if end is None else end
        start = months_before(end, months)
        with self.locked() as connection:
            if connection is None:
                return False
            states = self.states(connection)
            for kind in ORDER:
                if kind not in states:
                    table = CATALOG[kind]["table"]
                    count = connection.execute(text(f"SELECT COUNT(*) FROM `{table}` WHERE source_instance=:instance"),
                                               {"instance": self.instance}).scalar_one()
                    connection.execute(text("INSERT INTO nsfocus_sync_state(source_instance,log_type,state,updated_at) VALUES(:instance,:kind,:state,:updated)"),
                                       dict(instance=self.instance, kind=kind, state=json_text(new_state(start, end, count)), updated=utcnow()))
                elif extend:
                    state = states[kind]
                    job = state["history"]
                    changed = False
                    if start < job["start"]:
                        job["pending"].insert(0, [start, job["start"]])
                        job.update(start=start, checked_until=start - 1)
                        changed = True
                    if end > job["end"]:
                        job["pending"].append([job["end"], end])
                        job["end"] = end
                        changed = True
                    if changed:
                        job.update(status="pending", expected=None)
                        state.update(error="", retry_at=0, failures=0)
                        self.save(connection, kind, state)
            connection.commit()
        return True

    def initialize_live(self, *, overlap=120, lag=30, now=None):
        """Start a new live session without resuming historical or stale live queues.

        Existing rows, counters and history windows remain available for an explicit
        manual import; unfinished history is marked stopped rather than complete.
        """
        now = int(time.time()) if now is None else now
        end = now - lag
        start = max(0, end - overlap)
        with self.locked() as connection:
            if connection is None:
                return False
            states = self.states(connection)
            for kind in ORDER:
                state = states.get(kind)
                if state is None:
                    table = CATALOG[kind]["table"]
                    count = connection.execute(text(f"SELECT COUNT(*) FROM `{table}` WHERE source_instance=:instance"),
                                               {"instance": self.instance}).scalar_one()
                    state = new_state(start, end, count)
                    state["history"].update(pending=[], status="disabled")
                    connection.execute(text("INSERT INTO nsfocus_sync_state(source_instance,log_type,state,updated_at) VALUES(:instance,:kind,:state,:updated)"),
                                       dict(instance=self.instance, kind=kind, state=json_text(state), updated=utcnow()))
                elif state["history"]["status"] != "complete":
                    state["history"]["status"] = "stopped"
                state["live"].update(checked_until=start, since=start, pending=[], status="pending")
                state.update(collection_mode="live_only", next_poll=0, retry_at=0, failures=0, error="")
                self.save(connection, kind, state)
            connection.commit()
        return True

    def insert(self, connection, kind, records):
        rows = unique_records(kind, records, self.instance)
        table = CATALOG[kind]["table"]
        inserted = 0
        for offset in range(0, len(rows), 250):
            batch = rows[offset:offset + 250]
            query = text(f"SELECT upstream_index,log_id,record_sha256 FROM `{table}` WHERE source_instance=:instance AND upstream_index=:index AND log_id IN :ids").bindparams(bindparam("ids", expanding=True))
            existing = {}
            # Bind the full unique-key prefix; omitting upstream_index scans all instance rows.
            for index in {r["upstream_index"] for r in batch}:
                existing.update({(r.upstream_index, r.log_id): bytes(r.record_sha256) for r in connection.execute(query,
                    dict(instance=self.instance, index=index,
                         ids=[r["log_id"] for r in batch if r["upstream_index"] == index]))})
            fresh = []
            for row in batch:
                digest = existing.get((row["upstream_index"], row["log_id"]))
                if digest is not None and digest != row["record_sha256"]:
                    raise ValueError("stored log identity has conflicting upstream content; original preserved")
                if digest is None:
                    fresh.append(row)
            if fresh:
                # Execute individually when a batch exceeds 8 MiB; never send a 64 MiB packet.
                columns = list(fresh[0])
                statement = text(f"INSERT INTO `{table}` (" + ",".join(f"`{k}`" for k in columns) + ") VALUES (" + ",".join(":" + k for k in columns) + ")")
                if sum(len(r["raw_record"].encode("utf-8")) * 2 for r in fresh) > 8 * 1024 * 1024:
                    for row in fresh:
                        connection.execute(statement, row)
                else:
                    connection.execute(statement, fresh)
                inserted += len(fresh)
        return inserted, len(records) - inserted

    def summary(self):
        with self.engine.connect() as connection:
            states = self.states(connection)
            active = connection.execute(text("SELECT IS_USED_LOCK(:name)"), {"name": LOCK}).scalar() is not None
        return {"sourceInstance": self.instance, "collectorActive": active, "generatedAt": datetime.now(timezone.utc).isoformat(),
                "types": [{"type": kind, "label": CATALOG[kind]["display"], "table": CATALOG[kind]["table"],
                           "initialized": kind in states, **states.get(kind, {})} for kind in ORDER]}

    def logs(self, kind, before=None, limit=30):
        table = CATALOG[kind]["table"]
        columns = ["id", "event_time", "sip", "dip", "sport", "dport", "ruleid", "threat_type", "log_id",
                   "event", "protocol", "severity", "action", "acted", "count", "dev_ip"]
        # 仅查询各类已存在的列；列表不加载可能很大的 raw_record/rawinfo。
        columns += {"ipslog": [], "ipblacklistlog": ["domain", "ip", "engine"],
                    "ips_web_secure_event_log": ["url"]}[kind]
        projection = ",".join(f"`{column}`" for column in columns)
        query = f"SELECT {projection} FROM `{table}` WHERE source_instance=:instance"
        params = {"instance": self.instance, "limit": limit + 1}
        if before is not None:
            query += " AND id<:before"
            params["before"] = before
        with self.engine.connect() as connection:
            rows = [dict(r) for r in connection.execute(text(query + " ORDER BY id DESC LIMIT :limit"), params).mappings()]
        for row in rows:
            row["event_time"] = row["event_time"].isoformat(timespec="milliseconds") + "Z"
        return {"items": rows[:limit], "nextBefore": rows[limit - 1]["id"] if len(rows) > limit else None,
                "order": "id_desc", "type": kind}

    def detail(self, kind, record_id):
        table = CATALOG[kind]["table"]
        with self.engine.connect() as connection:
            row = connection.execute(text(f"SELECT raw_record FROM `{table}` WHERE id=:id AND source_instance=:instance"),
                                     dict(id=record_id, instance=self.instance)).scalar_one_or_none()
        return json.loads(row) if isinstance(row, str) else row


class SecurityWorker:
    def __init__(self, store, config_path, *, page_size=10000, poll_seconds=30, overlap=600, lag=30, timeout=90, history_only=False, live_only=False, live_window=600):
        if not 1 <= page_size <= 10000 or poll_seconds < 5 or overlap < 1 or lag < 0 or live_window < overlap:
            raise ValueError("invalid security sync limits")
        if history_only and live_only:
            raise ValueError("history_only and live_only are mutually exclusive")
        self.store, self.config_path = store, config_path
        self.page_size, self.poll_seconds, self.overlap, self.lag = page_size, poll_seconds, overlap, lag
        self.timeout, self.position = timeout, 0
        self.history_only = history_only
        self.live_only, self.live_window = live_only, live_window

    def step(self):
        with self.store.locked() as connection:
            if connection is None:
                return None
            states = self.store.states(connection)
            connection.commit()
            now = int(time.time())
            # Rotate types so a slow IPS history does not starve blacklists/WEB/live.
            ordered = ORDER[self.position:] + ORDER[:self.position]
            self.position = (self.position + 1) % len(ORDER)
            for kind in ordered:
                state = states.get(kind)
                if state is None or state["retry_at"] > now:
                    continue
                if self.live_only:
                    # Never issue requests for an old persisted split window after a long
                    # outage. Latest-only mode intentionally abandons the older gap.
                    end = now - self.lag
                    floor = max(state["live"].get("since", 0), end - self.live_window, 0)
                    state["live"]["pending"] = [[max(a, floor), min(b, end)] for a, b in state["live"]["pending"]
                                                  if max(a, floor) <= min(b, end)]
                if not self.history_only and not state["live"]["pending"] and state["next_poll"] <= now:
                    start = max(floor if self.live_only else state["history"]["start"],
                                state["live"]["checked_until"] - self.overlap)
                    end = now - self.lag
                    if start < end:
                        state["live"].update(pending=[[start, end]], target=end, status="running")
                    state["next_poll"] = now + self.poll_seconds
                # History always makes progress, even when live refresh takes > poll interval.
                mode = "live" if self.live_only else "history" if self.history_only or (state["history"]["pending"] and state.get("last_mode") == "live") else "live" if state["live"]["pending"] else "history"
                job = state[mode]
                if not job["pending"]:
                    continue
                a, b = job["pending"][0]
                job["status"] = "running"
                upstream = None
                try:
                    upstream = Upstream(self.config_path, self.timeout)
                    probe = upstream.search(kind, a, b, 1)
                    total = probe["totalSize"]
                    if mode == "history" and job["expected"] is None and a == job["start"] and b == job["end"]:
                        job["expected"] = total
                    rows = []
                    split = total > self.page_size
                    if total and not split:
                        page = upstream.search(kind, a, b, total)
                        rows = page["data"]
                        # Counts may change under retention/late arrivals. Never silently finish a partial page.
                        split = page["totalSize"] != total or len(rows) != total
                        # Identical repeated identities are legitimate upstream duplicates.
                        # Normalize the entire response before writing; conflicting duplicates fail.
                        if not split:
                            unique_records(kind, rows, self.store.instance)
                    with connection.begin():
                        if split:
                            job["pending"][0:1] = split_interval(a, b)
                        else:
                            inserted, duplicate = self.store.insert(connection, kind, rows)
                            state["rows"] += inserted
                            state["duplicates"] += duplicate
                            job["pending"].pop(0)
                            job["checked_until"] = b
                            state["last_success"] = datetime.now(timezone.utc).isoformat()
                            if not job["pending"]:
                                job["status"] = "complete" if mode == "history" else "watching"
                        state.update(error="", failures=0, retry_at=0, last_mode=mode)
                        self.store.save(connection, kind, state)
                    return {"type": "security_logs", "logType": kind, "mode": mode,
                            "rows": state["rows"], "status": job["status"]}
                except Exception as exc:
                    if connection.in_transaction():
                        connection.rollback()
                    # Reload committed checkpoint: an insert/normalization failure never advances it.
                    original = self.store.states(connection)[kind]
                    original["failures"] += 1
                    original.update(error=type(exc).__name__ + ": " + self._safe_error(exc),
                                    retry_at=now + min(300, 5 * 2**min(original["failures"], 6)))
                    original[mode]["status"] = "retrying"
                    self.store.save(connection, kind, original)
                    connection.commit()
                    return {"type": "security_logs", "logType": kind, "status": "retrying"}
                finally:
                    if upstream:
                        upstream.close()
            return None

    @staticmethod
    def _safe_error(exc):
        # Driver errors can include bound log payloads or connection URLs. Never expose them.
        if isinstance(exc, (ValueError, RuntimeError)):
            return str(exc)[:220]
        if isinstance(exc, httpx.HTTPError):
            return "upstream connection failed; retry scheduled"
        return "sync failed; checkpoint preserved"
