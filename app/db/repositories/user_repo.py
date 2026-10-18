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
