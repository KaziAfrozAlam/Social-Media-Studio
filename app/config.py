import os
from dataclasses import dataclass
from pathlib import Path

import dotenv

from app import BASE_DIR

dotenv.load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    publishers: str
    poll_seconds: int
    crash_after: int
    crash_recovery_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///data/social_studio.db"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            publishers=os.getenv(
                "PUBLISHERS",
                "telegram=TelegramPublisher,x=MockXPublisher,linkedin=MockLinkedInPublisher",
            ),
            poll_seconds=int(os.getenv("SCHEDULER_POLL_SECONDS", "5")),
            crash_after=int(os.getenv("SCHEDULER_CRASH_AFTER", "0")),
            crash_recovery_seconds=int(
                os.getenv("SCHEDULER_CRASH_RECOVERY_SECONDS", "300")
            ),
        )


def resolve_db_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        path = database_url[len(prefix):]
        return Path(path).resolve()
    return Path("data") / "social_studio.db"


_cached_settings: Settings | None = None


def get_settings() -> Settings:
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings.from_env()
    return _cached_settings


def reset_settings_cache():
    global _cached_settings
    _cached_settings = None