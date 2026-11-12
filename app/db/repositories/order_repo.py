import json
from decimal import Decimal, InvalidOperation

from app.db.core import db
from app.modules.orders.state_machine import validate_transition, InvalidOrderTransition


class OrderRepository:
    @staticmethod
    async def _append_event(
        connection, order_id, from_status, to_status,
        actor_user_id=None, actor_role="SYSTEM", metadata=None,
    ):
        event = await connection.fetchrow(
            """
            INSERT INTO order_events
                (order_id, actor_user_id, actor_role, from_status, to_status, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, created_at
            """,
            order_id,
            actor_user_id,
            actor_role,
            from_status,
            to_status,
            json.dumps(metadata or {}),
        )
        # The outbox row shares the caller's transaction with the state change
        # and event history, so a committed transition is always replayable.
        await connection.execute(
            """
            INSERT INTO order_event_outbox (event_id, order_id, payload)
            VALUES ($1, $2, $3::jsonb)
            """,
            event["id"],
            order_id,
            json.dumps({
                "event_id": event["id"],
                "order_id": order_id,
                "from_status": from_status,
                "status": to_status,
                "actor_role": actor_role,
                "created_at": event["created_at"].isoformat(),
            }),
        )

    @classmethod
    async def claim_status_events(cls, limit=100):
        """Claim undelivered status events for a dispatcher using SKIP LOCKED."""
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            raise ValueError("outbox claim limit must be an integer between 1 and 500")
        batch_size = limit
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    WITH candidates AS (
                        SELECT id FROM order_event_outbox
                        WHERE delivered_at IS NULL
                          AND available_at <= now()
                          AND (locked_at IS NULL OR locked_at < now() - interval '5 minutes')
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT $1
                    )
                    UPDATE order_event_outbox AS outbox
                    SET locked_at=now(), lock_token=gen_random_uuid(), attempts=attempts+1
                    FROM candidates
                    WHERE outbox.id=candidates.id
                    RETURNING outbox.id, outbox.event_id, outbox.order_id, outbox.payload, outbox.attempts, outbox.lock_token
                    """,
                    batch_size,
                )
            return [dict(row) for row in rows]

    @classmethod
    async def mark_status_event_delivered(cls, outbox_id, lock_token):
        async with db.pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE order_event_outbox
                SET delivered_at=now(), locked_at=NULL, lock_token=NULL, last_error=NULL
                WHERE id=$1 AND lock_token=$2 AND delivered_at IS NULL
                """,
                outbox_id,
                lock_token,
            )
            return result == "UPDATE 1"

    @classmethod
    async def retry_status_event(cls, outbox_id, lock_token, error):
        """Release a failed claim with capped exponential backoff."""
        safe_error = str(error).replace("\x00", "")[:1000]
        async with db.pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE order_event_outbox
                SET locked_at=NULL, lock_token=NULL,
                    available_at=now() + LEAST(interval '15 minutes',
                        interval '5 seconds' * power(2, LEAST(attempts, 8))),
                    last_error=$2
                WHERE id=$1 AND lock_token=$3 AND delivered_at IS NULL
                """,
                outbox_id,
                safe_error,
                lock_token,
            )
            return result == "UPDATE 1"

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
        bigint_max = 2**63 - 1
        if (
            not isinstance(oid, int) or isinstance(oid, bool)
            or not 0 < oid <= bigint_max
            or not isinstance(did, int) or isinstance(did, bool)
            or not 0 < did <= bigint_max
        ):
            return False
        async with db.pool.acquire() as connection:
            async with connection.transaction():
                # Serialize competing accept attempts made by the same driver.
                driver = await connection.fetchrow(
                    "SELECT id FROM users WHERE id=$1 AND driver_status='APPROVED' AND driver_is_online IS TRUE FOR UPDATE",
                    did,
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
        # Reject malformed database identifiers before binding them to BIGINT parameters.
        bigint_max = 2**63 - 1
        if (
            not isinstance(oid, int) or isinstance(oid, bool)
            or not 0 < oid <= bigint_max
            or actor_role not in {"SYSTEM", "DRIVER", "PASSENGER"}
        ):
            return False
        # Human actors must be bound to the order participant recorded in PostgreSQL.
        # SYSTEM is reserved for trusted internal transitions without a user identity.
        if actor_role == "SYSTEM":
            if actor_user_id is not None:
                return False
        elif (
            not isinstance(actor_user_id, int)
            or isinstance(actor_user_id, bool)
            or not 0 < actor_user_id <= bigint_max
        ):
            return False

        async with db.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE orders SET status=$1
                    WHERE id=$2 AND status=$3
                      AND (
                        ($5='SYSTEM' AND $4::bigint IS NULL)
                        OR ($5='DRIVER' AND driver_id=$4)
                        OR ($5='PASSENGER' AND customer_id=$4)
                      )
                    RETURNING id
                    """,
                    new_status, oid, expected_status, actor_user_id, actor_role,
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
        # The actor role is part of the authorization boundary: being either
        # participant is not sufficient to claim the other participant's role.
        bigint_max = 2**63 - 1
        if (
            role not in {"PASSENGER", "DRIVER"}
            or not isinstance(oid, int)
            or isinstance(oid, bool)
            or not 0 < oid <= bigint_max
            or not isinstance(uid, int)
            or isinstance(uid, bool)
            or not 0 < uid <= bigint_max
        ):
            return False
        if isinstance(fee, bool) or not isinstance(fee, (int, float, Decimal)):
            return False
        try:
            fee_decimal = fee if isinstance(fee, Decimal) else Decimal(str(fee))
            if not fee_decimal.is_finite() or fee_decimal < 0:
                return False
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return False

        async with db.pool.acquire() as connection:
            async with connection.transaction():
                order = await connection.fetchrow(
                    """
                    SELECT status FROM orders
                    WHERE id=$1
                      AND (
                        ($3='PASSENGER' AND customer_id=$2)
                        OR ($3='DRIVER' AND driver_id=$2)
                      )
                    FOR UPDATE
                    """,
                    oid, uid, role,
                )
                if not order or order["status"] not in ("DISPATCHING", "ACCEPTED", "ARRIVED"):
                    return False
                current_status = order["status"]
                validate_transition(current_status, "CANCELLED")
                # Apply the existing dispatch policy using the locked status, not
                # a status fetched by the caller before this transaction began.
                effective_fee = fee_decimal if current_status in ("ACCEPTED", "ARRIVED") else Decimal("0")
                row = await connection.fetchrow(
                    """
                    UPDATE orders
                    SET status='CANCELLED', cancel_by=$1, cancel_fee=$2
                    WHERE id=$3 AND status=$4
                    RETURNING id
                    """,
                    role, effective_fee, oid, current_status,
                )
                if not row:
                    return False
                await cls._append_event(
                    connection, oid, current_status, "CANCELLED",
                    actor_user_id=uid, actor_role=role,
                    # JSON has no Decimal type; preserve the exact decimal text
                    # rather than silently rounding through binary float.
                    metadata={"cancel_fee": format(effective_fee, "f")},
                )
            return True


    @classmethod
    async def submit_rating(cls, oid, actor_user_id, actor_role, score):
        """Record one authenticated rating per order participant, atomically."""
        if (
            actor_role not in {"PASSENGER", "DRIVER"}
            or not isinstance(actor_user_id, int)
            or isinstance(actor_user_id, bool)
            or not 0 < actor_user_id <= 2**63 - 1
            or not isinstance(oid, int)
            or isinstance(oid, bool)
            or not 0 < oid <= 2**63 - 1
            or not isinstance(score, int)
            or isinstance(score, bool)
            or not 1 <= score <= 5
        ):
            return False

        async with db.pool.acquire() as connection:
            async with connection.transaction():
                order = await connection.fetchrow(
                    """
                    SELECT customer_id, driver_id, status
                    FROM orders WHERE id=$1 FOR UPDATE
                    """,
                    oid,
                )
                if not order or order["status"] != "COMPLETED":
                    return False
                if actor_role == "PASSENGER":
                    if order["customer_id"] != actor_user_id:
                        return False
                    ratee_user_id = order["driver_id"]
                else:
                    if order["driver_id"] != actor_user_id:
                        return False
                    ratee_user_id = order["customer_id"]
                if ratee_user_id is None or ratee_user_id == actor_user_id:
                    return False

                inserted = await connection.fetchrow(
                    """
                    INSERT INTO order_ratings
                        (order_id, rater_role, rater_user_id, ratee_user_id, score)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (order_id, rater_role) DO NOTHING
                    RETURNING id
                    """,
                    oid, actor_role, actor_user_id, ratee_user_id, score,
                )
                if not inserted:
                    return False

                await connection.execute(
                    """
                    UPDATE users
                    SET rating = round(
                            (COALESCE(rating, 5.0) * GREATEST(COALESCE(rating_count, 1), 1) + $1)
                            / (GREATEST(COALESCE(rating_count, 1), 1) + 1),
                            2
                        ),
                        rating_count = GREATEST(COALESCE(rating_count, 1), 1) + 1
                    WHERE id=$2
                    """,
                    score, ratee_user_id,
                )

                # Keep the legacy driver-review projection in the same transaction.
                # The reviews table represents passenger feedback about a driver;
                # driver-to-passenger scores remain in the directional table only.
                if actor_role == "PASSENGER":
                    existing_review = await connection.fetchval(
                        "SELECT id FROM reviews WHERE order_id=$1 AND driver_id=$2",
                        oid, ratee_user_id,
                    )
                    if existing_review:
                        await connection.execute(
                            "UPDATE reviews SET rating=$1, passenger_id=$2 WHERE id=$3",
                            score, actor_user_id, existing_review,
                        )
                    else:
                        await connection.execute(
                            "INSERT INTO reviews (order_id, driver_id, passenger_id, rating) "
                            "VALUES ($1, $2, $3, $4)",
                            oid, ratee_user_id, actor_user_id, score,
                        )
                return True

    @classmethod
    async def add_rating_comment(cls, oid, passenger_user_id, comment):
        """Attach a bounded comment only to the passenger's own completed-order rating."""
        if (
            not isinstance(oid, int) or isinstance(oid, bool) or not 0 < oid <= 2**63 - 1
            or not isinstance(passenger_user_id, int)
            or isinstance(passenger_user_id, bool) or not 0 < passenger_user_id <= 2**63 - 1
            or not isinstance(comment, str) or not comment.strip()
            or len(comment) > 1000
        ):
            return False
        async with db.pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE order_ratings AS rating
                SET comment=$1
                FROM orders AS order_row
                WHERE rating.order_id=$2
                  AND rating.rater_role='PASSENGER'
                  AND rating.rater_user_id=$3
                  AND order_row.id=rating.order_id
                  AND order_row.customer_id=$3
                  AND order_row.status='COMPLETED'
                  AND (rating.comment IS NULL OR rating.comment='')
                """,
                comment.strip(), oid, passenger_user_id,
            )
            return result == "UPDATE 1"

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
