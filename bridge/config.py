from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _as_float(value: Any, default: float, *, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _parse_platform_agent_map(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        raw_map = value
    elif isinstance(value, str):
        content = value.strip()
        if not content:
            return {}
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        raw_map = parsed
    else:
        return {}

    result: dict[str, str] = {}
    for key, mapped_agent in raw_map.items():
        normalized_key = str(key).strip().lower()
        normalized_agent = str(mapped_agent).strip()
        if normalized_key and normalized_agent:
            result[normalized_key] = normalized_agent
    return result


def _parse_csv(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value).split(",")
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = str(raw).strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return tuple(items)


@dataclass(slots=True)
class BridgeConfig:
    enabled: bool = True
    service_base_url: str = "http://127.0.0.1:7811"
    api_key: str = ""
    api_key_mode: str = "both"
    agent_id: str = "shore"
    platform_agent_map: dict[str, str] = field(default_factory=dict)
    recall_limit: int = 8
    recall_recipe: str = ""
    recall_debug: bool = False
    inject_agent_state: bool = True
    inject_mode: str = "system"
    recall_min_score: float = 0.0
    recall_max_chars: int = 1600
    recall_include_entities: bool = True
    recall_context_messages: int = 4
    recall_on_empty_message: bool = True
    degraded_notice: bool = True
    writeback_enabled: bool = True
    writeback_max_retries: int = 3
    writeback_queue_size: int = 128
    session_idle_minutes: int = 30
    commands_enabled: bool = True
    events_ws_enabled: bool = False
    events_ws_log_types: tuple[str, ...] = field(default_factory=tuple)
    remember_default_scope: str = "auto"
    connect_timeout_seconds: float = 2.0
    recall_read_timeout_seconds: float = 4.0
    writeback_read_timeout_seconds: float = 8.0
    command_read_timeout_seconds: float = 6.0
    request_id_prefix: str = "shore-bridge"

    @classmethod
    def from_mapping(cls, mapping: Any) -> "BridgeConfig":
        data = mapping or {}
        api_key_mode = _as_str(data.get("api_key_mode", "both"), "both").lower()
        if api_key_mode not in {"both", "bearer", "x-api-key"}:
            api_key_mode = "both"
        inject_mode = _as_str(data.get("inject_mode", "system"), "system").lower()
        if inject_mode not in {"system", "user"}:
            inject_mode = "system"
        remember_default_scope = _as_str(
            data.get("remember_default_scope", "auto"),
            "auto",
        ).lower()
        if remember_default_scope not in {"auto", "private", "group", "shared", "system"}:
            remember_default_scope = "auto"
        return cls(
            enabled=_as_bool(data.get("enabled", True), True),
            service_base_url=_as_str(
                data.get("service_base_url", "http://127.0.0.1:7811"),
                "http://127.0.0.1:7811",
            ).rstrip("/"),
            api_key=_as_str(data.get("api_key", ""), ""),
            api_key_mode=api_key_mode,
            agent_id=_as_str(data.get("agent_id", "shore"), "shore"),
            platform_agent_map=_parse_platform_agent_map(
                data.get("platform_agent_map_json", ""),
            ),
            recall_limit=_as_int(data.get("recall_limit", 8), 8, minimum=1),
            recall_recipe=_as_str(data.get("recall_recipe", ""), ""),
            recall_debug=_as_bool(data.get("recall_debug", False), False),
            inject_agent_state=_as_bool(data.get("inject_agent_state", True), True),
            inject_mode=inject_mode,
            recall_min_score=_as_float(data.get("recall_min_score", 0.0), 0.0),
            recall_max_chars=_as_int(data.get("recall_max_chars", 1600), 1600, minimum=200),
            recall_include_entities=_as_bool(
                data.get("recall_include_entities", True),
                True,
            ),
            recall_context_messages=_as_int(
                data.get("recall_context_messages", 4),
                4,
                minimum=0,
            ),
            recall_on_empty_message=_as_bool(
                data.get("recall_on_empty_message", True),
                True,
            ),
            degraded_notice=_as_bool(data.get("degraded_notice", True), True),
            writeback_enabled=_as_bool(data.get("writeback_enabled", True), True),
            writeback_max_retries=_as_int(
                data.get("writeback_max_retries", 3),
                3,
                minimum=0,
            ),
            writeback_queue_size=_as_int(
                data.get("writeback_queue_size", 128),
                128,
                minimum=1,
            ),
            session_idle_minutes=_as_int(
                data.get("session_idle_minutes", 30),
                30,
                minimum=1,
            ),
            commands_enabled=_as_bool(data.get("commands_enabled", True), True),
            events_ws_enabled=_as_bool(data.get("events_ws_enabled", False), False),
            events_ws_log_types=_parse_csv(data.get("events_ws_log_types", "")),
            remember_default_scope=remember_default_scope,
            connect_timeout_seconds=_as_float(
                data.get("connect_timeout_seconds", 2.0),
                2.0,
                minimum=0.1,
            ),
            recall_read_timeout_seconds=_as_float(
                data.get("recall_read_timeout_seconds", 4.0),
                4.0,
                minimum=0.1,
            ),
            writeback_read_timeout_seconds=_as_float(
                data.get("writeback_read_timeout_seconds", 8.0),
                8.0,
                minimum=0.1,
            ),
            command_read_timeout_seconds=_as_float(
                data.get("command_read_timeout_seconds", 6.0),
                6.0,
                minimum=0.1,
            ),
            request_id_prefix=_as_str(
                data.get("request_id_prefix", "shore-bridge"),
                "shore-bridge",
            ),
        )

    def resolve_agent_id(self, platform_id: str, platform_name: str) -> str:
        for key in (platform_id, platform_name):
            normalized = str(key).strip().lower()
            if normalized and normalized in self.platform_agent_map:
                return self.platform_agent_map[normalized]
        return self.agent_id
