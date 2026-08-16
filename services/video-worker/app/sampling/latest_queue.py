from __future__ import annotations

import asyncio


class LatestOnlyQueue[T]:
    """A bounded queue that replaces stale pending work with the newest item."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=1)
        self.dropped = 0

    async def put_latest(self, item: T) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)

    async def get(self) -> T:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped += 1
            except asyncio.QueueEmpty:
                return

    @property
    def qsize(self) -> int:
        return self._queue.qsize()
