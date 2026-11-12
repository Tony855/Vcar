import asyncio

import pytest

from app.bot.handlers.driver import _schedule_notification


@pytest.mark.asyncio
async def test_background_notification_logs_delivery_failure(caplog):
    async def fail_delivery():
        raise RuntimeError("Telegram API unavailable")

    with caplog.at_level("ERROR"):
        task = _schedule_notification(
            fail_delivery(), context="trip-completed notice for order 42"
        )
        assert task is not None
        await asyncio.wait({task})
        await asyncio.sleep(0)

    assert "Telegram notification failed" in caplog.text
    assert "trip-completed notice for order 42" in caplog.text
    assert "Telegram API unavailable" in caplog.text


@pytest.mark.asyncio
async def test_background_notification_does_not_log_success(caplog):
    async def successful_delivery():
        return True

    with caplog.at_level("ERROR"):
        task = _schedule_notification(successful_delivery(), context="order 43")
        assert task is not None
        await task
        await asyncio.sleep(0)

    assert "Telegram notification failed" not in caplog.text
