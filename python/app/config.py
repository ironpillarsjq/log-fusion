from functools import lru_cache
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
