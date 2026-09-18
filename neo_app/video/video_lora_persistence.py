from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

from neo_app.video.video_lora_legacy_retirement import retire_legacy_payload

from neo_app.video.video_lora_runtime import (
    MAX_VIDEO_LORAS,
    VIDEO_LORA_EXTENSION_ID,
    normalize_video_lora_row,
)

EXTENSION_ID: Final[str] = VIDEO_LORA_EXTENSION_ID
VERSION: Final[int] = 1

VIDEO_LORA_PERSISTENCE_SCHEMA_VERSION: Final[str] = "neo.video.lora_stack.persistence.v1"
VIDEO_LORA_REPLAY_SCHEMA_VERSION: Final[str] = "neo.video.lora_stack.replay.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return default


def _raw_block(value: dict[str, Any] | None) -> dict[str, Any]:
    value = _dict(value)
    if isinstance(value.get(EXTENSION_ID), dict):
        return _dict(value.get(EXTENSION_ID))
    extensions = _dict(value.get("extensions"))
    if isinstance(extensions.get(EXTENSION_ID), dict):
        return _dict(extensions.get(EXTENSION_ID))
    payloads = _dict(value.get("payloads"))
    if isinstance(payloads.get(EXTENSION_ID), dict):
        return _dict(payloads.get(EXTENSION_ID))
    return {}


