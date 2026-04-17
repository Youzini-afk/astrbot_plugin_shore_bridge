from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core import sp
from astrbot.core.agent.message import TextPart
from astrbot.core.star.filter.command import GreedyStr

from .bridge import (
    BackgroundWriteback,
    BridgeConfig,
    BridgeIdentity,
    PendingTurn,
    ResponseDeduper,
    SessionBucketStore,
    ShoreClient,
    ShoreEventStream,
    build_recall_block,
    build_recall_preview,
    format_agent_state,
)

PLUGIN_NAME = "astrbot_plugin_shore_bridge"
PLUGIN_VERSION = "0.2.0"
EXTRA_USER_INPUT = "_shore_bridge_user_input"
EXTRA_IDENTITY = "_shore_bridge_identity"
EXTRA_AGENT_ID = "_shore_bridge_agent_id"
MUTE_KEY = "shore_bridge_muted"


@star.register(
    PLUGIN_NAME,
    "OpenAI Codex",
    "Bridge AstrBot conversations to Shore Memory.",
    PLUGIN_VERSION,
)
class Main(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self._settings = BridgeConfig.from_mapping(config)
        self._client: ShoreClient | None = None
        self._identity_store: SessionBucketStore | None = None
        self._writeback: BackgroundWriteback | None = None
        self._event_stream: ShoreEventStream | None = None
        self._deduper = ResponseDeduper()

    async def initialize(self) -> None:
        await self._ensure_runtime()

    async def terminate(self) -> None:
        if self._event_stream is not None:
            await self._event_stream.stop()
            self._event_stream = None
        if self._writeback is not None:
            await self._writeback.stop()
            self._writeback = None
        if self._client is not None:
            await self._client.close()
            self._client = None

    @filter.on_llm_request()
    async def inject_shore_memory(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        await self._ensure_runtime()
        if not self._settings.enabled:
            return
        if await self._is_muted(event):
            return
        identity = await self._get_identity(event)
        agent_id = self._settings.resolve_agent_id(identity.platform, identity.platform_name)
        user_text = self._extract_event_user_text(event)
        event.set_extra(EXTRA_USER_INPUT, user_text)
        event.set_extra(EXTRA_IDENTITY, identity)
        event.set_extra(EXTRA_AGENT_ID, agent_id)
        recall = await self._perform_recall(event, identity=identity, agent_id=agent_id)
        if recall is None:
            return
        prompt_block = build_recall_block(
            recall,
            min_score=self._settings.recall_min_score,
            max_chars=self._settings.recall_max_chars,
            include_entities=self._settings.recall_include_entities,
            inject_agent_state=self._settings.inject_agent_state,
            degraded_notice=self._settings.degraded_notice,
        )
        if not prompt_block:
            return
        if self._settings.inject_mode == "user":
            req.extra_user_content_parts.append(TextPart(text=prompt_block))
            return
        existing = req.system_prompt or ""
        req.system_prompt = (
            f"{existing}\n\n{prompt_block}".strip()
            if existing
            else prompt_block
        )

    @filter.on_llm_response()
    async def writeback_shore_turn(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
    ) -> None:
        await self._ensure_runtime()
        if not self._settings.enabled or not self._settings.writeback_enabled:
            return
        if await self._is_muted(event):
            return
        if getattr(resp, "is_chunk", False):
            return
        user_text = str(event.get_extra(EXTRA_USER_INPUT, "") or "").strip()
        assistant_text = str(resp.completion_text or "").strip()
        if not user_text or not assistant_text:
            return
        identity = await self._get_identity(event)
        agent_id = str(
            event.get_extra(EXTRA_AGENT_ID, "")
            or self._settings.resolve_agent_id(identity.platform, identity.platform_name),
        )
        response_key = self._build_response_key(event, resp, user_text, assistant_text)
        if self._deduper.seen(response_key):
            return
        payload = {
            "agent_id": agent_id,
            "user_uid": identity.user_uid,
            "channel_uid": identity.channel_uid,
            "session_uid": identity.session_uid,
            "source": "astrbot",
            "scope_hint": identity.scope_hint,
            "messages": [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
            "metadata": {
                "platform": identity.platform,
                "platform_name": identity.platform_name,
                "sender_name": identity.sender_name,
                "message_type": identity.message_type,
                "umo": getattr(event, "unified_msg_origin", ""),
                "response_id": str(getattr(resp, "id", "") or ""),
                "bridge_version": PLUGIN_VERSION,
            },
        }
        if self._writeback is None:
            return
        queued = self._writeback.enqueue(
            PendingTurn(
                payload=payload,
                request_id=self._new_request_id("turn"),
            ),
        )
        if not queued:
            logger.warning("shore bridge writeback queue is full; dropped a completed turn")

    @filter.command_group("shore", alias={"memory"})
    def shore(self) -> None:
        pass

    @shore.command("ping")
    async def shore_ping(self, event: AstrMessageEvent):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        try:
            health = await self._client.health(request_id=self._new_request_id("ping"))
        except Exception as exc:
            yield event.plain_result(f"Shore ping failed: {exc}").stop_event()
            return
        lines = [
            f"status: {health.get('status', 'unknown')}",
            f"worker_available: {health.get('worker_available', False)}",
            f"pending_tasks: {health.get('pending_tasks', 0)}",
            f"failed_tasks: {health.get('failed_tasks', 0)}",
        ]
        yield event.plain_result("\n".join(lines)).stop_event()

    @shore.command("status")
    async def shore_status(self, event: AstrMessageEvent):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        identity = await self._get_identity(event)
        agent_id = self._settings.resolve_agent_id(identity.platform, identity.platform_name)
        muted = await self._is_muted(event)
        lines = [
            f"enabled: {self._settings.enabled}",
            f"muted: {muted}",
            f"service: {self._settings.service_base_url}",
            f"agent_id: {agent_id}",
            f"scope_hint: {identity.scope_hint}",
            f"channel_uid: {identity.channel_uid}",
            f"session_uid: {identity.session_uid}",
        ]
        yield event.plain_result("\n".join(lines)).stop_event()

    @shore.command("recall")
    async def shore_recall(self, event: AstrMessageEvent, query: GreedyStr = ""):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        identity = await self._get_identity(event)
        agent_id = self._settings.resolve_agent_id(identity.platform, identity.platform_name)
        try:
            response = await self._perform_recall(
                event,
                identity=identity,
                agent_id=agent_id,
                manual_query=str(query).strip(),
                raise_on_error=True,
            )
        except Exception as exc:
            yield event.plain_result(f"Shore recall failed: {exc}").stop_event()
            return
        if response is None:
            yield event.plain_result("No recall query could be built for the current session.").stop_event()
            return
        preview = build_recall_preview(
            response,
            min_score=self._settings.recall_min_score,
            limit=self._settings.recall_limit,
        )
        state_block = format_agent_state(response.get("agent_state"))
        text = f"{preview}\n\n{state_block}".strip() if state_block else preview
        yield event.plain_result(text).stop_event()

    @shore.command("remember")
    async def shore_remember(self, event: AstrMessageEvent, content: GreedyStr = ""):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        memory_text = str(content).strip()
        if not memory_text:
            yield event.plain_result("Usage: /shore remember <content>").stop_event()
            return
        identity = await self._get_identity(event)
        agent_id = self._settings.resolve_agent_id(identity.platform, identity.platform_name)
        scope = self._resolve_manual_scope(identity)
        payload = {
            "agent_id": agent_id,
            "user_uid": identity.user_uid,
            "channel_uid": identity.channel_uid,
            "session_uid": identity.session_uid,
            "scope": scope,
            "memory_type": "manual_note",
            "content": memory_text,
            "source": "astrbot_manual",
            "metadata": {
                "platform": identity.platform,
                "platform_name": identity.platform_name,
                "sender_name": identity.sender_name,
                "umo": getattr(event, "unified_msg_origin", ""),
                "bridge_version": PLUGIN_VERSION,
            },
        }
        try:
            response = await self._client.create_memory(
                payload,
                request_id=self._new_request_id("remember"),
            )
        except Exception as exc:
            yield event.plain_result(f"Shore remember failed: {exc}").stop_event()
            return
        memory = response.get("memory") or {}
        memory_id = memory.get("memory_id") or memory.get("id") or "?"
        rebuild = bool(response.get("rebuild_queued"))
        yield event.plain_result(
            f"Stored memory #{memory_id} in scope={scope}, rebuild_queued={rebuild}",
        ).stop_event()

    @shore.command("forget")
    async def shore_forget(self, event: AstrMessageEvent, memory_id: int):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        try:
            response = await self._client.update_memory(
                memory_id,
                {"archived": True, "source": "astrbot_manual"},
                request_id=self._new_request_id("forget"),
            )
        except Exception as exc:
            yield event.plain_result(f"Shore forget failed: {exc}").stop_event()
            return
        memory = response.get("memory") or {}
        archived_id = memory.get("memory_id") or memory_id
        yield event.plain_result(f"Archived memory #{archived_id}").stop_event()

    @shore.command("state")
    async def shore_state(self, event: AstrMessageEvent):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        identity = await self._get_identity(event)
        agent_id = self._settings.resolve_agent_id(identity.platform, identity.platform_name)
        try:
            state = await self._client.get_agent_state(
                agent_id,
                request_id=self._new_request_id("state"),
            )
        except Exception as exc:
            yield event.plain_result(f"Shore state failed: {exc}").stop_event()
            return
        block = format_agent_state(state)
        text = block or json.dumps(state, ensure_ascii=False, indent=2)
        yield event.plain_result(text).stop_event()

    @shore.command("mute")
    async def shore_mute(self, event: AstrMessageEvent):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        await sp.session_put(event.unified_msg_origin, MUTE_KEY, True)
        yield event.plain_result("Shore bridge muted for this session.").stop_event()

    @shore.command("unmute")
    async def shore_unmute(self, event: AstrMessageEvent):
        gate = await self._command_gate(event)
        if gate is not None:
            yield gate
            return
        await sp.session_put(event.unified_msg_origin, MUTE_KEY, False)
        yield event.plain_result("Shore bridge unmuted for this session.").stop_event()

    async def _ensure_runtime(self) -> None:
        if self._client is not None:
            return
        self._settings = BridgeConfig.from_mapping(self.config)
        self._identity_store = SessionBucketStore(self._settings.session_idle_minutes)
        self._client = ShoreClient(self._settings, version=PLUGIN_VERSION)
        await self._client.open()
        if self._settings.writeback_enabled:
            self._writeback = BackgroundWriteback(
                self._send_turn_writeback,
                max_retries=self._settings.writeback_max_retries,
                queue_size=self._settings.writeback_queue_size,
                logger=logger,
            )
            await self._writeback.start()
        if self._settings.events_ws_enabled:
            self._event_stream = ShoreEventStream(
                url=self._client.websocket_url(),
                headers_factory=self._client.websocket_headers,
                logger=logger,
                interested_events=self._settings.events_ws_log_types,
                on_event=self._handle_server_event,
            )
            await self._event_stream.start()

    async def _command_gate(self, event: AstrMessageEvent):
        await self._ensure_runtime()
        if not self._settings.commands_enabled:
            return event.plain_result("Shore commands are disabled in plugin config.").stop_event()
        if self._client is None:
            return event.plain_result("Shore client is not initialized.").stop_event()
        return None

    async def _perform_recall(
        self,
        event: AstrMessageEvent,
        *,
        identity: BridgeIdentity,
        agent_id: str,
        manual_query: str = "",
        raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        if self._client is None:
            return None
        history = await self._load_conversation_history(event)
        base_text = manual_query or self._extract_event_user_text(event)
        query = self._build_recall_query(base_text, history)
        if not query:
            return None
        payload = {
            "agent_id": agent_id,
            "user_uid": identity.user_uid,
            "channel_uid": identity.channel_uid,
            "session_uid": identity.session_uid,
            "query": query,
            "source": "astrbot",
            "limit": self._settings.recall_limit,
            "include_state": self._settings.inject_agent_state,
            "scope_hint": identity.scope_hint,
        }
        if self._settings.recall_recipe:
            payload["recipe"] = self._settings.recall_recipe
        if self._settings.recall_selected_scopes:
            payload["selected_scopes"] = list(self._settings.recall_selected_scopes)
        if self._settings.recall_debug:
            payload["debug"] = True
        try:
            return await self._client.recall(
                payload,
                request_id=self._new_request_id("recall"),
            )
        except Exception as exc:
            if raise_on_error:
                raise
            logger.warning("shore bridge recall failed: %s", exc)
            return None

    async def _send_turn_writeback(self, payload: dict[str, Any], request_id: str) -> None:
        if self._client is None:
            return
        await self._client.write_turn(payload, request_id=request_id)

    async def _handle_server_event(self, event_data: dict[str, Any]) -> None:
        event_name = str(event_data.get("event") or "unknown")
        payload = event_data.get("payload")
        if event_name == "lagged":
            logger.warning("shore bridge websocket lagged: %s", payload)
            return
        compact_payload = json.dumps(payload, ensure_ascii=False)[:300] if payload is not None else ""
        logger.info("shore bridge event %s %s", event_name, compact_payload)

    async def _get_identity(self, event: AstrMessageEvent) -> BridgeIdentity:
        cached = event.get_extra(EXTRA_IDENTITY)
        if isinstance(cached, BridgeIdentity):
            return cached
        assert self._identity_store is not None
        identity = await self._identity_store.build_identity(event)
        event.set_extra(EXTRA_IDENTITY, identity)
        return identity

    async def _is_muted(self, event: AstrMessageEvent) -> bool:
        try:
            muted = await sp.session_get(event.unified_msg_origin, MUTE_KEY, False)
        except Exception:
            return False
        return bool(muted)

    async def _load_conversation_history(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        manager = getattr(self.context, "conversation_manager", None)
        if manager is None:
            return []
        try:
            conversation_id = await manager.get_curr_conversation_id(event.unified_msg_origin)
            if not conversation_id:
                return []
            conversation = await manager.get_conversation(event.unified_msg_origin, conversation_id)
        except Exception:
            return []
        if not conversation:
            return []
        try:
            history = json.loads(conversation.history or "[]")
        except Exception:
            return []
        return history if isinstance(history, list) else []

    def _build_recall_query(self, user_text: str, history: list[dict[str, Any]]) -> str:
        primary = self._normalize_text(user_text)
        if not primary and not self._settings.recall_on_empty_message:
            return ""
        parts: list[str] = []
        if primary:
            parts.append(primary)
        history_block = self._format_recent_history(history)
        if history_block:
            parts.append(f"Recent conversation:\n{history_block}")
        return "\n\n".join(part for part in parts if part).strip()

    def _format_recent_history(self, history: list[dict[str, Any]]) -> str:
        if self._settings.recall_context_messages <= 0:
            return ""
        selected: list[str] = []
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._extract_history_content(item.get("content"))
            if not content:
                continue
            selected.append(f"- {role}: {content}")
            if len(selected) >= self._settings.recall_context_messages:
                break
        selected.reverse()
        return "\n".join(selected)

    def _extract_history_content(self, value: Any) -> str:
        if isinstance(value, str):
            return self._normalize_text(value)
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = self._normalize_text(item.get("text"))
                    if text:
                        parts.append(text)
            return self._normalize_text(" ".join(parts))
        return ""

    def _extract_event_user_text(self, event: AstrMessageEvent) -> str:
        primary = self._normalize_text(getattr(event, "message_str", ""))
        if primary:
            return primary
        outline_getter = getattr(event, "get_message_outline", None)
        if callable(outline_getter):
            try:
                return self._normalize_text(outline_getter())
            except Exception:
                return ""
        return ""

    def _resolve_manual_scope(self, identity: BridgeIdentity) -> str:
        if self._settings.remember_default_scope == "auto":
            return identity.scope_hint
        return self._settings.remember_default_scope

    def _build_response_key(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        user_text: str,
        assistant_text: str,
    ) -> str:
        response_id = str(getattr(resp, "id", "") or "")
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        created_at = str(getattr(event, "created_at", "") or "")
        raw = "|".join([session_id, created_at, response_id, user_text, assistant_text])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _new_request_id(self, label: str) -> str:
        return f"{self._settings.request_id_prefix}-{label}-{uuid4().hex[:12]}"

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = str(value or "").strip()
        return " ".join(text.split())
