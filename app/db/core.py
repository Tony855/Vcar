import asyncpg
import redis.asyncio as redis
from app.core.config import settings

async def init_connection(conn):
    # 强制将数据库的 NUMERIC 类型转换为 Python 的 float，防止类型冲突
    await conn.set_type_codec(
        'numeric',
        encoder=str,
        decoder=float,
        schema='pg_catalog'
    )

class DB:
    def __init__(self): self.pool = None; self.redis = None
    async def init(self):
        self.pool = await asyncpg.create_pool(
            settings.database_url, 
            min_size=2, 
            max_size=10, 
            init=init_connection # 注入初始化转换器
        )
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)
db = DB()
