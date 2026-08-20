from __future__ import annotations

from copy import deepcopy
from typing import Any

VOICE_BASE_COMMON_CONTRACT_SCHEMA = "neo.voice.base_common_contract.v1"
VOICE_BASE_COMMON_SETTINGS_SCHEMA = "neo.voice.common_settings.v1"
VOICE_BASE_COMMON_PHASE = "VO-R2"

VOICE_COMMON_DEFAULTS: dict[str, Any] = {
    "script": "",
    "language": "en",
    "model_id": "provider_default",
    "voice_id": "provider_default",
    "speaking_rate": 1.0,
    "output_format": "wav",
    "split_long_text": True,
    "max_chunk_chars": 650,
    "punctuation_cleanup": True,
}

VOICE_COMMON_LIMITS = {
    "script_max_chars": 100_000,
    "language_max_chars": 32,
    "model_id_max_chars": 256,
    "voice_id_max_chars": 256,
    "speaking_rate_min": 0.5,
    "speaking_rate_max": 2.0,
    "max_chunk_chars_min": 160,
    "max_chunk_chars_max": 2400,
}

VOICE_COMMON_OUTPUT_FORMATS = ["wav", "mp3"]
VOICE_COMMON_FIELD_IDS = [
    "script",
    "language",
    "model_id",
    "voice_id",
    "speaking_rate",
    "output_format",
    "split_long_text",
    "max_chunk_chars",
    "punctuation_cleanup",
]

# Historical Voice code still carries these fields. They are deliberately *not*
# part of the provider-neutral R2 contract and must stay capability/provider owned.
VOICE_PROVIDER_NATIVE_EXCLUSIONS = [
    "delivery_notes",
    "expression_strength",
    "reference_strength",
    "seed",
    "voice_source_type",
    "saved_profile_id",
    "reference_audio",
    "reference_id",
    "reference_qc",
    "pause_handling",
    "artifact_cleanup",
    "tag_blocks",
    "prosody",
    "backend_native",
    "speaker_blocks",
    "speaker_mapping",
]


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False
    if value is None:
        return default
    return bool(value)


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> tuple[float, bool]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default), value not in {None, ""}
    clamped = max(minimum, min(maximum, parsed))
    return clamped, clamped != parsed


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> tuple[int, bool]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default), value not in {None, ""}
    clamped = max(minimum, min(maximum, parsed))
    return clamped, clamped != parsed


def _bounded_text(value: Any, default: str, maximum: int) -> tuple[str, bool]:
    text = str(default if value is None else value).strip()
    if len(text) <= maximum:
        return text, False
    return text[:maximum], True


