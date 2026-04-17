from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_shore_bridge.bridge.identity import SessionBucketStore
from astrbot_plugin_shore_bridge.bridge.writeback import BackgroundWriteback, PendingTurn, ResponseDeduper


class _FakeEvent:
    unified_msg_origin = "qq:group:123"

    def get_platform_id(self):
        return "qq"

    def get_platform_name(self):
        return "qq"

    def get_message_type(self):
        return "group"

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "alice"

    def get_group_id(self):
        return "g1"


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args) -> None:
        self.messages.append(message % args if args else message)


class IdentityAndWritebackTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_bucket_reuses_active_bucket(self) -> None:
        store = SessionBucketStore(30)
        event = _FakeEvent()

        first = await store.build_identity(event)
        second = await store.build_identity(event)

        self.assertEqual(first.session_uid, second.session_uid)
        self.assertEqual(first.scope_hint, "group")
        self.assertEqual(first.channel_uid, "qq:group:g1")

    async def test_background_writeback_retries_once(self) -> None:
        attempts: list[str] = []
        logger = _Logger()

        async def sender(payload, request_id):
            attempts.append(request_id)
            if len(attempts) == 1:
                raise RuntimeError("boom")

        worker = BackgroundWriteback(sender, max_retries=1, queue_size=4, logger=logger)
        await worker.start()
        self.assertTrue(worker.enqueue(PendingTurn(payload={"ok": True}, request_id="rid-1")))
        await worker._queue.join()
        await worker.stop()

        self.assertEqual(attempts, ["rid-1", "rid-1"])
        self.assertEqual(logger.messages, [])

    def test_response_deduper_marks_seen_values(self) -> None:
        deduper = ResponseDeduper(max_entries=8)

        self.assertFalse(deduper.seen("abc"))
        self.assertTrue(deduper.seen("abc"))
