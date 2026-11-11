import asyncpg
import redis.asyncio as redis
from app.core.config import settings


async def init_connection(conn):
    # Keep NUMERIC decoding consistent with the existing application models.
    await conn.set_type_codec(
        "numeric",
        encoder=str,
        decoder=float,
        schema="pg_catalog",
    )


class DB:
    def __init__(self):
        self.pool = None
        self.redis = None

    async def init(self):
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL must be configured; insecure built-in database credentials are disabled"
            )
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                settings.database_url,
                min_size=2,
                max_size=10,
                init=init_connection,
            )
        if self.redis is None:
            self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def close(self):
        """Close application connections cleanly; safe after partial startup."""
        pool, self.pool = self.pool, None
        redis_client, self.redis = self.redis, None
        if pool is not None:
            await pool.close()
        if redis_client is not None:
            await redis_client.aclose()


db = DB()
