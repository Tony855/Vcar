import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    passenger_bot_token: str
    driver_bot_token: str
    database_url: str
    secret_key: str
    redis_url: str
    admin_username: str
    admin_password_hash: str
    cors_origins: tuple[str, ...]
    osrm_base_url: str
    osrm_profile: str
    osrm_enabled: bool
    osrm_timeout_seconds: float
    osrm_max_table_coordinates: int
    order_event_outbox_poll_interval: float
    metrics_bearer_token: str

    @classmethod
    def from_env(cls):
        origins = tuple(
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        return cls(
            passenger_bot_token=os.getenv("PASSENGER_BOT_TOKEN", ""),
            driver_bot_token=os.getenv("DRIVER_BOT_TOKEN", ""),
            database_url=os.getenv("DATABASE_URL", "").strip(),
            secret_key=os.getenv("SECRET_KEY", ""),
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            admin_username=os.getenv("ADMIN_USERNAME", "admin"),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", ""),
            cors_origins=origins,
            osrm_base_url=os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5000").rstrip("/"),
            osrm_profile=os.getenv("OSRM_PROFILE", "driving"),
            osrm_enabled=os.getenv("OSRM_ENABLED", "false").lower() in ("1", "true", "yes", "on"),
            osrm_timeout_seconds=float(os.getenv("OSRM_TIMEOUT_SECONDS", "8")),
            osrm_max_table_coordinates=int(os.getenv("OSRM_MAX_TABLE_COORDINATES", "100")),
            order_event_outbox_poll_interval=max(0.1, float(os.getenv("ORDER_EVENT_OUTBOX_POLL_INTERVAL", "1.0"))),
            metrics_bearer_token=os.getenv("METRICS_BEARER_TOKEN", "").strip(),
        )


settings = Settings.from_env()
