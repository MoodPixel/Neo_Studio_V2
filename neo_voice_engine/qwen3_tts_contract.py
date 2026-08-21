from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

SCHEMA_ID = "neo.voice_engine.qwen3_tts.contract.v1"
SCHEMA_VERSION = 1
PHASE = "qwen3_tts_capability_runtime_contract_audit"
EXPECTED_MODEL_IDS = {
    "qwen3_tts_17b_custom_voice",
    "qwen3_tts_06b_custom_voice",
    "qwen3_tts_17b_base",
    "qwen3_tts_06b_base",
    "qwen3_tts_17b_voice_design",
}
EXPECTED_LANGUAGES = {"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"}
EXPECTED_SPEAKERS = {"vivian", "serena", "uncle_fu", "dylan", "eric", "ryan", "aiden", "ono_anna", "sohee"}
_DATA_PATH = Path(__file__).resolve().parent / "contracts" / "qwen3_tts_phase1.json"


class Qwen3TTSContractError(ValueError):
    pass


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Qwen3TTSContractError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise Qwen3TTSContractError(f"{field} must be an array")
    return value


def validate_contract(payload: dict[str, Any]) -> dict[str, Any]:
    root = _require_object(payload, "contract")
    if root.get("schema_id") != SCHEMA_ID:
        raise Qwen3TTSContractError(f"schema_id must be {SCHEMA_ID}")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise Qwen3TTSContractError(f"schema_version must be {SCHEMA_VERSION}")
    if root.get("phase") != PHASE:
        raise Qwen3TTSContractError(f"phase must be {PHASE}")
    if root.get("behavioral_activation") is not False:
        raise Qwen3TTSContractError("Phase 1 must remain non-behavioral")

    engine = _require_object(root.get("engine_contract"), "engine_contract")
    if engine.get("candidate_engine_id") != "qwen3_tts":
        raise Qwen3TTSContractError("candidate engine must be qwen3_tts")
    if engine.get("environment_scope") != "voice_runtime":
        raise Qwen3TTSContractError("Qwen3-TTS must use the isolated voice_runtime scope")
    if engine.get("active_manifest_created") is not False:
        raise Qwen3TTSContractError("Phase 1 may not create an active manifest")
    if engine.get("worker_created") is not False or engine.get("ui_wired") is not False:
        raise Qwen3TTSContractError("Phase 1 may not create a worker or UI wiring")

    models = _require_list(root.get("models"), "models")
    ids = {str(item.get("neo_model_id")) for item in models if isinstance(item, dict)}
    if ids != EXPECTED_MODEL_IDS:
        raise Qwen3TTSContractError(f"model IDs mismatch: {sorted(ids)}")
    if len(models) != len(ids):
        raise Qwen3TTSContractError("model IDs must be unique")

    for model in models:
        item = _require_object(model, "models[]")
        if item.get("upstream_streaming") is not True:
            raise Qwen3TTSContractError(f"{item.get('neo_model_id')} must record upstream streaming support")
        if item.get("initial_neo_streaming") is not False:
            raise Qwen3TTSContractError(f"{item.get('neo_model_id')} may not activate streaming in Phase 1")
        hardware = _require_object(item.get("hardware_validation"), f"{item.get('neo_model_id')}.hardware_validation")
        if hardware.get("min_vram_mb") is not None or hardware.get("recommended_vram_mb") is not None:
            raise Qwen3TTSContractError("Phase 1 may not invent Qwen3-TTS VRAM admission thresholds")
        if hardware.get("rtx_12gb_status") != "pending_physical_validation":
            raise Qwen3TTSContractError("12 GB GPU compatibility must remain pending physical validation")

        role = item.get("role")
        tasks = set(item.get("neo_tasks_target") or [])
        if role == "base_clone":
            if tasks != {"voice_clone"} or item.get("reference_audio") is not True:
                raise Qwen3TTSContractError("Base models must be clone-only targets with reference audio in Phase 1")
            clone = _require_object(item.get("clone_contract"), f"{item.get('neo_model_id')}.clone_contract")
            if clone.get("icl", {}).get("reference_text_required") is not True:
                raise Qwen3TTSContractError("ICL clone mode must require reference transcript")
            if clone.get("x_vector_only", {}).get("reference_text_required") is not False:
                raise Qwen3TTSContractError("x-vector-only clone mode must allow audio-only reference")
        elif role == "custom_voice":
            if tasks != {"tts"} or item.get("built_in_speakers") is not True:
                raise Qwen3TTSContractError("CustomVoice models must target built-in-speaker TTS")
        elif role == "voice_design":
            if tasks != {"voice_design"} or item.get("voice_design") is not True:
                raise Qwen3TTSContractError("VoiceDesign must remain a dedicated task, not be tunneled through generic TTS")
        else:
            raise Qwen3TTSContractError(f"unsupported role {role}")

    languages = set(_require_object(root.get("languages"), "languages").get("neo_codes") or [])
    if languages != EXPECTED_LANGUAGES:
        raise Qwen3TTSContractError(f"language set mismatch: {sorted(languages)}")

    speakers_root = _require_object(root.get("built_in_speakers"), "built_in_speakers")
    speakers = {str(item.get("neo_id")) for item in _require_list(speakers_root.get("items"), "built_in_speakers.items") if isinstance(item, dict)}
    if speakers != EXPECTED_SPEAKERS:
        raise Qwen3TTSContractError(f"speaker seed mismatch: {sorted(speakers)}")
    if speakers_root.get("audit_seed_only") is not True:
        raise Qwen3TTSContractError("static speaker list must remain audit-seed-only")

    audit = _require_object(root.get("neo_integration_audit"), "neo_integration_audit")
    blockers = {str(item.get("id")) for item in _require_list(audit.get("current_activation_blockers"), "current_activation_blockers") if isinstance(item, dict)}
    required_blockers = {
        "active_manifest_missing",
        "worker_missing",
        "gateway_mode_limit",
        "provider_controls_mode_limit",
        "static_family_fallback",
        "neo_voice_engine_alias_scope",
        "voice_source_option_gap",
        "hardware_admission_unknown",
    }
    if not required_blockers.issubset(blockers):
        raise Qwen3TTSContractError(f"missing activation blockers: {sorted(required_blockers - blockers)}")
    return root


