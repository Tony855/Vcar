import os
from dataclasses import dataclass
@dataclass(frozen=True)
class Settings:
    passenger_bot_token: str
    driver_bot_token: str
    database_url: str
    secret_key: str
    @classmethod
    def from_env(cls):
        return cls(
            passenger_bot_token=os.getenv("PASSENGER_BOT_TOKEN", ""),
            driver_bot_token=os.getenv("DRIVER_BOT_TOKEN", ""),
            database_url=os.getenv("DATABASE_URL", "postgresql://vcar:vcar@127.0.0.1:5432/vcar"),
            secret_key=os.getenv("SECRET_KEY", "vcar-super-secret-key-2026")
        )
settings = Settings.from_env()
