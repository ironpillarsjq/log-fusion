from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, func
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