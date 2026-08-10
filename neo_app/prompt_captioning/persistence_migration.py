from __future__ import annotations

from copy import deepcopy
from typing import Any

from .profile_contract import PROFILE_SCHEMA_VERSION, normalize_profile, resolve_profile_surface

PERSISTENCE_SCHEMA_VERSION = "prompt_captioning.persistence.v2"
MIGRATION_SCHEMA_VERSION = "prompt_captioning.storage_migration.v1"

PROFILE_BEARING_KINDS = {
    "saved_prompts",
    "prompt_history",
    "prompt_presets",
    "saved_captions",
    "caption_history",
    "caption_presets",
    "caption_batch_results",
    "result_metadata",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _profile_from_nested_record(record: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(record.get("profile"))
    if direct:
        return direct
    settings = _dict(record.get("settings"))
    settings_profile = _dict(settings.get("profile"))
    if settings_profile:
        return settings_profile
    payload = _dict(record.get("payload"))
    payload_profile = _dict(payload.get("profile"))
    if payload_profile:
        return payload_profile
    result_metadata = _dict(record.get("result_metadata"))
    meta_profile = _dict(result_metadata.get("profile"))
    if meta_profile:
        return meta_profile
    replay = _dict(record.get("replay_payload"))
    replay_profile = _dict(replay.get("profile"))
    if replay_profile:
        return replay_profile
    return {}


def _workflow_mode(record: dict[str, Any]) -> str:
    payload = _dict(record.get("payload"))
    inputs = _dict(payload.get("inputs"))
    params = _dict(payload.get("params"))
    return _clean(
        record.get("workflow_mode")
        or inputs.get("workflow_mode")
        or params.get("workflow_mode")
        or _dict(record.get("metadata")).get("workflow_mode")
    ).lower().replace("-", "_").replace(" ", "_")


def infer_record_surface(kind: str, record: dict[str, Any]) -> str:
    existing_profile = _profile_from_nested_record(record)
    existing_surface = _clean(existing_profile.get("surface"))
    if existing_surface in {"prompt_studio", "caption_studio", "batch_dataset", "batch_library"}:
        return existing_surface

    safe_kind = _clean(kind)
    if safe_kind in {"saved_prompts", "prompt_history", "prompt_presets"}:
        return "prompt_studio"
    if safe_kind == "caption_batch_results":
        return "batch_library" if _workflow_mode(record) in {"library", "save_to_library", "save_library", "library_caption"} else "batch_dataset"
    if safe_kind in {"saved_captions", "caption_history", "caption_presets"}:
        origin = _clean(record.get("library_origin") or record.get("origin") or record.get("source_origin")).lower().replace("-", "_")
        if origin in {"batch", "batch_captioning", "library_batch"}:
            return "batch_library"
        return "caption_studio"
    if safe_kind == "result_metadata":
        replay = _dict(record.get("replay_payload"))
        payload = replay or {
            "tool": record.get("tool_id") or record.get("tool") or "",
            "mode": record.get("mode") or "",
            "metadata": record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
        }
        return resolve_profile_surface(_clean(record.get("tool_id") or record.get("tool")), payload)
    return "caption_studio"


def _legacy_payload_for_record(kind: str, record: dict[str, Any], surface: str) -> dict[str, Any]:
    nested_payload = deepcopy(_dict(record.get("payload")))
    if nested_payload:
        nested_payload.setdefault("profile", _profile_from_nested_record(record))
        return nested_payload

    replay = deepcopy(_dict(record.get("replay_payload"))) if kind == "result_metadata" else {}
    if replay:
        replay.setdefault("profile", _profile_from_nested_record(record))
        return replay

    inputs: dict[str, Any] = {}
    params: dict[str, Any] = {}
    settings = _dict(record.get("settings"))

    # Prompt Studio legacy values.
    if record.get("style") not in {None, ""}:
        inputs["style"] = record.get("style")
    if record.get("target_media") not in {None, ""}:
        inputs["target_media"] = record.get("target_media")
    if record.get("prompt_task") not in {None, ""}:
        inputs["prompt_task"] = record.get("prompt_task")

    # Caption Studio / batch legacy values.
    legacy_input_keys = {
        "target_use": "target_use",
        "purpose": "purpose",
        "output_style": "output_style",
        "visual_treatment": "visual_treatment",
        "caption_style": "caption_style",
        "output_format": "output_format",
        "grounding": "grounding",
        "edit_intent": "edit_intent",
        "preservation_policy": "preservation_policy",
        "motion_profile": "motion_profile",
        "camera_behavior": "camera_behavior",
    }
    for source_key, target_key in legacy_input_keys.items():
        if record.get(source_key) not in {None, ""}:
            inputs[target_key] = record.get(source_key)

    legacy_param_keys = ("caption_mode", "component_type", "analysis_scope", "detail_level")
    for key in legacy_param_keys:
        if record.get(key) not in {None, ""}:
            params[key] = record.get(key)

    # Existing settings remain a useful source of old fields. Keep them nested in
    # caption_settings so normalize_profile can resolve historical presets.
    caption_settings = {}
    for key in (
        "target_use",
        "purpose",
        "output_style",
        "visual_treatment",
        "caption_style",
        "output_format",
        "caption_mode",
        "component_type",
        "analysis_scope",
        "grounding",
    ):
        value = settings.get(key)
        if value not in {None, ""}:
            caption_settings[key] = value
    if caption_settings:
        params["caption_settings"] = caption_settings

    tool = "prompt_generate" if surface == "prompt_studio" else ("batch_captioning" if surface.startswith("batch_") else "image_captioning")
    if surface.startswith("batch_"):
        inputs["workflow_mode"] = "library" if surface == "batch_library" else "dataset"

    return {
        "tool": tool,
        "tool_id": tool,
        "inputs": inputs,
        "params": params,
        "metadata": deepcopy(_dict(record.get("metadata"))),
        "profile": deepcopy(_profile_from_nested_record(record)),
    }


def canonical_profile_for_record(kind: str, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind not in PROFILE_BEARING_KINDS:
        return {}, {"migrated": False, "used_legacy_aliases": [], "unmapped_values": {}}
    surface = infer_record_surface(kind, record)
    payload = _legacy_payload_for_record(kind, record, surface)
    result = normalize_profile(_profile_from_nested_record(record), payload=payload, tool_id=_clean(payload.get("tool") or payload.get("tool_id")), surface=surface)
    return deepcopy(_dict(result.get("profile"))), deepcopy(_dict(result.get("migration")))


def _normalize_replay_payload(replay: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(replay)
    if not clean:
        return clean
    clean["schema_version"] = "prompt_captioning.replay_payload.v2"
    clean["profile"] = deepcopy(profile)
    metadata = deepcopy(_dict(clean.get("metadata")))
    metadata.setdefault("profile_schema_version", PROFILE_SCHEMA_VERSION)
    clean["metadata"] = metadata
    return clean


def normalize_persisted_record(kind: str, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a canonical, non-destructive view of one persisted record.

    The original legacy fields remain present for compatibility. The canonical
    P23 profile is materialized at top level and mirrored into known nested
    profile locations so saved records, presets, history, snapshots, and replay
    all resolve the same task intent.
    """
    source = deepcopy(_dict(record))
    if kind not in PROFILE_BEARING_KINDS:
        return source, {"migrated": False, "changed": False, "used_legacy_aliases": [], "unmapped_values": {}}

    profile, migration = canonical_profile_for_record(kind, source)
    before_profile = _profile_from_nested_record(source)
    source["profile"] = deepcopy(profile)
    source["profile_schema_version"] = PROFILE_SCHEMA_VERSION
    source["persistence_schema_version"] = PERSISTENCE_SCHEMA_VERSION

    settings = deepcopy(_dict(source.get("settings")))
    if settings or kind in {"saved_prompts", "prompt_presets", "saved_captions", "caption_presets"}:
        settings["profile"] = deepcopy(profile)
        source["settings"] = settings

    metadata = deepcopy(_dict(source.get("metadata")))
    if metadata or kind in {"saved_prompts", "saved_captions", "result_metadata"}:
        metadata["profile_schema_version"] = PROFILE_SCHEMA_VERSION
        source["metadata"] = metadata

    if kind in {"prompt_history", "caption_history", "caption_batch_results"}:
        payload = deepcopy(_dict(source.get("payload")))
        if payload:
            payload["profile"] = deepcopy(profile)
            source["payload"] = payload

    if kind == "result_metadata":
        source["replay_payload"] = _normalize_replay_payload(_dict(source.get("replay_payload")), profile)

    changed = before_profile != profile or source.get("profile_schema_version") != record.get("profile_schema_version") or source.get("persistence_schema_version") != record.get("persistence_schema_version")
    report = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migrated": bool(migration.get("migrated") or changed),
        "changed": bool(changed),
        "used_legacy_aliases": deepcopy(migration.get("used_legacy_aliases") or []),
        "unmapped_values": deepcopy(migration.get("unmapped_values") or {}),
        "surface": profile.get("surface") or "",
    }
    if report["migrated"]:
        source["profile_migration"] = report
    return source, report


def normalize_persisted_records(kind: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    changed = 0
    migrated = 0
    aliases = 0
    unmapped = 0
    for record in records:
        clean, report = normalize_persisted_record(kind, record)
        normalized.append(clean)
        changed += 1 if report.get("changed") else 0
        migrated += 1 if report.get("migrated") else 0
        aliases += len(report.get("used_legacy_aliases") or [])
        unmapped += len(report.get("unmapped_values") or {})
    return normalized, {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "kind": kind,
        "records": len(records),
        "changed": changed,
        "migrated": migrated,
        "legacy_aliases": aliases,
        "unmapped_values": unmapped,
    }
