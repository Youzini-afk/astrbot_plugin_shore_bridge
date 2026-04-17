from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any


@dataclass(slots=True)
class BridgeIdentity:
    platform: str
    platform_name: str
    message_type: str
    sender_id: str
    sender_name: str
    group_id: str
    user_uid: str
    channel_uid: str
    session_uid: str
    scope_hint: str


class SessionBucketStore:
    def __init__(self, idle_minutes: int) -> None:
        self._idle_seconds = max(60, int(idle_minutes) * 60)
        self._lock = asyncio.Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    async def build_identity(self, event: Any) -> BridgeIdentity:
        platform = self._safe_call(event, "get_platform_id") or "unknown"
        platform_name = self._safe_call(event, "get_platform_name") or platform
        message_type = self._safe_call(event, "get_message_type") or "unknown"
        sender_id = self._safe_call(event, "get_sender_id") or "unknown"
        sender_name = self._safe_call(event, "get_sender_name") or ""
        group_id = self._safe_call(event, "get_group_id") or ""
        scope_hint = "group" if group_id else "private"
        user_uid = f"{platform}:user:{sender_id}"
        channel_uid = (
            f"{platform}:group:{group_id}"
            if group_id
            else f"{platform}:dm:{sender_id}"
        )
        raw_session_uid = getattr(event, "unified_msg_origin", None) or channel_uid
        session_uid = await self._bucketize(raw_session_uid)
        return BridgeIdentity(
            platform=str(platform),
            platform_name=str(platform_name),
            message_type=str(message_type),
            sender_id=str(sender_id),
            sender_name=str(sender_name),
            group_id=str(group_id),
            user_uid=user_uid,
            channel_uid=channel_uid,
            session_uid=session_uid,
            scope_hint=scope_hint,
        )

    async def _bucketize(self, raw_session_uid: str) -> str:
        now = time.time()
        async with self._lock:
            bucket = self._buckets.get(raw_session_uid)
            if bucket and now - bucket[1] <= self._idle_seconds:
                bucket_id = bucket[0]
            else:
                bucket_id = int(now)
            self._buckets[raw_session_uid] = (bucket_id, now)
        return f"{raw_session_uid}#{bucket_id}"

    @staticmethod
    def _safe_call(obj: Any, method_name: str) -> Any:
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                return None
        return None
