import pytest
from telethon.errors import FloodWaitError

from telegram_crawler.ratelimit import with_flood_retry


async def test_flood_wait_retries_exhaust_after_configured_attempts():
    calls = 0
    sleeps = []

    async def always_waits():
        nonlocal calls
        calls += 1
        raise FloodWaitError(request=None, capture=2)

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    with pytest.raises(FloodWaitError):
        await with_flood_retry(always_waits, max_retries=2, max_wait=30, sleep=fake_sleep)

    assert calls == 3
    assert sleeps == [3, 3]