def _normalize_persisted_row(raw: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    enabled = _bool(raw.get("enabled"), True)
    runtime_shape = deepcopy(raw)
    runtime_shape["enabled"] = True
    item = normalize_video_lora_row(runtime_shape, index)
    if item:
        item["enabled"] = enabled
        source_record_id = str(raw.get("source_record_id") or raw.get("record_id") or "").strip()
        if source_record_id:
            item["source_record_id"] = source_record_id
    return item


def _rows_from_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    params = _dict(block.get("params"))
    raw_rows = _list(params.get("loras")) or _list(block.get("rows"))
    rows: list[dict[str, Any]] = []
    used_uids: set[str] = set()
    for index, raw in enumerate(raw_rows):
        item = _normalize_persisted_row(raw, index)
        if not item:
            continue
        uid = str(item.get("uid") or f"video_lora_{index + 1}")
        if uid in used_uids:
            suffix = 2
            candidate = f"{uid}_{suffix}"
            while candidate in used_uids:
                suffix += 1
                candidate = f"{uid}_{suffix}"
            uid = candidate
            item["uid"] = uid
        used_uids.add(uid)
        rows.append(item)
        if len(rows) >= MAX_VIDEO_LORAS:
            break
    return rows


def _requested_block(result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    candidates = (
        request,
        _dict(result.get("request")),
        _dict(result.get("prompt_api_payload")),
        result,
        _dict(result.get("extensions")),
    )
    for candidate in candidates:
        block = _raw_block(candidate)
        if block:
            return deepcopy(block)
    return {}


def _runtime(result: dict[str, Any]) -> dict[str, Any]:
    output_metadata = _dict(result.get("output_metadata"))
    return (
        _dict(result.get("video_lora_stack"))
        or _dict(output_metadata.get("video_lora_stack"))
        or _dict(_dict(result.get("compiled")).get("video_lora_stack"))
    )


def _applied_rows(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(runtime.get("applied"))):
        item = _normalize_persisted_row(raw, index)
        if item:
            item["enabled"] = True
            rows.append(item)
    return rows


def _unresolved_rows(requested: list[dict[str, Any]], runtime: dict[str, Any]) -> list[dict[str, Any]]:
    missing_names: set[str] = set()
    for value in _list(runtime.get("missing")) + _list(runtime.get("missing_loras")):
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("lora_name") or "").strip()
        else:
            name = str(value or "").strip()
        if name:
            missing_names.add(name)
    unresolved: list[dict[str, Any]] = []
    for row in requested:
        if row.get("enabled") and row.get("name") in missing_names:
            unresolved.append({
                "uid": row.get("uid"),
                "name": row.get("name"),
                "reason": "missing_from_live_catalog",
                "repair_actions": ["refresh_catalog", "replace_file", "disable_row", "remove_row"],
            })
    return unresolved


def build_video_lora_persistence(
    result: dict[str, Any] | None,
    request: dict[str, Any] | None,
) -> dict[str, Any]:
    result, _result_retirement = retire_legacy_payload(_dict(result))
    request, _request_retirement = retire_legacy_payload(_dict(request))
    block = _requested_block(result, request)
    rows = _rows_from_block(block)
    runtime = _runtime(result)
    enabled = _bool(block.get("enabled"), bool(rows))
    applied = _applied_rows(runtime)
    unresolved = _unresolved_rows(rows, runtime)
    return {
        "schema_version": VIDEO_LORA_PERSISTENCE_SCHEMA_VERSION,
        "extension_id": EXTENSION_ID,
        "version": int(block.get("version") or VERSION),
        "enabled": enabled,
        "rows": rows,
        "summary": {
            "total": len(rows),
            "enabled": len([row for row in rows if row.get("enabled")]),
            "standard": len([row for row in rows if row.get("role") == "standard"]),
            "speed": len([row for row in rows if row.get("role") == "speed"]),
            "all": len([row for row in rows if row.get("target") == "all"]),
            "high": len([row for row in rows if row.get("target") == "high"]),
            "low": len([row for row in rows if row.get("target") == "low"]),
        },
        "requested": {"enabled": enabled, "rows": deepcopy(rows)},
        "applied": {
            "active": bool(runtime.get("active") or applied),
            "rows": applied,
            "standard_count": int(runtime.get("standard_count") or len([r for r in applied if r.get("role") == "standard"])),
            "speed_count": int(runtime.get("speed_count") or len([r for r in applied if r.get("role") == "speed"])),
        },
        "unresolved": unresolved,
        "legacy_field_writeback": False,
        "universal_stack_writeback": True,
    }


def state_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    record, _retirement = retire_legacy_payload(_dict(record))
    current = _dict(record.get("video_lora_stack"))
    retirement = _dict(current.get("migration"))
    if retirement.get("schema_version") == "neo.video.lora_stack.legacy_retirement.v1":
        migrated = deepcopy(current)
        migrated["schema_version"] = VIDEO_LORA_PERSISTENCE_SCHEMA_VERSION
        migrated["extension_id"] = EXTENSION_ID
        migrated["version"] = int(migrated.get("version") or VERSION)
        migrated["legacy_field_writeback"] = False
        migrated["universal_stack_writeback"] = True
        return migrated
    if current.get("schema_version") == VIDEO_LORA_PERSISTENCE_SCHEMA_VERSION:
        return deepcopy(current)
    return build_video_lora_persistence(record, {"extensions": _dict(record.get("extensions")).get("payloads", {})})


def merge_record_extensions(existing: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(_dict(existing))
    used = [str(item) for item in _list(merged.get("used")) if str(item)]
    payloads = deepcopy(_dict(merged.get("payloads")))
    applied = _dict(state.get("applied"))
    if applied.get("active") and EXTENSION_ID not in used:
        used.append(EXTENSION_ID)
    if state.get("rows"):
        payloads[EXTENSION_ID] = {
            "enabled": bool(state.get("enabled")),
            "version": int(state.get("version") or VERSION),
            "inputs": {},
            "params": {"loras": deepcopy(_list(state.get("rows")))},
            "assets": {},
            "metadata": {
                "source": "video.output.persistence",
                "persistence_schema": VIDEO_LORA_PERSISTENCE_SCHEMA_VERSION,
            },
        }
    merged["used"] = used
    merged["payloads"] = payloads
    return merged


def attach_state_to_replay(payload: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    replay = deepcopy(_dict(payload))
    state = state_from_record({"video_lora_stack": _dict(state)})
    replay["video_lora_stack_state"] = {
        "schema_version": VIDEO_LORA_REPLAY_SCHEMA_VERSION,
        "enabled": bool(state.get("enabled")),
        "rows": deepcopy(_list(state.get("rows"))),
        "unresolved": deepcopy(_list(state.get("unresolved"))),
    }
    extensions = deepcopy(_dict(replay.get("extensions")))
    if state.get("enabled") and state.get("rows"):
        extensions[EXTENSION_ID] = _dict(merge_record_extensions({}, state).get("payloads")).get(EXTENSION_ID)
    else:
        extensions.pop(EXTENSION_ID, None)
    replay["extensions"] = extensions
    for legacy_key in (
        "h3_turbo_enabled", "h3_turbo_lora", "h3_turbo_strength",
        "enable_video_lora", "video_lora_mode", "video_lora_model", "video_lora_strength",
        "video_lora_target", "enable_lightx2v", "high_noise_lora", "low_noise_lora",
        "high_noise_lora_strength", "low_noise_lora_strength",
    ):
        replay.pop(legacy_key, None)
    return replay


def inspector_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    state = state_from_record(record)
    return {
        "recorded": bool(state.get("rows")),
        "enabled": bool(state.get("enabled")),
        "rows": deepcopy(_list(state.get("rows"))),
        "requested": deepcopy(_dict(state.get("requested"))),
        "applied": deepcopy(_dict(state.get("applied"))),
        "unresolved": deepcopy(_list(state.get("unresolved"))),
        "repair_required": bool(state.get("unresolved")),
        "schema_version": state.get("schema_version"),
    }
