from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

CHECKPOINT_SCHEMA_ID = "neo.assistant.conversation_checkpoint.v1"
CHECKPOINT_STATE_SCHEMA_ID = "neo.assistant.conversation_checkpoint_state.v1"
CHECKPOINT_VERSION = "1.0.0-aci7"

# Provider history remains deliberately small. Canonical source messages stay in the
# session file; checkpoints are a derived, reversible prompt projection only.
RECENT_MESSAGE_WINDOW = 6
LEVEL1_CHUNK_MESSAGES = 6
LEVEL2_CHUNK_CHECKPOINTS = 4
MAX_LEVEL1_SUMMARY_CHARS = 1800
MAX_LEVEL2_SUMMARY_CHARS = 1800
MAX_PROVIDER_CHECKPOINT_CHARS = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, *, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _hash(parts: list[str]) -> str:
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _message_digest(message: dict[str, Any]) -> str:
    role = "Assistant" if str(message.get("role") or "") == "assistant" else "User"
    text = _clean(message.get("text"), limit=240)
    return f"{role}: {text}" if text else ""


def _source_prefix_hash(messages: list[dict[str, Any]], count: int) -> str:
    if count <= 0:
        return ""
    parts: list[str] = []
    for item in messages[:count]:
        if not isinstance(item, dict):
            continue
        parts.extend([
            str(item.get("message_id") or ""),
            str(item.get("role") or ""),
            str(item.get("text") or ""),
        ])
    return _hash(parts) if parts else ""


def _checkpoint_from_messages(messages: list[dict[str, Any]], start_index: int, end_index: int) -> dict[str, Any]:
    chunk = [item for item in messages[start_index:end_index] if isinstance(item, dict)]
    source_ids = [str(item.get("message_id") or "") for item in chunk if str(item.get("message_id") or "")]
    lines = [line for line in (_message_digest(item) for item in chunk) if line]
    summary = _clean("\n".join(f"- {line}" for line in lines), limit=MAX_LEVEL1_SUMMARY_CHARS)
    digest = _hash([*source_ids, summary])
    return {
        "schema_id": CHECKPOINT_SCHEMA_ID,
        "version": CHECKPOINT_VERSION,
        "checkpoint_id": f"cp1_{uuid4().hex[:12]}",
        "level": 1,
        "source_start_index": int(start_index),
        "source_end_index": int(end_index),
        "source_message_ids": source_ids,
        "source_checkpoint_ids": [],
        "message_count": len(chunk),
        "summary": summary,
        "content_hash": digest,
        "created_at": _now(),
    }


