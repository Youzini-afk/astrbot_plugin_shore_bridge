from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

try:
    import websockets
except Exception:
    websockets = None


class ShoreEventStream:
    def __init__(
        self,
        *,
        url: str,
        headers_factory: Callable[[], dict[str, str]],
        logger: Any,
        interested_events: tuple[str, ...],
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._url = url
        self._headers_factory = headers_factory
        self._logger = logger
        self._interested_events = set(interested_events)
        self._on_event = on_event
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if websockets is None:
            self._logger.warning(
                "shore bridge event stream disabled because websockets is unavailable",
            )
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="shore-bridge-events")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url,
                    extra_headers=self._headers_factory(),
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=1024 * 1024,
                ) as socket:
                    backoff = 1.0
                    async for message in socket:
                        if self._stop.is_set():
                            break
                        if not isinstance(message, str):
                            continue
                        event = self._parse_message(message)
                        if event is None:
                            continue
                        if self._interested_events and event.get("event") not in self._interested_events:
                            continue
                        if self._on_event is not None:
                            await self._on_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._logger.warning("shore bridge event stream disconnected: %s", exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    def _parse_message(self, message: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._logger.debug("shore bridge ignored non-json websocket frame")
            return None
        if isinstance(payload, dict):
            return payload
        return None
