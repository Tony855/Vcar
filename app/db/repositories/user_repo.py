from app.db.core import db
class UserRepository:
    @staticmethod
    async def get_or_create(tid, name=None, username=None):
        async with db.pool.acquire() as c:
            return dict(await c.fetchrow("INSERT INTO users (telegram_id, first_name, username) VALUES ($1,$2,$3) ON CONFLICT (telegram_id) DO UPDATE SET first_name=EXCLUDED.first_name RETURNING *", tid, name, username))

    @staticmethod
    async def update_driver_info(tid, phone, plate, car_pic, lic_pic, drv_pic):
        async with db.pool.acquire() as c:
            await c.execute('UPDATE users SET phone=$1, plate_number=$2, car_photo=$3, license_photo=$4, driving_license_photo=$5, driver_status=\'PENDING\' WHERE telegram_id=$6', phone, plate, car_pic, lic_pic, drv_pic, tid)

    @staticmethod
    async def get_drivers_info(tids: list[int]):
        if not tids: return {}
        async with db.pool.acquire() as c:
            rows = await c.fetch("SELECT telegram_id, rating FROM users WHERE telegram_id = ANY($1)", tids)
            return {r['telegram_id']: float(r['rating'] or 5.0) for r in rows}

    @staticmethod
    async def update_user_rating(tid: int, new_score: int):
        async with db.pool.acquire() as c:
            row = await c.fetchrow("SELECT rating, rating_count FROM users WHERE telegram_id=$1", tid)
            if not row: return
            curr_rating = float(row['rating'] or 5.0)
            curr_count = int(row['rating_count'] or 1)
            new_rating = round((curr_rating * curr_count + new_score) / (curr_count + 1), 2)
            await c.execute("UPDATE users SET rating=$1, rating_count=$2 WHERE telegram_id=$3", 
                            new_rating, curr_count + 1, tid)
