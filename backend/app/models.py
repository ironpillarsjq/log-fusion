from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class ClientStatus(Base):
    """客户端心跳状态表（log_fusion 库）。状态阈值：10s 在线 / 30s 延迟 / 其余离线。"""

    __tablename__ = "client_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    system: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    latency: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OFFLINE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LogHash(Base):
    """原始日志存证表（log_hash 库）。

    每条采集到的日志在写入业务表之前，先按 `timestamp + 0x1F + log` 计算 SHA-256，
    并把 log 原文与摘要一起保存，用于事后防篡改校验。

    - `log_hash` 唯一：同一 `(timestamp, log)` 重复上报（Fluent Bit 重试）只留一条存证。
    - `log_timestamp` 保存参与哈希的**原始时间字符串**，与 `event_time`（解析后的
      UTC 时间，不参与哈希）分开，保证摘要可以原样复算。
    """

    __tablename__ = "log_hash"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    log_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    raw_log: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    log_timestamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ArchiveLog(Base):
    """存证归档台账（log_fusion 库）。

    每次导出并删除 `log_hash` 记录后写一条，记录归档产物与范围。删除是不可逆的，
    台账是事后唯一能追溯「哪些记录在什么时候被导出到哪里」的依据。

    台账写入失败不影响已经完成的导出与删除（见 `archive.py`）。
    """

    __tablename__ = "archive_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # auto / manual
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    deleted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    range_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    range_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    before_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)