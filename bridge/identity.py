from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    actor_account_uid: str
    actor_person_uid: str
    subject_person_uid: str
    source_platform: str
    observation_at: str

    def domain_payload(self, *, scope: str | None = None) -> dict[str, Any]:
        resolved_scope = str(scope or self.scope_hint or "").strip().lower()
        if resolved_scope == "private":
            return {
                "kind": "platform_person",
                "key": self.actor_person_uid,
                "platform": self.source_platform,
                "channel_uid": self.channel_uid,
                "session_uid": self.session_uid,
                "person_uid": self.actor_person_uid,
            }
        if resolved_scope == "group":
            return {
                "kind": "channel_shared",
                "key": self.channel_uid,
                "platform": self.source_platform,
                "channel_uid": self.channel_uid,
                "session_uid": self.session_uid,
                "person_uid": None,
            }
        return {
            "kind": "session_thread",
            "key": self.session_uid,
            "platform": self.source_platform,
            "channel_uid": self.channel_uid,
            "session_uid": self.session_uid,
            "person_uid": self.actor_person_uid,
        }

    def alias_hints_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "account_uid": self.actor_account_uid,
                "person_uid": self.actor_person_uid,
                "confidence": 1.0,
            }
        ]


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
        actor_account_uid = f"{platform}:account:{sender_id}"
        actor_person_uid = f"{platform}:person:{sender_id}"
        observation_at = self._observation_at(event)
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
            actor_account_uid=actor_account_uid,
            actor_person_uid=actor_person_uid,
            subject_person_uid=actor_person_uid,
            source_platform=str(platform),
            observation_at=observation_at,
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

    @staticmethod
    def _observation_at(event: Any) -> str:
        raw = getattr(event, "created_at", None)
        if isinstance(raw, datetime):
            dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        text = str(raw or "").strip()
        if text:
            return text
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