def _common_source(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    if isinstance(data.get("common_settings"), dict):
        return data["common_settings"]
    if isinstance(data.get("common"), dict):
        return data["common"]
    return data


def normalize_voice_common_settings(payload: dict[str, Any] | None = None, *, require_script: bool = False) -> dict[str, Any]:
    raw = _common_source(payload)
    defaults = VOICE_COMMON_DEFAULTS
    limits = VOICE_COMMON_LIMITS
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    script = str(raw.get("script", raw.get("script_body", defaults["script"])) or "")
    if len(script) > limits["script_max_chars"]:
        script = script[: limits["script_max_chars"]]
        warnings.append({"field": "script", "code": "clamped", "message": f"Script was limited to {limits['script_max_chars']} characters."})
    if require_script and not script.strip():
        errors.append({"field": "script", "code": "required", "message": "Script is required for Voice generation."})

    language, language_trimmed = _bounded_text(raw.get("language", defaults["language"]), defaults["language"], limits["language_max_chars"])
    if not language:
        language = defaults["language"]
    if language_trimmed:
        warnings.append({"field": "language", "code": "clamped", "message": "Language/locale value was shortened."})

    model_id, model_trimmed = _bounded_text(raw.get("model_id", defaults["model_id"]), defaults["model_id"], limits["model_id_max_chars"])
    model_id = model_id or defaults["model_id"]
    if model_trimmed:
        warnings.append({"field": "model_id", "code": "clamped", "message": "Model identifier was shortened."})

    voice_id, voice_trimmed = _bounded_text(raw.get("voice_id", defaults["voice_id"]), defaults["voice_id"], limits["voice_id_max_chars"])
    voice_id = voice_id or defaults["voice_id"]
    if voice_trimmed:
        warnings.append({"field": "voice_id", "code": "clamped", "message": "Voice identifier was shortened."})

    speaking_rate, speaking_rate_clamped = _clamp_float(
        raw.get("speaking_rate", defaults["speaking_rate"]),
        defaults["speaking_rate"],
        limits["speaking_rate_min"],
        limits["speaking_rate_max"],
    )
    if speaking_rate_clamped:
        warnings.append({"field": "speaking_rate", "code": "clamped", "message": f"Speaking rate must stay between {limits['speaking_rate_min']} and {limits['speaking_rate_max']}."})

    output_format = str(raw.get("output_format", defaults["output_format"]) or defaults["output_format"]).strip().lower()
    if output_format not in VOICE_COMMON_OUTPUT_FORMATS:
        warnings.append({"field": "output_format", "code": "fallback", "message": f"Unsupported output format '{output_format}' fell back to WAV."})
        output_format = defaults["output_format"]

    split_long_text = _bool(raw.get("split_long_text"), defaults["split_long_text"])
    punctuation_cleanup = _bool(raw.get("punctuation_cleanup"), defaults["punctuation_cleanup"])
    max_chunk_chars, max_chunk_clamped = _clamp_int(
        raw.get("max_chunk_chars", defaults["max_chunk_chars"]),
        defaults["max_chunk_chars"],
        limits["max_chunk_chars_min"],
        limits["max_chunk_chars_max"],
    )
    if max_chunk_clamped:
        warnings.append({"field": "max_chunk_chars", "code": "clamped", "message": f"Chunk size must stay between {limits['max_chunk_chars_min']} and {limits['max_chunk_chars_max']} characters."})

    normalized = {
        "script": script,
        "language": language,
        "model_id": model_id,
        "voice_id": voice_id,
        "speaking_rate": speaking_rate,
        "output_format": output_format,
        "split_long_text": split_long_text,
        "max_chunk_chars": max_chunk_chars,
        "punctuation_cleanup": punctuation_cleanup,
    }
    return {
        "schema_id": VOICE_BASE_COMMON_SETTINGS_SCHEMA,
        "phase": VOICE_BASE_COMMON_PHASE,
        "status": "valid" if not errors else "invalid",
        "ready_for_generation": bool(script.strip()) and not errors,
        "common_settings": normalized,
        "errors": errors,
        "warnings": warnings,
        "provider_native_fields_ignored": sorted(set(raw).intersection(VOICE_PROVIDER_NATIVE_EXCLUSIONS)),
    }


def voice_base_common_contract_payload() -> dict[str, Any]:
    return {
        "schema_id": VOICE_BASE_COMMON_CONTRACT_SCHEMA,
        "phase": VOICE_BASE_COMMON_PHASE,
        "surface": "voice",
        "status": "active_common_settings_contract",
        "mode": "tts",
        "authority": "provider_neutral_voice_draft",
        "defaults": deepcopy(VOICE_COMMON_DEFAULTS),
        "limits": deepcopy(VOICE_COMMON_LIMITS),
        "output_formats": list(VOICE_COMMON_OUTPUT_FORMATS),
        "common_field_ids": list(VOICE_COMMON_FIELD_IDS),
        "groups": [
            {
                "group_id": "script",
                "label": "Script",
                "fields": [
                    {"field_id": "script", "label": "Script", "type": "multiline_text", "required_for_generation": True, "max_chars": VOICE_COMMON_LIMITS["script_max_chars"]},
                    {"field_id": "language", "label": "Language", "type": "text", "default": VOICE_COMMON_DEFAULTS["language"], "provider_mapping_phase": "VO-R3"},
                ],
            },
            {
                "group_id": "selection",
                "label": "Voice Selection",
                "fields": [
                    {"field_id": "model_id", "label": "Model", "type": "provider_catalog_reference", "default": "provider_default", "catalog_authority_phase": "VO-R3"},
                    {"field_id": "voice_id", "label": "Voice / Speaker", "type": "provider_catalog_reference", "default": "provider_default", "catalog_authority_phase": "VO-R3"},
                ],
            },
            {
                "group_id": "delivery",
                "label": "Delivery",
                "fields": [
                    {"field_id": "speaking_rate", "label": "Speaking Rate", "type": "number", "default": 1.0, "minimum": 0.5, "maximum": 2.0, "step": 0.05},
                    {"field_id": "output_format", "label": "Output Format", "type": "select", "default": "wav", "options": list(VOICE_COMMON_OUTPUT_FORMATS)},
                ],
            },
            {
                "group_id": "script_processing",
                "label": "Script Processing",
                "fields": [
                    {"field_id": "split_long_text", "label": "Split Long Text", "type": "boolean", "default": True},
                    {"field_id": "max_chunk_chars", "label": "Maximum Chunk Size", "type": "integer", "default": 650, "minimum": 160, "maximum": 2400, "depends_on": "split_long_text"},
                    {"field_id": "punctuation_cleanup", "label": "Punctuation Cleanup", "type": "boolean", "default": True},
                ],
            },
        ],
        "provider_native_exclusions": list(VOICE_PROVIDER_NATIVE_EXCLUSIONS),
        "release_boundary": {
            "ui_editable": True,
            "generation_execution": True,
            "generation_endpoint": "POST /api/voice/generate",
            "generation_poll_endpoint": "GET /api/voice/generation/jobs/{job_id}",
            "provider_capability_routing": True,
            "provider_routing_endpoint": "GET /api/voice/provider-routing",
            "provider_native_controls": True,
            "provider_controls_endpoint": "GET /api/voice/provider-controls",
            "provider_controls_policy": "selected_profile_capability_manifest_only; nested_provider_controls_only; cross_backend_replay_clears",
            "voice_clone": True,
            "reference_assets": True,
            "reference_list_endpoint": "GET /api/voice/references",
            "reference_upload_endpoint": "POST /api/voice/references/upload",
            "clone_generation_endpoint": "POST /api/voice/clone/generate",
            "clone_poll_endpoint": "GET /api/voice/clone/jobs/{job_id}",
            "dialogue": True,
            "dialogue_capabilities_endpoint": "GET /api/voice/dialogue-runtime/capabilities",
            "dialogue_parse_endpoint": "POST /api/voice/dialogue-runtime/parse",
            "dialogue_generation_endpoint": "POST /api/voice/dialogue-runtime/generate",
            "dialogue_poll_endpoint": "GET /api/voice/dialogue-runtime/jobs/{job_id}",
            "dialogue_policy": "one_selected_backend_profile_per_dialogue; R4/R6_child_jobs; real_ffmpeg_stitch; no_placeholder_audio; speaker_sources_revalidated_on_replay",
            "batch": True,
            "batch_capabilities_endpoint": "GET /api/voice/batch-runtime/capabilities",
            "batch_import_endpoint": "POST /api/voice/batch-runtime/import",
            "batch_run_endpoint": "POST /api/voice/batch-runtime/{batch_id}/run",
            "batch_poll_endpoint": "GET /api/voice/batch-runtime/{batch_id}/poll",
            "batch_history_endpoint": "GET /api/voice/batch-runtime/history",
            "batch_retry_endpoint": "POST /api/voice/batch-runtime/{batch_id}/retry-item",
            "batch_policy": "one_selected_backend_profile; bounded_concurrency; current_R4_R6_R10_child_jobs_only; no_native_batch_api_required; no_placeholder_audio",
            "finish_processing": True,
            "finish_capabilities_endpoint": "GET /api/voice/finish-runtime/capabilities",
            "finish_process_endpoint": "POST /api/voice/finish-runtime/process",
            "finish_split_endpoint": "POST /api/voice/finish-runtime/split",
            "finish_merge_endpoint": "POST /api/voice/finish-runtime/merge",
            "finish_history_endpoint": "GET /api/voice/finish-runtime/history",
            "finish_policy": "provider_independent_neo_owned_audio_only_no_placeholder_outputs_shared_registry_child_lineage",
            "current_phase": "VO-R11",
            "next_phase": "VO-R12",
            "voice_profile_assets": True,
            "profile_assets_endpoint": "GET /api/voice/profile-assets",
            "profile_asset_create_endpoint": "POST /api/voice/profile-assets",
            "profile_asset_apply_endpoint": "POST /api/voice/profile-assets/{asset_id}/apply",
            "profile_asset_policy": "never_auto_switch_backend; script_and_provider_native_controls_not_stored; legacy_v7_profiles_not_auto_promoted",
            "preview_results": True,
            "results_endpoint": "GET /api/voice/results",
            "result_detail_endpoint": "GET /api/voice/results/{job_id}",
            "result_replay_endpoint": "GET /api/voice/results/{job_id}/replay",
        },
    }
