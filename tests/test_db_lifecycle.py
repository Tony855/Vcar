import asyncio

import pytest

from app.db.core import DB


class FakePool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeRedis:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_db_close_releases_pool_and_redis():
    instance = DB()
    pool = FakePool()
    redis_client = FakeRedis()
    instance.pool = pool
    instance.redis = redis_client

    await instance.close()

    assert pool.closed
    assert redis_client.closed
    assert instance.pool is None
    assert instance.redis is None


@pytest.mark.asyncio
async def test_db_close_is_safe_after_partial_startup():
    instance = DB()
    pool = FakePool()
    instance.pool = pool

    await instance.close()
    await instance.close()

    assert pool.closed
    assert instance.pool is None
    assert instance.redis is None
