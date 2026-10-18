from app.db.core import db
class UserRepository:
    @staticmethod
    async def get_or_create(tid, name=None, username=None):
        async with db.pool.acquire() as c:
            return dict(await c.fetchrow("INSERT INTO users (telegram_id, first_name, username) VALUES ($1,$2,$3) ON CONFLICT (telegram_id) DO UPDATE SET first_name=EXCLUDED.first_name RETURNING *", tid, name, username))
