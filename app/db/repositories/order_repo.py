import json

from app.db.core import db
from app.modules.orders.state_machine import validate_transition, InvalidOrderTransition


class OrderRepository:
    @staticmethod
    async def _append_event(
        connection, order_id, from_status, to_status,
        actor_user_id=None, actor_role="SYSTEM", metadata=None,
    ):
        await connection.execute(
            """
            INSERT INTO order_events
                (order_id, actor_user_id, actor_role, from_status, to_status, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            order_id,
            actor_user_id,
            actor_role,
            from_status,
            to_status,
            json.dumps(metadata or {}),
        )

    @classmethod
    async def create(cls, cid, plng, plat, dlng, dlat, dist, price):
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO orders
                        (customer_id, pickup_lng, pickup_lat, dropoff_lng, dropoff_lat,
                         distance_km, price_estimate, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'DISPATCHING')
                    RETURNING id
                    """,
                    cid, plng, plat, dlng, dlat, dist, price,
                )
                await cls._append_event(
                    connection, row["id"], None, "DISPATCHING",
                    actor_user_id=cid, actor_role="PASSENGER",
                )
                return dict(row)

    @staticmethod
    async def get(oid):
        async with db.pool.acquire() as c:
            row = await c.fetchrow(
                """
                SELECT o.*, cu.telegram_id AS c_tg, cu.locale AS c_locale,
                       cu.first_name AS c_name, du.telegram_id AS d_tg,
                       du.locale AS d_locale, du.first_name AS d_name,
                       du.phone AS d_phone, du.plate_number AS d_plate,
                       du.car_photo AS d_car_photo
                FROM orders o
                LEFT JOIN users cu ON o.customer_id=cu.id
                LEFT JOIN users du ON o.driver_id=du.id
                WHERE o.id=$1
                """,
                oid,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_active_order_by_user(uid):
        async with db.pool.acquire() as c:
            row = await c.fetchrow(
                """
                SELECT o.*, c.telegram_id AS c_tg, d.telegram_id AS d_tg
                FROM orders o
                LEFT JOIN users c ON o.customer_id = c.id
                LEFT JOIN users d ON o.driver_id = d.id
                WHERE (o.customer_id = $1 OR o.driver_id = $1)
                  AND status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')
                ORDER BY o.id DESC LIMIT 1
                """,
                uid,
            )
            return dict(row) if row else None

    @classmethod
    async def accept(cls, oid, did):
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                # Serialize competing accept attempts made by the same driver.
                driver = await connection.fetchrow(
                    "SELECT id FROM users WHERE id=$1 FOR UPDATE", did
                )
                if not driver:
                    return False
                busy = await connection.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM orders
                        WHERE driver_id=$1 AND status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')
                    )
                    """,
                    did,
                )
                if busy:
                    return False
                row = await connection.fetchrow(
                    """
                    UPDATE orders
                    SET driver_id=$1, status='ACCEPTED'
                    WHERE id=$2 AND status='DISPATCHING' AND driver_id IS NULL
                    RETURNING id
                    """,
                    did,
                    oid,
                )
                if not row:
                    return False
                await cls._append_event(
                    connection, oid, "DISPATCHING", "ACCEPTED",
                    actor_user_id=did, actor_role="DRIVER",
                )
                return True

    @classmethod
    async def update_status(cls, oid, new_status, expected_status, actor_user_id=None, actor_role="SYSTEM"):
        validate_transition(expected_status, new_status)
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE orders SET status=$1
                    WHERE id=$2 AND status=$3
                    RETURNING id
                    """,
                    new_status, oid, expected_status,
                )
                if not row:
                    return False
                await cls._append_event(
                    connection, oid, expected_status, new_status,
                    actor_user_id=actor_user_id, actor_role=actor_role,
                )
                return True

    @classmethod
    async def cancel_with_fee(cls, oid, uid, role, fee=0.0):
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                order = await connection.fetchrow(
                    """
                    SELECT status FROM orders
                    WHERE id=$1 AND (customer_id=$2 OR driver_id=$2)
                    FOR UPDATE
                    """,
                    oid, uid,
                )
                if not order or order["status"] not in ("DISPATCHING", "ACCEPTED", "ARRIVED"):
                    return False
                current_status = order["status"]
                validate_transition(current_status, "CANCELLED")
                row = await connection.fetchrow(
                    """
                    UPDATE orders
                    SET status='CANCELLED', cancel_by=$1, cancel_fee=$2
                    WHERE id=$3 AND status=$4
                    RETURNING id
                    """,
                    role, fee, oid, current_status,
                )
                if not row:
                    return False
                await cls._append_event(
                    connection, oid, current_status, "CANCELLED",
                    actor_user_id=uid, actor_role=role,
                    metadata={"cancel_fee": float(fee)},
                )
                return True

    @staticmethod
    async def get_customer_orders(cid):
        async with db.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, status, price_estimate FROM orders WHERE customer_id=$1 ORDER BY id DESC LIMIT 5",
                cid,
            )
            return [dict(r) for r in rows]

    @staticmethod
    async def get_driver_orders(did):
        async with db.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, status, price_estimate FROM orders WHERE driver_id=$1 ORDER BY id DESC LIMIT 5",
                did,
            )
            return [dict(r) for r in rows]
