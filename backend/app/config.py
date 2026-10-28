from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "Log Fusion"
    db_host: str = "192.168.5.6"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "mysql_YbJWfE"
    linux_db: str = "linux_logs"
    windows_db: str = "windows_logs"
    log_fusion_db: str = "log_fusion"
    # 原始日志存证库/表：保存 log 原文 + SHA-256 摘要（见 docs/DATABASE.md）
    log_hash_db: str = "log_hash"
    log_hash_table: str = "log_hash"
    raw_storage_dir: str = "data/raw"
    # 存证归档目录：自动/手动导出的 CSV 永久保留在这里，不做自动清理
    archive_dir: str = "data/archive"
    # 自动归档：超过该月数的存证（按 created_at 算）会被导出并删除；<=0 表示不启用
    archive_retention_months: int = 3
    # 每次归档最多处理的行数，避免单次把整表拉进内存
    archive_batch_limit: int = 200000
    # Startup polls recent logs only; historical imports require the explicit CLI.
    security_sync_enabled: bool = True
    security_source_instance: str = "nsfocus-site-a"
    security_config_file: str = "security-log-client/config/connection.json"
    security_poll_seconds: int = 30
    security_history_months: int = 3
    security_page_size: int = 10000
    security_overlap_seconds: int = 120
    security_live_window_seconds: int = 600
    security_lag_seconds: int = 30
    security_timeout_seconds: int = 90
    # Started by the ASGI worker lifespan, including run.py/reload; no sockets at import time.
    syslog_enabled: bool = True
    syslog_host: str = "0.0.0.0"
    syslog_spool_dir: str = "data/syslog"
    syslog_max_bytes: int = Field(1048576, ge=1)
    syslog_max_spool_bytes: int = Field(1073741824, ge=1)
    syslog_idle_timeout: float = Field(60, gt=0)
    syslog_max_connections: int = Field(64, ge=1)
    waf_syslog_enabled: bool = True
    waf_syslog_port: int = Field(5514, ge=1, le=65535)
    waf_syslog_source_instance: str = Field("waf-site-a", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    waf_syslog_protocols: Literal["udp", "tcp", "both"] = "both"
    waf_syslog_timezone: str = "+08:00"
    waf_syslog_framing: Literal["auto", "line", "octet"] = "auto"
    waf_syslog_body_base64: bool = False
    waf_syslog_allow_sources: list[str] = Field(default_factory=list)
    das_syslog_enabled: bool = True
    das_syslog_port: int = Field(5515, ge=1, le=65535)
    das_syslog_source_instance: str = Field("das-site-a", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    das_syslog_protocols: Literal["udp", "tcp", "both"] = "both"
    das_syslog_timezone: str = "+08:00"
    das_syslog_framing: Literal["auto", "line", "octet"] = "auto"
    das_syslog_body_base64: bool = False
    das_syslog_allow_sources: list[str] = Field(default_factory=list)

    @property
    def mysql_base(self) -> str:
        return f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}"

    @property
    def linux_url(self) -> str:
        return f"{self.mysql_base}/{self.linux_db}?charset=utf8mb4"

    @property
    def windows_url(self) -> str:
        return f"{self.mysql_base}/{self.windows_db}?charset=utf8mb4"

    @property
    def log_fusion_url(self) -> str:
        return f"{self.mysql_base}/{self.log_fusion_db}?charset=utf8mb4"

    @property
    def log_hash_url(self) -> str:
        return f"{self.mysql_base}/{self.log_hash_db}?charset=utf8mb4"


@lru_cache
def get_settings() -> Settings:
    return Settings()
