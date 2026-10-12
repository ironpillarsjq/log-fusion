from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "Log Fusion"
    db_host: str = "100.107.214.72"
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
