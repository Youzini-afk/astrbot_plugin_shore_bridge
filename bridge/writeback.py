from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class PendingTurn:
    payload: dict[str, Any]
    request_id: str


class ResponseDeduper:
    def __init__(self, *, max_entries: int = 512) -> None:
        self._max_entries = max(32, max_entries)
        self._values: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str) -> bool:
        if not key:
            return False
        now = time.time()
        if key in self._values:
            self._values.move_to_end(key)
            self._values[key] = now
            return True
        self._values[key] = now
        if len(self._values) > self._max_entries:
            self._values.popitem(last=False)
        return False


class BackgroundWriteback:
    def __init__(
        self,
        sender: Callable[[dict[str, Any], str], Awaitable[None]],
        *,
        max_retries: int,
        queue_size: int,
        logger: Any,
    ) -> None:
        self._sender = sender
        self._max_retries = max(0, max_retries)
        self._queue: asyncio.Queue[PendingTurn | None] = asyncio.Queue(
            maxsize=max(1, queue_size),
        )
        self._logger = logger
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="shore-bridge-writeback")

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        try:
            await self._task
        finally:
            self._task = None

    def enqueue(self, item: PendingTurn) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            try:
                await self._send_with_retry(item)
            finally:
                self._queue.task_done()

    async def _send_with_retry(self, item: PendingTurn) -> None:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                await self._sender(item.payload, item.request_id)
                return
            except Exception as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(min(8.0, 1.5 * (2 ** attempt)))
        if last_error is not None:
            self._logger.warning(
                "shore bridge writeback permanently failed: %s",
                last_error,
            )
