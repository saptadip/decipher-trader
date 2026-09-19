from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    operator_token: str = Field(default="dev-token", alias="OPERATOR_TOKEN")
    db_path: Path = Field(default=Path("/app/data/decipher.sqlite3"), alias="CONTROL_PLANE_DB_PATH")
    sqlite_wal: bool = Field(default=True, alias="CONTROL_PLANE_SQLITE_WAL")
    paper_forward_min_days: int = Field(default=14, alias="PAPER_FORWARD_MIN_DAYS")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")


def get_settings() -> Settings:
    return Settings()
