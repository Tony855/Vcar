import asyncpg
import redis.asyncio as redis
from app.core.config import settings
class DB:
    def __init__(self): self.pool = None; self.redis = None
    async def init(self):
        self.pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        self.redis = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
db = DB()
