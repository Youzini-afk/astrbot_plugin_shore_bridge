from __future__ import annotations

import json
from typing import Any


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _format_entities(value: Any) -> str:
    if not value:
        return ""
    parts: list[str] = []
    if isinstance(value, list):
        source = value
    else:
        source = [value]
    for item in source:
        if isinstance(item, dict):
            candidate = item.get("name") or item.get("entity") or item.get("value")
        else:
            candidate = item
        text = _normalize_text(candidate)
        if text and text not in parts:
            parts.append(text)
    return ", ".join(parts)


def _format_state_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return _normalize_text(json.dumps(value, ensure_ascii=False))
    return _normalize_text(value)


def format_agent_state(agent_state: Any) -> str:
    if not isinstance(agent_state, dict) or not agent_state:
        return ""
    preferred = ["mood", "vibe", "mind", "goal", "focus", "style"]
    ordered_keys: list[str] = []
    for key in preferred:
        if key in agent_state:
            ordered_keys.append(key)
    for key in agent_state:
        if key not in ordered_keys:
            ordered_keys.append(key)
    lines = ["[Shore Agent State]"]
    for key in ordered_keys:
        value = _format_state_value(agent_state.get(key))
        if value:
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_recall_block(
    response: dict[str, Any],
    *,
    min_score: float,
    max_chars: int,
    include_entities: bool,
    inject_agent_state: bool,
    degraded_notice: bool,
) -> str:
    parts: list[str] = []
    if degraded_notice and response.get("degraded"):
        parts.append("[Shore Recall Notice]\n- Shore Memory returned a degraded result. Prefer stable facts and avoid over-committing.")
    lines = ["[Shore Memory Context]"]
    current_chars = 0
    for item in response.get("memory_context") or []:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if isinstance(score, (int, float)) and float(score) < min_score:
            continue
        content = _normalize_text(item.get("content"))
        if not content:
            continue
        segments: list[str] = []
        time_text = _normalize_text(item.get("time"))
        if time_text:
            segments.append(time_text)
        if isinstance(score, (int, float)):
            segments.append(f"score={float(score):.2f}")
        prefix = f"[{', '.join(segments)}] " if segments else ""
        line = f"- {prefix}{content}"
        entities = _format_entities(item.get("entities")) if include_entities else ""
        if entities:
            line = f"{line}\n  entities: {entities}"
        if current_chars and current_chars + len(line) > max_chars:
            break
        lines.append(line)
        current_chars += len(line)
    if len(lines) > 1:
        parts.append("\n".join(lines))
    if inject_agent_state:
        state_block = format_agent_state(response.get("agent_state"))
        if state_block:
            parts.append(state_block)
    return "\n\n".join(parts)


def build_recall_preview(
    response: dict[str, Any],
    *,
    min_score: float,
    limit: int,
) -> str:
    items = response.get("memory_context") or []
    lines = []
    if response.get("degraded"):
        lines.append("Recall degraded: true")
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if isinstance(score, (int, float)) and float(score) < min_score:
            continue
        content = _normalize_text(item.get("content"))
        if not content:
            continue
        memory_id = item.get("memory_id") or item.get("id") or "-"
        time_text = _normalize_text(item.get("time"))
        if isinstance(score, (int, float)):
            score_text = f"{float(score):.2f}"
        else:
            score_text = "-"
        header = f"#{memory_id} score={score_text}"
        if time_text:
            header = f"{header} time={time_text}"
        lines.append(header)
        lines.append(content)
        count += 1
        if count >= limit:
            break
    if not lines:
        return "No recalled memories matched the current filter."
    return "\n".join(lines)