def _checkpoint_from_children(children: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids: list[str] = []
    source_checkpoint_ids: list[str] = []
    lines: list[str] = []
    for child in children:
        source_ids.extend(str(v) for v in child.get("source_message_ids") or [] if str(v or ""))
        source_checkpoint_ids.append(str(child.get("checkpoint_id") or ""))
        summary = _clean(child.get("summary"), limit=520)
        if summary:
            lines.append(summary)
    start_index = min(int(child.get("source_start_index") or 0) for child in children)
    end_index = max(int(child.get("source_end_index") or 0) for child in children)
    summary = _clean("\n".join(lines), limit=MAX_LEVEL2_SUMMARY_CHARS)
    return {
        "schema_id": CHECKPOINT_SCHEMA_ID,
        "version": CHECKPOINT_VERSION,
        "checkpoint_id": f"cp2_{uuid4().hex[:12]}",
        "level": 2,
        "source_start_index": start_index,
        "source_end_index": end_index,
        "source_message_ids": list(dict.fromkeys(source_ids)),
        "source_checkpoint_ids": [item for item in source_checkpoint_ids if item],
        "message_count": len(source_ids),
        "summary": summary,
        "content_hash": _hash([*source_checkpoint_ids, summary]),
        "created_at": _now(),
    }


def ensure_conversation_checkpoints(session: dict[str, Any] | None) -> dict[str, Any]:
    """Incrementally derive source-linked checkpoints without mutating source history."""
    target = session if isinstance(session, dict) else {}
    messages = target.get("messages") if isinstance(target.get("messages"), list) else []
    # A trailing partial L1 checkpoint is provider continuity state, not a sealed
    # checkpoint. Rebuild it as more source turns age out of the recent window.
    checkpoints = [
        dict(item)
        for item in (target.get("conversation_checkpoints") or [])
        if isinstance(item, dict) and not bool(item.get("partial"))
    ]
    state = dict(target.get("checkpoint_state") or {})
    processed = max(0, int(state.get("level1_processed_messages") or 0))
    previous_prefix_hash = str(state.get("processed_prefix_hash") or "")
    current_prefix_hash = _source_prefix_hash(messages, processed)
    regenerated = bool(
        processed > len(messages)
        or (processed > 0 and (not previous_prefix_hash or previous_prefix_hash != current_prefix_hash))
    )
    if regenerated:
        checkpoints = []
        state = {}
        processed = 0
    recent_start = max(0, len(messages) - RECENT_MESSAGE_WINDOW)

    while processed + LEVEL1_CHUNK_MESSAGES <= recent_start:
        end = processed + LEVEL1_CHUNK_MESSAGES
        checkpoints.append(_checkpoint_from_messages(messages, processed, end))
        processed = end

    if processed < recent_start:
        partial = _checkpoint_from_messages(messages, processed, recent_start)
        partial["partial"] = True
        checkpoints.append(partial)

    level1 = sorted(
        (item for item in checkpoints if int(item.get("level") or 1) == 1 and not bool(item.get("partial"))),
        key=lambda item: int(item.get("source_start_index") or 0),
    )
    rolled = max(0, int(state.get("level2_processed_checkpoints") or 0))
    while rolled + LEVEL2_CHUNK_CHECKPOINTS <= len(level1):
        children = level1[rolled:rolled + LEVEL2_CHUNK_CHECKPOINTS]
        child_ids = [str(item.get("checkpoint_id") or "") for item in children]
        already = next((item for item in checkpoints if int(item.get("level") or 0) == 2 and item.get("source_checkpoint_ids") == child_ids), None)
        if not already:
            checkpoints.append(_checkpoint_from_children(children))
        rolled += LEVEL2_CHUNK_CHECKPOINTS

    state = {
        "schema_id": CHECKPOINT_STATE_SCHEMA_ID,
        "version": CHECKPOINT_VERSION,
        "level1_processed_messages": processed,
        "level2_processed_checkpoints": rolled,
        "source_message_count": len(messages),
        "processed_prefix_hash": _source_prefix_hash(messages, processed),
        "updated_at": _now(),
    }
    target["conversation_checkpoints"] = checkpoints
    target["checkpoint_state"] = state
    return {
        "ok": True,
        "status": "ready",
        "checkpoint_count": len(checkpoints),
        "level1_count": sum(1 for item in checkpoints if int(item.get("level") or 0) == 1),
        "level2_count": sum(1 for item in checkpoints if int(item.get("level") or 0) == 2),
        "covered_message_count": processed,
        "source_message_count": len(messages),
        "recent_message_window": RECENT_MESSAGE_WINDOW,
        "regenerated": regenerated,
        "state": state,
    }


def select_provider_checkpoints(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    target = session if isinstance(session, dict) else {}
    checkpoints = [dict(item) for item in (target.get("conversation_checkpoints") or []) if isinstance(item, dict)]
    if not checkpoints:
        return []
    recent_start = max(0, len(target.get("messages") or []) - RECENT_MESSAGE_WINDOW)
    eligible = [item for item in checkpoints if int(item.get("source_end_index") or 0) <= recent_start and _clean(item.get("summary"))]
    # Prefer high-level checkpoints, then fill uncovered gaps with L1 checkpoints.
    selected: list[dict[str, Any]] = []
    covered: list[tuple[int, int]] = []
    for item in sorted(eligible, key=lambda row: (-int(row.get("level") or 1), int(row.get("source_start_index") or 0))):
        start = int(item.get("source_start_index") or 0)
        end = int(item.get("source_end_index") or 0)
        if any(not (end <= a or start >= b) for a, b in covered):
            continue
        selected.append(item)
        covered.append((start, end))
    selected.sort(key=lambda row: int(row.get("source_start_index") or 0))
    return selected


def render_provider_checkpoint_context(session: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    selected = select_provider_checkpoints(session)
    if not selected:
        return "", {"status": "not_needed", "selected": 0, "chars": 0, "source_message_count": len((session or {}).get("messages") or []) if isinstance(session, dict) else 0}
    lines = [
        "Conversation checkpoints (derived from earlier source messages; current/recent messages below take precedence):"
    ]
    used = len(lines[0])
    emitted: list[dict[str, Any]] = []
    for item in selected:
        summary = _clean(item.get("summary"), limit=1000)
        if not summary:
            continue
        label = f"Checkpoint {item.get('checkpoint_id')} · messages {int(item.get('source_start_index') or 0)+1}-{int(item.get('source_end_index') or 0)}"
        block = f"\n[{label}]\n{summary}"
        if used + len(block) > MAX_PROVIDER_CHECKPOINT_CHARS:
            break
        lines.append(block)
        used += len(block)
        emitted.append(item)
    text = "\n".join(lines).strip() if emitted else ""
    return text, {
        "status": "ready" if emitted else "not_needed",
        "selected": len(emitted),
        "chars": len(text),
        "checkpoint_ids": [item.get("checkpoint_id") for item in emitted],
        "covered_message_count": sum(int(item.get("message_count") or 0) for item in emitted),
        "source_message_count": len((session or {}).get("messages") or []) if isinstance(session, dict) else 0,
    }
