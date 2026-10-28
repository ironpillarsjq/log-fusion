"""WAF/DAS Syslog lifecycle for run.py, with an optional standalone/status/replay CLI."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import ipaddress
import json
import logging
from pathlib import Path
import re
import signal
import socket
import threading

from .syslog_parsers import parse_message
from .syslog_protocol import ProtocolError, TCPFramer, device_timezone
from .syslog_store import Inbox, MySQLDeviceStore

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def drain_once(inbox: Inbox, sink, limit: int = 100, stop: threading.Event | None = None) -> dict:
    counts = dict(stored=0, quarantined=0, failed=0)
    for item in inbox.pending(limit):
        if stop is not None and stop.is_set():
            break
        try:
            parsed = parse_message(item["raw"], product=item["product"], source_instance=item["source_instance"],
                                   peer_ip=item["peer_ip"], transport=item["transport"],
                                   timezone_name=item["timezone_name"], body_base64=bool(item["body_base64"]),
                                   received_at=datetime.fromisoformat(item["received_at"]), ingest_id=item["ingest_id"])
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            inbox.failed(item["sequence"], str(exc), quarantine=True)
            counts["quarantined"] += 1
            logger.warning("Syslog quarantined ingest_id=%s reason=%s", item["ingest_id"], exc)
            continue
        try:
            sink.write(parsed)
        except Exception as exc:
            # Driver exceptions can contain SQL bind values (SQL statements/credentials). Do not log them.
            inbox.failed(item["sequence"], "database write failed: " + type(exc).__name__)
            counts["failed"] += 1
            logger.error("Syslog database write failed ingest_id=%s error_type=%s; retry retained",
                         item["ingest_id"], type(exc).__name__)
            break
        inbox.acknowledge(item["sequence"])
        counts["stored"] += 1
    return counts


class Receiver:
    def __init__(self, inbox: Inbox, *, product: str, source_instance: str, host: str = "0.0.0.0",
                 port: int = 5514, protocols: str = "both", timezone_name: str = "+08:00",
                 body_base64: bool = False, max_bytes: int = 1024**2, framing: str = "auto",
                 allow_sources: tuple[str, ...] = (), idle_timeout: float = 60, max_connections: int = 64):
        if product not in ("waf", "das") or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", source_instance):
            raise ValueError("invalid product/source_instance")
        if protocols not in ("both", "tcp", "udp") or not 0 <= port <= 65535 or max_bytes < 1 or idle_timeout <= 0 or max_connections < 1:
            raise ValueError("invalid receiver limits")
        TCPFramer(max_bytes, framing)
        device_timezone(timezone_name)
        self.inbox, self.product, self.source_instance = inbox, product, source_instance
        self.host, self.port, self.protocols = host, port, protocols
        self.timezone_name, self.body_base64 = timezone_name, body_base64
        self.max_bytes, self.framing, self.idle_timeout = max_bytes, framing, idle_timeout
        self.max_connections = max_connections
        self.allowed = tuple(ipaddress.ip_network(value, strict=False) for value in allow_sources)
        self.server = self.udp_socket = self.udp_task = None
        self._udp_use_reader = False
        self.clients: set[asyncio.Task] = set()
        self.writers = set()
        self.closing = False
        self.counters = dict(received=0, dropped=0, rejected=0, framing_errors=0)

    def permits(self, peer: str) -> bool:
        address = ipaddress.ip_address(peer)
        return not self.allowed or any(address in network for network in self.allowed)

    async def accept(self, raw: bytes, peer: str, transport: str):
        received_at = datetime.now(timezone.utc)
        if self.closing:
            return
        if not self.permits(peer):
            self.counters["rejected"] += 1
            return
        if not raw or len(raw) > self.max_bytes:
            self.counters["dropped"] += 1
            logger.warning("Syslog %s message empty/oversized peer=%s bytes=%d", transport, peer, len(raw))
            return
        try:
            committing = asyncio.create_task(asyncio.to_thread(
                self.inbox.append, raw, product=self.product,
                source_instance=self.source_instance, peer_ip=peer, transport=transport,
                timezone_name=self.timezone_name, body_base64=self.body_base64, received_at=received_at))
            try:
                await asyncio.shield(committing)
            except asyncio.CancelledError:
                await committing
                raise
        except Exception:
            self.counters["dropped"] += 1
            logger.error("Syslog durable inbox unavailable peer=%s transport=%s", peer, transport)
            raise
        self.counters["received"] += 1

    async def _receive_datagram(self):
        loop = asyncio.get_running_loop()
        if not self._udp_use_reader:
            try:
                return await loop.sock_recvfrom(self.udp_socket, 65535)
            except NotImplementedError:
                # uvloop exposes sock_recvfrom but raises NotImplementedError (including 0.22).
                # Use its supported fd reader API; keep native recvfrom on Proactor/asyncio.
                self._udp_use_reader = True
                logging.getLogger("uvicorn.error").warning(
                    "%s Syslog UDP using fd reader: %s.sock_recvfrom is unavailable",
                    self.product.upper(), type(loop).__module__ + "." + type(loop).__name__)
        ready = loop.create_future()
        fd = self.udp_socket.fileno()

        def readable():
            if ready.done():
                return
            try:
                result = self.udp_socket.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except Exception as exc:
                ready.set_exception(exc)
            else:
                ready.set_result(result)

        loop.add_reader(fd, readable)
        try:
            return await ready
        finally:
            # Always unregister before close/rebind, including cancellation while idle.
            loop.remove_reader(fd)

    def _udp_finished(self, task):
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None or not self.closing:
            logging.getLogger("uvicorn.error").error(
                "%s Syslog UDP receive task stopped bind=%s:%d error_type=%s; UDP socket closed",
                self.product.upper(), self.host, self.port,
                type(failure).__name__ if failure is not None else "unexpected_exit",
                exc_info=(type(failure), failure, failure.__traceback__) if failure is not None else None)
            if self.udp_socket:
                self.udp_socket.close()

    async def _udp(self):
        while True:
            # Entire UDP payload, never recvfrom(1024) truncation. max_bytes is checked after receipt.
            try:
                raw, peer = await self._receive_datagram()
            except OSError:
                self.counters["dropped"] += 1
                logger.error("Syslog UDP receive error")
                await asyncio.sleep(0.1)
                continue
            try:
                await self.accept(raw, peer[0], "UDP")
            except Exception:
                await asyncio.sleep(0.1)

    async def _tcp(self, reader, writer):
        task = asyncio.current_task()
        peer = writer.get_extra_info("peername")[0]
        if self.closing or not self.permits(peer) or len(self.clients) >= self.max_connections:
            self.counters["rejected"] += 1
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            return
        self.clients.add(task)
        self.writers.add(writer)
        framer = TCPFramer(self.max_bytes, self.framing)
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), timeout=self.idle_timeout)
                if not data:
                    framer.finish()
                    break
                for message in framer.feed(data):
                    await self.accept(message, peer, "TCP")
        except (ProtocolError, TimeoutError):
            self.counters["framing_errors"] += 1
            logger.warning("Syslog TCP framing error/timeout peer=%s buffered=%d", peer, len(framer.buffer))
        except (ConnectionError, OSError):
            self.counters["dropped"] += 1
        except Exception:
            logger.error("Syslog TCP inbox failure; closing peer=%s", peer)
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.clients.discard(task)

    async def start(self):
        self.closing = False
        self._udp_use_reader = False
        # Numeric bind addresses avoid ambiguous IPv4/IPv6 port allocation.
        address = ipaddress.ip_address(self.host)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        try:
            if self.protocols in ("both", "tcp"):
                self.server = await asyncio.start_server(self._tcp, host=self.host, port=self.port, family=family)
                self.port = self.server.sockets[0].getsockname()[1]
            if self.protocols in ("both", "udp"):
                self.udp_socket = socket.socket(family, socket.SOCK_DGRAM)
                self.udp_socket.setblocking(False)
                self.udp_socket.bind((self.host, self.port))
                self.port = self.udp_socket.getsockname()[1]
                self.udp_task = asyncio.create_task(self._udp())
                self.udp_task.add_done_callback(self._udp_finished)
                # Exercise the receive API before reporting readiness, not merely the bind.
                await asyncio.sleep(0)
                if self.udp_task.done():
                    self.udp_task.result()
        except BaseException:
            await self.close()
            raise
        logger.info("Syslog listening product=%s source=%s bind=%s:%d protocols=%s",
                    self.product, self.source_instance, self.host, self.port, self.protocols)
        return self

    async def close(self):
        self.closing = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.udp_task:
            self.udp_task.cancel()
            # A failed task has already been reported; still close sockets and TCP clients.
            await asyncio.gather(self.udp_task, return_exceptions=True)
        if self.udp_socket:
            self.udp_socket.close()
        # Wait for accepted inbox writes. Do not cancel a to_thread commit and close SQLite beneath it.
        for writer in list(self.writers):
            writer.close()
        if self.clients:
            await asyncio.gather(*list(self.clients), return_exceptions=True)


async def consume(receiver: Receiver, sink):
    worker = None
    stop = threading.Event()
    try:
        while True:
            worker = asyncio.create_task(asyncio.to_thread(drain_once, receiver.inbox, sink, stop=stop))
            try:
                result = await asyncio.shield(worker)
            except Exception as exc:
                logger.error("Syslog inbox processing failed source=%s error_type=%s; retrying",
                             receiver.source_instance, type(exc).__name__)
                worker = None
                await asyncio.sleep(3)
                continue
            worker = None
            logger.info("Syslog receiver=%s inbox=%s writes=%s", receiver.counters, receiver.inbox.status(), result)
            await asyncio.sleep(3 if result["failed"] else 0.25 if result["stored"] else 1)
    finally:
        stop.set()
        if worker:
            with suppress(Exception):
                await worker


async def serve(receiver: Receiver, sink):
    await receiver.start()
    try:
        await consume(receiver, sink)
    finally:
        await receiver.close()


def create_store_engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True, pool_recycle=1800, pool_size=2, max_overflow=0,
                         connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10})


def inbox_path(product: str, source_instance: str, directory: Path | str = "data/syslog") -> Path:
    directory = Path(directory)
    if not directory.is_absolute():
        directory = ROOT / directory
    return directory / (product + "-" + source_instance.replace(":", "%3A") + ".sqlite3")


@asynccontextmanager
async def application_receivers(settings):
    """Bind before application readiness; release every socket/inbox on failure, exit or reload."""
    products = [product for product in ("waf", "das")
                if settings.syslog_enabled and getattr(settings, product + "_syslog_enabled")]
    if not products:
        yield ()
        return
    engine = create_store_engine(settings.log_fusion_url)
    inboxes, receivers, tasks = [], [], []
    try:
        sink = MySQLDeviceStore(engine)
        for product in products:
            prefix = product + "_syslog_"
            source = getattr(settings, prefix + "source_instance")
            inbox = Inbox(inbox_path(product, source, settings.syslog_spool_dir), settings.syslog_max_spool_bytes)
            inboxes.append(inbox)
            receiver = Receiver(
                inbox, product=product, source_instance=source,
                host=settings.syslog_host, port=getattr(settings, prefix + "port"),
                protocols=getattr(settings, prefix + "protocols"),
                timezone_name=getattr(settings, prefix + "timezone"),
                body_base64=getattr(settings, prefix + "body_base64"),
                framing=getattr(settings, prefix + "framing"),
                allow_sources=tuple(getattr(settings, prefix + "allow_sources")),
                max_bytes=settings.syslog_max_bytes, idle_timeout=settings.syslog_idle_timeout,
                max_connections=settings.syslog_max_connections)
            receivers.append(receiver)
            try:
                await receiver.start()
                logging.getLogger("uvicorn.error").info(
                    "%s Syslog listening on %s:%d (%s)",
                    product.upper(), receiver.host, receiver.port, receiver.protocols)
            except OSError as exc:
                logger.error("Syslog startup failed product=%s bind=%s:%s; check port/conflicting standalone receiver",
                             product, receiver.host, receiver.port)
                raise RuntimeError(f"{product.upper()} Syslog cannot bind {receiver.host}:{receiver.port}") from exc
        tasks = [asyncio.create_task(consume(receiver, sink), name="syslog-" + receiver.product)
                 for receiver in receivers]
        yield tuple(receivers)
    finally:
        # Stop accepting first, finish accepted local commits, then finish in-flight remote writes.
        closed = await asyncio.gather(*(receiver.close() for receiver in receivers), return_exceptions=True)
        for failure in closed:
            if isinstance(failure, BaseException):
                logger.error("Syslog listener cleanup failed error_type=%s", type(failure).__name__)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for inbox in inboxes:
            inbox.close()
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("waf", "das"), required=True)
    parser.add_argument("--source-instance", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5514)
    parser.add_argument("--protocols", choices=("udp", "tcp", "both"), default="both")
    parser.add_argument("--timezone", default="+08:00")
    parser.add_argument("--body-base64", action="store_true")
    parser.add_argument("--framing", choices=("auto", "line", "octet"), default="auto")
    parser.add_argument("--max-bytes", type=int, default=1024**2)
    parser.add_argument("--max-spool-bytes", type=int, default=1024**3)
    parser.add_argument("--idle-timeout", type=float, default=60)
    parser.add_argument("--max-connections", type=int, default=64)
    parser.add_argument("--allow-source", action="append", default=[])
    parser.add_argument("--spool", type=Path)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-quarantine", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", args.source_instance):
        parser.error("invalid --source-instance")
    # Colons are valid instance identifiers but invalid in Windows filenames.
    path = args.spool or inbox_path(args.product, args.source_instance)
    if not path.is_absolute():
        path = ROOT / path
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    inbox = Inbox(path, args.max_spool_bytes)
    try:
        if args.retry_quarantine:
            inbox.retry_quarantine()
        if args.status:
            print(json.dumps(inbox.status(), ensure_ascii=False))
            return 0
        receiver = Receiver(inbox, product=args.product, source_instance=args.source_instance,
                            host=args.host, port=args.port, protocols=args.protocols, timezone_name=args.timezone,
                            body_base64=args.body_base64, framing=args.framing, max_bytes=args.max_bytes,
                            allow_sources=tuple(args.allow_source), idle_timeout=args.idle_timeout,
                            max_connections=args.max_connections)
        from .config import get_settings

        engine = create_store_engine(get_settings().log_fusion_url)
        sink = MySQLDeviceStore(engine)
        try:
            if args.replay_only:
                while inbox.status()["pending"]:
                    result = drain_once(inbox, sink)
                    if result["failed"]:
                        return 1
                return 0
            try:
                def terminate(signum, frame):
                    raise KeyboardInterrupt

                previous = signal.signal(signal.SIGTERM, terminate)
                try:
                    asyncio.run(serve(receiver, sink))
                finally:
                    signal.signal(signal.SIGTERM, previous)
            except KeyboardInterrupt:
                pass
        finally:
            engine.dispose()
    finally:
        inbox.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
