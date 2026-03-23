import asyncio
from collections import defaultdict
from bot.config import config


class RateLimiter:
    """
    Per-user semaphore that limits concurrent downloads.

    Usage:
        if rate_limiter.is_at_limit(user_id):
            # tell user they're at capacity
        else:
            await rate_limiter.acquire(user_id)
            try:
                ...work...
            finally:
                rate_limiter.release(user_id)
    """

    def __init__(self) -> None:
        self._semaphores: dict[int, asyncio.Semaphore] = {}
        self._active: dict[int, int] = defaultdict(int)

    def _sem(self, user_id: int) -> asyncio.Semaphore:
        if user_id not in self._semaphores:
            self._semaphores[user_id] = asyncio.Semaphore(
                config.MAX_CONCURRENT_PER_USER
            )
        return self._semaphores[user_id]

    def get_active_count(self, user_id: int) -> int:
        return self._active[user_id]

    def is_at_limit(self, user_id: int) -> bool:
        return self._active[user_id] >= config.MAX_CONCURRENT_PER_USER

    async def acquire(self, user_id: int) -> None:
        await self._sem(user_id).acquire()
        self._active[user_id] += 1

    def release(self, user_id: int) -> None:
        self._sem(user_id).release()
        self._active[user_id] = max(0, self._active[user_id] - 1)


rate_limiter = RateLimiter()