def load_contract(path: Path | None = None) -> dict[str, Any]:
    source = path or _DATA_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    return validate_contract(payload)


def contract_payload() -> dict[str, Any]:
    return deepcopy(load_contract())


def model_contract(model_id: str) -> dict[str, Any] | None:
    requested = str(model_id or "").strip().lower()
    for item in load_contract().get("models") or []:
        if str(item.get("neo_model_id") or "").lower() == requested:
            return deepcopy(item)
    return None


def audit_summary() -> dict[str, Any]:
    payload = load_contract()
    models = payload["models"]
    return {
        "schema_id": "neo.voice_engine.qwen3_tts.phase1.audit_summary.v1",
        "behavioral_activation": False,
        "candidate_engine_id": payload["engine_contract"]["candidate_engine_id"],
        "model_count": len(models),
        "custom_voice_models": sum(item.get("role") == "custom_voice" for item in models),
        "clone_models": sum(item.get("role") == "base_clone" for item in models),
        "voice_design_models": sum(item.get("role") == "voice_design" for item in models),
        "language_count": len(payload["languages"]["neo_codes"]),
        "speaker_seed_count": len(payload["built_in_speakers"]["items"]),
        "activation_blocker_count": len(payload["neo_integration_audit"]["current_activation_blockers"]),
        "active_manifest_created": payload["engine_contract"]["active_manifest_created"],
        "worker_created": payload["engine_contract"]["worker_created"],
        "ui_wired": payload["engine_contract"]["ui_wired"],
        "streaming_activation": payload["engine_contract"]["neo_streaming_activation"],
    }


__all__ = [
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "PHASE",
    "EXPECTED_MODEL_IDS",
    "EXPECTED_LANGUAGES",
    "EXPECTED_SPEAKERS",
    "Qwen3TTSContractError",
    "validate_contract",
    "load_contract",
    "contract_payload",
    "model_contract",
    "audit_summary",
]
