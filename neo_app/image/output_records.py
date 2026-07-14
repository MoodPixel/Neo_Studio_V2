from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.image.output_paths import normalize_image_output_category, sanitize_path_part

IMAGE_OUTPUT_RECORD_SCHEMA_VERSION: Final[str] = "neo.image.output.v1"
EXTENSION_METADATA_KEYS: Final[tuple[str, ...]] = (
    "used",
    "payloads",
    "workflow_patches",
    "validation",
    "replay_payloads",
    "assistant_summaries",
    "memory_events",
    "timing",
)

IMAGE_RUN_TIMING_SCHEMA_VERSION: Final[str] = "neo.image.run_timing.v1"
IMAGE_EXTENSION_TIMING_IDS: Final[tuple[str, ...]] = (
    "image.controlnet",
    "image.ip_adapter",
    "image.adetailer",
    "image.high_res_lab",
    "image.scene_director",
    "image.background_removal",
)

LATENT_CAPTURE_MODES: Final[tuple[str, ...]] = ("off", "final_latent", "milestone_checkpoints", "full_debug_checkpoints")
LATENT_RESTORE_POINT_LABELS: Final[dict[str, str]] = {
    "base_generation_only": "Base generation only",
    "before_high_res_fix": "Before High-Res Fix",
    "after_high_res_fix": "After High-Res Fix",
    "before_adetailer": "Before ADetailer",
    "final_latent": "Final latent",
}


def normalize_latent_capture_request(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the IMG-R7 latent capture request contract carried by job params.

    R7 only records intent/capability metadata. Providers must later append real
    artifacts before Neo can expose latent resume or phase checkpoint restore.
    """
    source = params if isinstance(params, dict) else {}
    raw = source.get("_neo_latent_capture") if isinstance(source.get("_neo_latent_capture"), dict) else {}
    mode = str(raw.get("mode") or source.get("latent_capture_mode") or "off").strip().lower()
    if mode not in LATENT_CAPTURE_MODES:
        mode = "off"
    requested = bool(raw.get("requested") or mode != "off")
    milestones = raw.get("requested_restore_points")
    if not isinstance(milestones, list):
        if mode == "final_latent":
            milestones = ["final_latent"]
        elif mode in ("milestone_checkpoints", "full_debug_checkpoints"):
            milestones = ["base_generation_only", "before_high_res_fix", "after_high_res_fix", "before_adetailer", "final_latent"]
        else:
            milestones = []
    requested_restore_points = [str(item) for item in milestones if str(item) in LATENT_RESTORE_POINT_LABELS]
    return {
        "schema_version": "neo.image.latent_capture_request.v1",
        "requested": requested,
        "mode": mode,
        "requested_restore_points": requested_restore_points,
        "storage_policy": str(raw.get("storage_policy") or ("metadata_only_until_provider_hook" if requested else "off")),
        "provider_hook_required": True,
        "state": "requested_pending_provider_support" if requested else "off",
    }


def normalize_latent_capture_artifacts(params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Sanitize provider-owned latent artifact manifests for future R8/R9 support."""
    source = params if isinstance(params, dict) else {}
    raw_items = source.get("_neo_latent_artifacts") if isinstance(source.get("_neo_latent_artifacts"), list) else []
    artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        restore_point = str(item.get("restore_point") or item.get("phase") or "").strip()
        if restore_point not in LATENT_RESTORE_POINT_LABELS:
            continue
        path = str(item.get("path") or item.get("relative_path") or "").strip()
        artifact = {
            "artifact_id": str(item.get("artifact_id") or f"latent_{index}"),
            "restore_point": restore_point,
            "label": LATENT_RESTORE_POINT_LABELS[restore_point],
            "kind": str(item.get("kind") or "latent_tensor"),
            "path": path,
            "format": str(item.get("format") or "safetensors"),
            "provider_owned": bool(item.get("provider_owned", True)),
            "provider_id": str(item.get("provider_id") or ""),
            "backend": str(item.get("backend") or ""),
            "source_node_id": str(item.get("source_node_id") or item.get("node_id") or ""),
            "source_filename": str(item.get("source_filename") or item.get("filename") or ""),
            "source_subfolder": str(item.get("source_subfolder") or item.get("subfolder") or ""),
            "source_type": str(item.get("source_type") or item.get("type") or ""),
            "comfy_load_name": str(item.get("comfy_load_name") or item.get("load_latent_name") or ""),
            "state": "available" if path else "manifest_only",
        }
        artifacts.append(artifact)
    return artifacts


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _elapsed_label_from_seconds(value: Any) -> str:
    try:
        seconds = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining = seconds % 60
    if hours:
        return f"{hours}h {minutes:02d}m {remaining:02d}s"
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"


def normalize_run_timing(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a safe Image output timing block for metadata/inspector use only."""
    source = value if isinstance(value, dict) else {}
    elapsed = source.get("elapsed_seconds")
    if elapsed in (None, "") and source.get("elapsed_ms") not in (None, ""):
        try:
            elapsed = float(source.get("elapsed_ms") or 0) / 1000.0
        except (TypeError, ValueError):
            elapsed = None
    try:
        elapsed_seconds = round(float(elapsed), 3) if elapsed not in (None, "") else 0.0
    except (TypeError, ValueError):
        elapsed_seconds = 0.0
    label = str(source.get("elapsed_label") or _elapsed_label_from_seconds(elapsed_seconds) or "")
    if elapsed_seconds <= 0 and not any(source.get(key) for key in ("started_at", "queued_at", "completed_at")):
        return {}
    normalized = {
        "schema_version": IMAGE_RUN_TIMING_SCHEMA_VERSION,
        "surface": "image",
        "timing_source": str(source.get("timing_source") or "provider_wall_clock"),
        "started_at": str(source.get("started_at") or source.get("queued_at") or ""),
        "queued_at": str(source.get("queued_at") or source.get("started_at") or ""),
        "completed_at": str(source.get("completed_at") or ""),
        "elapsed_seconds": elapsed_seconds,
        "elapsed_label": label,
        "state": str(source.get("state") or ("completed" if source.get("completed_at") else "running")),
        "replay_used": False,
    }
    if isinstance(source.get("phases"), dict):
        normalized["phases"] = deepcopy(source.get("phases"))
    if isinstance(source.get("notes"), list):
        normalized["notes"] = [str(item) for item in source.get("notes") if str(item or "").strip()]
    return normalized


def extension_metadata_with_run_timing(extensions: dict[str, Any] | None, run_timing: dict[str, Any] | None) -> dict[str, Any]:
    """Attach truthful timing hints to Image extension metadata.

    Comfy history gives reliable whole-run wall clock timing in this adapter. It
    does not expose stable per-extension elapsed time yet, so each participating
    extension receives an attribution block that points to the shared run total
    instead of pretending to know per-node timings.
    """
    normalized = normalize_extension_metadata(extensions)
    timing = normalize_run_timing(run_timing)
    if not timing:
        return normalized
    normalized["timing"] = {
        "schema_version": "neo.image.extension_timing.v1",
        "surface": "image",
        "run_timing": deepcopy(timing),
        "per_extension_source": "workflow_total_only",
        "per_extension_elapsed_available": False,
        "notes": [
            "Total run time is measured from provider queue to completed poll.",
            "Individual extension timings need websocket/node execution telemetry before they can be reported as exact durations.",
        ],
    }
    used = normalized.get("used") if isinstance(normalized.get("used"), list) else []
    payloads = normalized.get("payloads") if isinstance(normalized.get("payloads"), dict) else {}
    seen = {str(item.get("extension_id") or "") for item in used if isinstance(item, dict)}
    for extension_id in IMAGE_EXTENSION_TIMING_IDS:
        payload = payloads.get(extension_id) if isinstance(payloads, dict) else None
        has_payload = isinstance(payload, dict) and bool(payload)
        if not has_payload and extension_id not in seen:
            continue
        if extension_id not in seen:
            used.append({"extension_id": extension_id, "status": "metadata_recorded"})
            seen.add(extension_id)
    for item in used:
        if not isinstance(item, dict):
            continue
        extension_id = str(item.get("extension_id") or "")
        if extension_id in IMAGE_EXTENSION_TIMING_IDS:
            item["timing"] = {
                "schema_version": "neo.image.extension_timing_entry.v1",
                "source": "image_run_total",
                "included_in_total_run": True,
                "individual_elapsed_available": False,
                "individual_elapsed_seconds": None,
                "run_elapsed_seconds": timing.get("elapsed_seconds", 0),
                "elapsed_label": timing.get("elapsed_label", ""),
                "note": "Exact per-extension duration is not available from Comfy history yet; this extension participated in the recorded whole image run.",
            }
    normalized["used"] = used
    return normalized


def empty_extension_metadata() -> dict[str, Any]:
    """Return the core-owned extension metadata slots for an output record.

    Extensions may append into these slots during later phases, but they must not
    replace the outer Image output record schema.
    """
    return {
        "used": [],
        "payloads": {},
        "workflow_patches": [],
        "validation": [],
        "replay_payloads": {},
        "assistant_summaries": {},
        "memory_events": {},
        "timing": {},
    }


def normalize_extension_metadata(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize extension metadata while preserving only approved append slots."""
    normalized = empty_extension_metadata()
    source = value if isinstance(value, dict) else {}

    used = source.get("used")
    if isinstance(used, list):
        normalized["used"] = [normalize_extension_usage(item) for item in used if normalize_extension_usage(item)]

    payloads = source.get("payloads")
    if isinstance(payloads, dict):
        normalized["payloads"] = deepcopy(payloads)

    patches = source.get("workflow_patches")
    if isinstance(patches, list):
        normalized["workflow_patches"] = [deepcopy(item) for item in patches if isinstance(item, dict)]

    validation = source.get("validation")
    if isinstance(validation, list):
        normalized["validation"] = [deepcopy(item) for item in validation if isinstance(item, dict)]

    replay_payloads = source.get("replay_payloads")
    if isinstance(replay_payloads, dict):
        normalized["replay_payloads"] = deepcopy(replay_payloads)

    assistant_summaries = source.get("assistant_summaries")
    if isinstance(assistant_summaries, dict):
        normalized["assistant_summaries"] = {str(key): str(value) for key, value in assistant_summaries.items() if str(value or "").strip()}

    memory_events = source.get("memory_events")
    if isinstance(memory_events, dict):
        normalized["memory_events"] = deepcopy(memory_events)

    timing = source.get("timing")
    if isinstance(timing, dict):
        normalized["timing"] = deepcopy(timing)

    return normalized


def normalize_extension_usage(item: Any) -> dict[str, Any]:
    """Convert extension usage input into a stable metadata entry."""
    if isinstance(item, str):
        extension_id = sanitize_path_part(item, fallback="extension")
        return {"extension_id": extension_id, "status": "used"}
    if not isinstance(item, dict):
        return {}

    extension_id = str(item.get("extension_id") or item.get("id") or item.get("name") or "").strip()
    if not extension_id:
        return {}

    normalized = {
        "extension_id": sanitize_path_part(extension_id, fallback="extension"),
        "status": str(item.get("status") or "used"),
    }
    for optional_key in (
        "version", "surface", "subtab", "label", "extension_type", "workspace_app", "enabled", "status",
        "workflow_patch_applied", "workflow_patch_allowed", "route", "route_state", "node", "reason",
        "lora_count", "lora_names", "global_base_count", "regional_count", "subject_count", "detail_region_count",
        "ip_adapter_binding_count", "lora_binding_count", "identity_unit_count", "finish_only_count",
        "embedding_count", "embedding_tokens", "positive_count", "negative_count", "finish_count",
        "controlnet_unit_count", "ip_adapter_unit_count", "ip_adapter_standard_count", "ip_adapter_faceid_count",
        "high_res_mode", "scale", "steps", "denoise", "cfg", "tiled_vae", "upscaler",
        "restore_assist", "source_count", "resize_method",
        "prompt_only", "merge_policy", "target", "style_count", "style_names",
        "resolved_token_count", "missing_token_count", "inline_choice_count", "file_choice_count", "variant_offset",
        "manual_positive", "manual_negative",
        "node_readiness", "node_status", "optional_capabilities", "assistant_summary",
        "detector_model", "detector_type", "patch_path", "patch_paths", "gated_reason", "mount_slot",
        "detailer_pass_count", "enabled_detailer_pass_count", "runtime_unit_count", "manual_unit_count",
        "sep_unit_count", "skipped_pass_count", "face_detailer_unit_count", "segs_detailer_unit_count",
    ):
        if item.get(optional_key) not in (None, ""):
            normalized[optional_key] = deepcopy(item.get(optional_key))
    return normalized



def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _clean_string(value)
        if text:
            return text
    return ""


def _collect_route_assets(params: dict[str, Any], model: dict[str, Any], family: str, loader: str) -> dict[str, Any]:
    """Extract provider/model assets that affected the generated output.

    The keys are intentionally route-oriented instead of UI-oriented so a saved
    result can explain exactly which backend asset family was used later.
    """
    assets: dict[str, Any] = {}

    def put(key: str, *values: Any) -> None:
        value = _first_nonempty(*values)
        if value:
            assets[key] = value

    if loader == "gguf":
        put("gguf_unet", params.get("gguf_unet"), params.get("gguf_model"), model.get("model"), params.get("model"))
        put("text_encoder_1", params.get("text_encoder_1"), params.get("gguf_text_encoder_1"), params.get("gguf_text_encoder_primary"), params.get("qwen_text_encoder"))
        put("text_encoder_2", params.get("text_encoder_2"), params.get("gguf_text_encoder_2"), params.get("gguf_text_encoder_secondary"))
        put("mmproj", params.get("gguf_mmproj"), params.get("qwen_mmproj"), params.get("mmproj"))
    else:
        put("model", model.get("model"), params.get("model"))
        put("diffusion_model", params.get("diffusion_model"), params.get("unet"), params.get("unet_name"))
        put("checkpoint", params.get("checkpoint"), params.get("ckpt_name"))
        put("text_encoder_1", params.get("text_encoder_1"), params.get("qwen3_text_encoder"), params.get("clip_name"))
        put("text_encoder_2", params.get("text_encoder_2"), params.get("t5xxl"), params.get("clip_name2"))

    put("vae", model.get("vae"), params.get("vae"), params.get("vae_or_ae"), params.get("ae"))
    return assets


def _collect_provider_nodes(params: dict[str, Any]) -> dict[str, Any]:
    profile_keys = (
        "flux_gguf_profile",
        "qwen_gguf_profile",
        "z_image_profile",
        "hidream_profile",
        "sd_checkpoint_family_profile",
    )
    for key in profile_keys:
        profile = params.get(key)
        if isinstance(profile, dict) and isinstance(profile.get("provider_nodes"), dict):
            return deepcopy(profile.get("provider_nodes") or {})

    nodes: dict[str, Any] = {}
    for source_key, node_key in (
        ("gguf_unet_loader", "gguf_unet_loader"),
        ("gguf_clip_dual_loader", "gguf_clip_dual_loader"),
        ("gguf_clip_single_loader", "gguf_clip_single_loader"),
        ("vae_loader", "vae_loader"),
        ("unet_loader", "unet_loader"),
        ("clip_loader", "clip_loader"),
    ):
        value = _clean_string(params.get(source_key))
        if value:
            nodes[node_key] = value
    return nodes


def _infer_parameter_profile(params: dict[str, Any], family: str, loader: str) -> str:
    explicit = _first_nonempty(params.get("parameter_profile"), params.get("profile"), params.get("profile_id"))
    if explicit:
        return explicit
    for key in ("flux_gguf_profile", "qwen_gguf_profile", "z_image_profile", "hidream_profile", "sd_checkpoint_family_profile"):
        profile = params.get(key)
        if isinstance(profile, dict) and _clean_string(profile.get("compiler")):
            if key == "sd_checkpoint_family_profile":
                return f"{family}_checkpoint"
            return key.removesuffix("_profile")
    return "_".join(part for part in (family, loader) if part)


def _extension_ids(extensions: dict[str, Any]) -> list[str]:
    normalized = normalize_extension_metadata(extensions)
    ids: list[str] = []
    for item in normalized.get("used", []):
        if isinstance(item, dict) and item.get("extension_id"):
            ids.append(str(item.get("extension_id")))
    return ids


def _cfg_fix_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    block = (normalized.get("payloads") or {}).get("cfg_fix_dynamic_thresholding") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "cfg_fix_dynamic_thresholding"), {})
    if not block.get("enabled"):
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" CFG Fix / Dynamic Thresholding was not applied ({reason})."
    mode = params.get("mode") or "simple"
    preset = params.get("preset") or "custom"
    mimic = params.get("mimic_scale")
    percentile = params.get("threshold_percentile")
    node = metadata.get("node") or (patch or {}).get("node") or ("DynamicThresholdingFull" if mode == "full" else "DynamicThresholdingSimple")
    applied = "applied" if isinstance(patch, dict) and patch.get("applied") else "validated"
    tuning = f" mimic {mimic} / threshold {percentile}" if mimic is not None and percentile is not None else ""
    return f" CFG Fix / Dynamic Thresholding {applied}: {mode} mode, preset {preset},{tuning}, node {node}."


def _lora_stack_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("lora_stack"):
        return f" {summaries.get('lora_stack')}"
    block = (normalized.get("payloads") or {}).get("lora_stack") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "lora_stack"), {})
    if not block.get("enabled") or not rows:
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" LoRA Stack was not applied ({reason})."
    names = ", ".join(str(row.get("name") or "") for row in rows[:3] if isinstance(row, dict))
    suffix = "…" if len(rows) > 3 else ""
    applied = "applied" if isinstance(patch, dict) and patch.get("applied") else "validated"
    return f" LoRA Stack {applied}: {len(rows)} LoRA row(s){' — ' + names + suffix if names else ''}."


def _embeddings_ti_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("embeddings_ti"):
        return f" {summaries.get('embeddings_ti')}"
    block = (normalized.get("payloads") or {}).get("embeddings_ti") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    items = params.get("items") if isinstance(params.get("items"), list) else []
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "embeddings_ti"), {})
    if not block.get("enabled") or not items:
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" Embeddings/TI was not applied ({reason})."
    names = ", ".join(str(item.get("token") or item.get("name") or "") for item in items[:4] if isinstance(item, dict))
    suffix = "…" if len(items) > 4 else ""
    applied = "applied" if isinstance(patch, dict) and patch.get("mutated") else "validated"
    return f" Embeddings/TI {applied}: {len(items)} chip(s){' — ' + names + suffix if names else ''}."


def _controlnet_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.controlnet"):
        return f" {summaries.get('image.controlnet')}"
    block = (normalized.get("payloads") or {}).get("image.controlnet") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
    active_units = [unit for unit in units if isinstance(unit, dict) and unit.get("enabled")]
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "image.controlnet"), {})
    if not block.get("enabled") or not active_units:
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" ControlNet was not applied ({reason})."
    applied = "applied" if isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")) else "validated"
    labels = ", ".join(str(unit.get("unit") or unit.get("preprocessor") or "unit") for unit in active_units[:4] if isinstance(unit, dict))
    suffix = "…" if len(active_units) > 4 else ""
    node = (patch or {}).get("node") or (patch or {}).get("node_class") or "ControlNetApply"
    return f" ControlNet {applied}: {len(active_units)} unit(s){' — ' + labels + suffix if labels else ''}, node {node}."


def _ip_adapter_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.ip_adapter"):
        return f" {summaries.get('image.ip_adapter')}"
    block = (normalized.get("payloads") or {}).get("image.ip_adapter") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
    active_units = [unit for unit in units if isinstance(unit, dict) and unit.get("enabled", True)]
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "image.ip_adapter"), {})
    if not block.get("enabled") or not active_units:
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" IP Adapter was not applied ({reason})."
    applied = "applied" if isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")) else "validated"
    faceid = sum(1 for unit in active_units if str(unit.get("mode") or "standard") == "faceid")
    standard = len(active_units) - faceid
    return f" IP Adapter {applied}: {len(active_units)} unit(s), {standard} standard / {faceid} FaceID."



def _scene_director_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.scene_director"):
        return f" {summaries.get('image.scene_director')}"
    block = (normalized.get("payloads") or {}).get("image.scene_director") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "image.scene_director"), {})
    if not block.get("enabled") or not regions:
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" Scene Director was not applied ({reason})."
    applied = "applied" if isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")) else "validated"
    return f" Scene Director {applied}: {len(regions)} region(s) with regional prompt planning."


def _adetailer_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.adetailer"):
        return f" {summaries.get('image.adetailer')}"
    block = (normalized.get("payloads") or {}).get("image.adetailer") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "image.adetailer"), {})
    if not block.get("enabled"):
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" ADetailer was not applied ({reason})."
    applied = "applied" if isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")) else "validated"
    detector = params.get("detector_model") or (patch or {}).get("detector_model") or "detector not selected"
    path = str((patch or {}).get("patch_path") or "detailer").replace("_", " ")
    steps = params.get("steps") or ""
    denoise = params.get("denoise") or ""
    cfg = params.get("cfg")
    cfg_text = f", CFG {cfg}" if cfg is not None else ""
    return f" ADetailer {applied}: {path}, detector {detector}, {steps} steps, denoise {denoise}{cfg_text}."

def _high_res_lab_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.high_res_lab"):
        return f" {summaries.get('image.high_res_lab')}"
    block = (normalized.get("payloads") or {}).get("image.high_res_lab") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = next((item for item in normalized.get("workflow_patches", []) if isinstance(item, dict) and item.get("extension_id") == "image.high_res_lab"), {})
    if not block.get("enabled"):
        reason = metadata.get("reason") or (patch or {}).get("reason") or "disabled or gated"
        return f" High-Res Lab was not applied ({reason})."
    applied = "applied" if isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")) else "validated"
    mode = params.get("mode") or (patch or {}).get("mode") or "latent"
    scale = params.get("scale") or ""
    steps = params.get("steps") or ""
    denoise = params.get("denoise") or ""
    return f" High-Res Lab {applied}: {mode} refine at {scale}x, {steps} steps, denoise {denoise}."


def _image_upscale_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.image_upscale"):
        return f" {summaries.get('image.image_upscale')}"
    memory_events = normalized.get("memory_events") if isinstance(normalized.get("memory_events"), dict) else {}
    event = memory_events.get("image.image_upscale") if isinstance(memory_events.get("image.image_upscale"), dict) else {}
    if event.get("assistant_summary"):
        return f" {event.get('assistant_summary')}"
    block = (normalized.get("payloads") or {}).get("image.image_upscale") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict):
        return ""
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    if not block.get("enabled"):
        reason = metadata.get("reason") or metadata.get("gated_reason") or "disabled or gated"
        return f" Image Upscale was not queued ({reason})."
    mode = "model upscale" if params.get("upscale_model") else "interpolation upscale"
    scale = params.get("scale") or params.get("image_upscale_scale") or ""
    restore = params.get("restore_assist") or params.get("image_upscale_restore_assist") or "off"
    summary = f" Image Upscale queued using {mode}"
    if scale:
        summary += f" at {scale}x"
    if restore == "codeformer":
        summary += " with CodeFormer restore"
    return summary + "."


def _background_removal_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("image.background_removal"):
        return f" {summaries.get('image.background_removal')}"
    memory_events = normalized.get("memory_events") if isinstance(normalized.get("memory_events"), dict) else {}
    event = memory_events.get("image.background_removal") if isinstance(memory_events.get("image.background_removal"), dict) else {}
    if event.get("assistant_summary"):
        summary = f" {event.get('assistant_summary')}"
    else:
        block = (normalized.get("payloads") or {}).get("image.background_removal") if isinstance(normalized.get("payloads"), dict) else None
        if not isinstance(block, dict):
            return ""
        params = block.get("params") if isinstance(block.get("params"), dict) else {}
        if not block.get("enabled"):
            return " Background Removal was disabled or gated."
        workflow_mode = str(params.get("workflow_mode") or "segment")
        mask = " with alpha-mask output" if params.get("save_mask", True) else ""
        if workflow_mode == "refine_mask":
            summary = " Background Removal reviewed-mask refinement queued without rerunning BiRefNet segmentation"
            if params.get("mask_expand"):
                summary += f" at edge offset {int(params.get('mask_expand') or 0):+d}px"
            if params.get("mask_feather"):
                summary += f" with {int(params.get('mask_feather') or 0)}px feather"
            summary += f"{mask}."
        elif workflow_mode == "interactive_sam":
            subjects = [item for item in (params.get("sam_subjects") or []) if isinstance(item, dict) and item.get("selected", True)]
            prompts = list(params.get("sam_prompts") or [])
            keep = sum(len(item.get("keep_points") or []) for item in subjects)
            remove = sum(len(item.get("remove_points") or []) for item in subjects)
            boxes = sum(1 for item in subjects if item.get("bbox"))
            if not subjects:
                keep = sum(1 for item in prompts if item.get("type") == "point" and int(item.get("label") or 0) == 1)
                remove = sum(1 for item in prompts if item.get("type") == "point" and int(item.get("label") or 0) == 0)
                boxes = sum(1 for item in prompts if item.get("type") == "rectangle")
            model = str(params.get("sam_comfy_model") or params.get("sam_model_variant") or "SAM")
            route = "shared ADetailer SAM" if params.get("resolved_engine") == "comfy_sam" else "Native ONNX SAM"
            summary = f" Interactive SAM kept {len(subjects) or 1} selected subject(s) through {route} / {model}, {keep} Keep point(s), {remove} Remove point(s), and {boxes} box(es)"
            if params.get("sam_refine_mode") == "birefnet_gate":
                edge_model = params.get("model") if params.get("resolved_engine") == "comfy_sam" else params.get("sam_refine_model")
                summary += f" with {edge_model or 'BiRefNet'} soft-edge refinement"
                if params.get("sam_refine_fallback_used"):
                    summary += " (SAM-only fallback used)"
            summary += f"{mask}."
        else:
            requested_engine = str(params.get("engine") or "smart").replace("_", " ")
            resolved_engine = str(params.get("resolved_engine") or "").strip()
            resolved_model = str(params.get("resolved_model") or params.get("native_model") or params.get("model") or "").strip()
            preset = str(params.get("preset") or "smart_auto").replace("_", " ")
            if resolved_engine == "native_rembg":
                engine_label = "Neo native rembg"
            elif resolved_engine == "comfy_birefnet":
                engine_label = "Comfy BiRefNet"
            else:
                engine_label = requested_engine.title()
            model_label = resolved_model or ("BiRefNet" if resolved_engine != "native_rembg" else "rembg model")
            summary = f" Background Removal queued with {engine_label} / {model_label} ({preset}){mask}."
            if params.get("fallback_used"):
                reason = str(params.get("fallback_reason") or "primary engine unavailable").strip()
                summary += f" Smart fallback was used: {reason}."
    verification = event.get("output_verification") if isinstance(event.get("output_verification"), dict) else {}
    if verification.get("status") == "passed":
        summary += " RGBA output verification passed."
    elif verification.get("status") == "warning":
        summary += " RGBA output verification completed with warnings."
    elif verification.get("status") == "failed":
        summary += " RGBA output verification failed; inspect the saved output."
    return summary


def _wildcards_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("wildcards"):
        return f" {summaries.get('wildcards')}"
    block = (normalized.get("payloads") or {}).get("wildcards") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict) or not block.get("enabled"):
        return ""
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    resolved = [str(item) for item in (metadata.get("resolved_tokens") or []) if str(item or "").strip()]
    missing = [str(item) for item in (metadata.get("missing_tokens") or []) if str(item or "").strip()]
    inline_count = int(metadata.get("inline_choice_count") or 0)
    file_count = int(metadata.get("file_choice_count") or 0)
    if resolved:
        names = ", ".join(resolved[:5]) + ("…" if len(resolved) > 5 else "")
        return f" Wildcards resolved {len(resolved)} token{'s' if len(resolved) != 1 else ''}: {names}."
    if inline_count or file_count:
        return f" Wildcards resolved {inline_count} inline choice{'s' if inline_count != 1 else ''} and {file_count} file choice{'s' if file_count != 1 else ''}."
    if missing:
        names = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        return f" Wildcards left missing token{'s' if len(missing) != 1 else ''} visible: {names}."
    return " Wildcards enabled without resolvable tokens."


def _style_stack_assistant_summary(extensions: dict[str, Any]) -> str:
    normalized = normalize_extension_metadata(extensions)
    summaries = normalized.get("assistant_summaries") if isinstance(normalized.get("assistant_summaries"), dict) else {}
    if summaries.get("style_stack"):
        return f" {summaries.get('style_stack')}"
    block = (normalized.get("payloads") or {}).get("style_stack") if isinstance(normalized.get("payloads"), dict) else None
    if not isinstance(block, dict) or not block.get("enabled"):
        return ""
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    names = [str(item) for item in (inputs.get("active_styles") or []) if str(item or "").strip()]
    target = str(params.get("target") or "both")
    if names:
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f", +{len(names) - 5} more"
        return f" Style Stack applied {len(names)} style{'s' if len(names) != 1 else ''}: {label}. Target: {target}."
    if str(inputs.get("manual_positive") or inputs.get("manual_negative") or "").strip():
        return f" Style Stack applied manual style text. Target: {target}."
    return " Style Stack enabled without active style text."

def build_route_metadata(
    *,
    mode: str | None,
    provider_id: str | None,
    backend: str | None = None,
    params: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Phase 12.21 exact route metadata block for an Image output."""
    params = params if isinstance(params, dict) else {}
    model = model if isinstance(model, dict) else {}
    family = _first_nonempty(model.get("family"), params.get("family"))
    loader = _first_nonempty(model.get("loader"), params.get("loader"))
    clean_mode = _clean_string(mode or params.get("mode") or "generate")
    workflow_type = _first_nonempty(params.get("workflow_type"), f"image.{clean_mode}.{family}" if family else "")
    backend_name = _first_nonempty(backend, params.get("backend"), "comfyui" if _clean_string(provider_id).startswith("comfy") else "")
    extension_payload = normalize_extension_metadata(extensions)
    used_extensions = _extension_ids(extension_payload)
    if loader == "gguf" and "image.gguf_loader" not in used_extensions:
        used_extensions.append("image.gguf_loader")

    return {
        "family": family,
        "loader": loader,
        "backend": backend_name,
        "mode": clean_mode,
        "workflow_type": workflow_type,
        "parameter_profile": _infer_parameter_profile(params, family, loader),
        "assets": _collect_route_assets(params, model, family, loader),
        "provider_nodes": _collect_provider_nodes(params),
        "extensions": {
            "used": used_extensions,
        },
    }


def _workflow_node_classes(workflow_prompt: Any) -> list[str]:
    if not isinstance(workflow_prompt, dict):
        return []
    classes: list[str] = []
    for node in workflow_prompt.values():
        if isinstance(node, dict) and node.get("class_type"):
            classes.append(str(node.get("class_type")))
    return sorted(set(classes))


def _clean_key_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if str(item or "").strip()})


def _snapshot_ui_payload(params: dict[str, Any]) -> dict[str, Any]:
    ui = params.get("_neo_ui_route_snapshot") or params.get("_neo_ui_snapshot")
    if not isinstance(ui, dict):
        return {"visible_fields": [], "hidden_fields": [], "badges": []}
    return {
        "visible_fields": _clean_key_list(ui.get("visible_fields")),
        "hidden_fields": _clean_key_list(ui.get("hidden_fields")),
        "badges": _clean_key_list(ui.get("badges")),
    }


def build_route_snapshot(
    *,
    route_metadata: dict[str, Any] | None = None,
    mode: str | None = None,
    provider_id: str | None = None,
    backend: str | None = None,
    params: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
    compile_route: dict[str, Any] | None = None,
    workflow_prompt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Phase 12.32 route snapshot debug block.

    Route metadata answers what route produced the output. The route snapshot adds
    debug proof: what the UI exposed, which payload keys were submitted, and what
    provider workflow node classes were compiled.
    """
    params = params if isinstance(params, dict) else {}
    model = model if isinstance(model, dict) else {}
    route = deepcopy(route_metadata) if isinstance(route_metadata, dict) else build_route_metadata(
        mode=mode,
        provider_id=provider_id,
        backend=backend,
        params=params,
        model=model,
        extensions=extensions if isinstance(extensions, dict) else {},
    )
    compile_route = compile_route if isinstance(compile_route, dict) else {}
    route_block = {
        "backend": _first_nonempty(route.get("backend"), backend),
        "family": _first_nonempty(route.get("family"), model.get("family"), params.get("family")),
        "loader": _first_nonempty(route.get("loader"), model.get("loader"), params.get("loader")),
        "mode": _first_nonempty(route.get("mode"), mode, params.get("mode")),
        "state": _first_nonempty(compile_route.get("status"), params.get("_neo_route_state"), "unknown"),
        "workflow_type": _first_nonempty(route.get("workflow_type"), params.get("workflow_type")),
        "compiler": _first_nonempty(compile_route.get("compiler_id"), params.get("compiler_id")),
        "parameter_profile": _first_nonempty(route.get("parameter_profile"), params.get("parameter_profile")),
        "provider_id": _clean_string(provider_id),
    }
    node_classes = _workflow_node_classes(workflow_prompt or params.get("_neo_workflow_prompt"))
    return {
        "schema_version": "neo.image.route_snapshot.v1",
        "route": route_block,
        "ui": _snapshot_ui_payload(params),
        "payload": {
            "submitted_keys": sorted(str(key) for key in params.keys()),
        },
        "workflow": {
            "node_classes": node_classes,
            "node_count": len(workflow_prompt or params.get("_neo_workflow_prompt") or {}) if isinstance(workflow_prompt or params.get("_neo_workflow_prompt"), dict) else 0,
        },
        "compile_route": deepcopy(compile_route),
    }

def build_output_file_record(
    *,
    file_id: str | None = None,
    filename: str | None = None,
    path: str | Path | None = None,
    url: str | None = None,
    mime_type: str = "image/png",
    width: int | None = None,
    height: int | None = None,
    role: str = "image",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_filename = sanitize_path_part(filename or (Path(str(path)).name if path else "output.png"), fallback="output.png")
    record: dict[str, Any] = {
        "file_id": sanitize_path_part(file_id or Path(safe_filename).stem, fallback="output"),
        "filename": safe_filename,
        "role": role,
        "mime_type": mime_type,
    }
    if path is not None:
        record["path"] = Path(str(path)).as_posix()
    if url:
        record["url"] = url
    if width is not None:
        record["width"] = int(width)
    if height is not None:
        record["height"] = int(height)
    if isinstance(metadata, dict) and metadata:
        record["metadata"] = deepcopy(metadata)
    return record



def build_provider_binding_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return provider/profile binding metadata used by Replay + audit panels.

    This is intentionally provider-neutral. Cloud providers such as xai_grok need
    profile/key/model revalidation later; local Comfy profiles need route/node
    revalidation. The block records enough without storing secrets.
    """
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    route = record.get("route") if isinstance(record.get("route"), dict) else {}
    route_snapshot = record.get("route_snapshot") if isinstance(record.get("route_snapshot"), dict) else {}
    profile_snapshot = params.get("_neo_backend_profile_snapshot") if isinstance(params.get("_neo_backend_profile_snapshot"), dict) else {}
    connection_type = _first_nonempty(profile_snapshot.get("connection_type"), params.get("connection_type"), route.get("backend"))
    provider_id = _first_nonempty(job.get("provider_id"), params.get("provider_id"), route_snapshot.get("route", {}).get("provider_id") if isinstance(route_snapshot.get("route"), dict) else "")
    backend_profile_id = _first_nonempty(job.get("backend_profile_id"), params.get("backend_profile_id"), profile_snapshot.get("profile_id"))
    default_model = _first_nonempty(profile_snapshot.get("default_model"), model.get("model"), params.get("model"))
    return {
        "schema_version": "neo.image.provider_binding.v1",
        "surface": "image",
        "provider_id": provider_id,
        "provider_label": _first_nonempty(profile_snapshot.get("provider_label"), provider_id),
        "backend_profile_id": backend_profile_id,
        "backend_profile_name": _first_nonempty(profile_snapshot.get("display_name"), backend_profile_id),
        "connection_type": connection_type,
        "model": default_model,
        "family": _first_nonempty(model.get("family"), route.get("family")),
        "loader": _first_nonempty(model.get("loader"), route.get("loader")),
        "mode": _first_nonempty(record.get("mode"), route.get("mode"), params.get("mode")),
        "endpoint": _first_nonempty(params.get("endpoint"), params.get("api_endpoint"), route_snapshot.get("compile_route", {}).get("endpoint") if isinstance(route_snapshot.get("compile_route"), dict) else ""),
        "requires_profile_revalidation": True,
        "requires_secret_revalidation": connection_type == "cloud_api",
        "profile_snapshot": {
            "profile_id": _first_nonempty(profile_snapshot.get("profile_id"), backend_profile_id),
            "display_name": _first_nonempty(profile_snapshot.get("display_name"), backend_profile_id),
            "provider_id": _first_nonempty(profile_snapshot.get("provider_id"), provider_id),
            "connection_type": connection_type,
            "default_model": default_model,
            "available_models": deepcopy(profile_snapshot.get("available_models") if isinstance(profile_snapshot.get("available_models"), list) else []),
            "capabilities": deepcopy(profile_snapshot.get("capabilities") if isinstance(profile_snapshot.get("capabilities"), dict) else {}),
        },
    }


def build_provider_replay_validation_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return metadata-only replay validation requirements.

    Current live profile checks are done by the API route in main.py. This static
    block stays inside the sidecar so old outputs can still explain what must be
    checked before replay.
    """
    binding = record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else build_provider_binding_metadata(record)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    input_assets = source.get("input_assets") if isinstance(source.get("input_assets"), list) else []
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    unsupported = params.get("unsupported_inline_controls") if isinstance(params.get("unsupported_inline_controls"), list) else []
    connection_type = str(binding.get("connection_type") or "")
    provider_id = str(binding.get("provider_id") or "")
    is_cloud = connection_type == "cloud_api"
    blocked = []
    allowed = []
    extension_gate = params.get("_neo_provider_parameter_gate") if isinstance(params.get("_neo_provider_parameter_gate"), dict) else {}
    if isinstance(extension_gate.get("allowed_prompt_extensions"), list):
        allowed = deepcopy(extension_gate.get("allowed_prompt_extensions"))
    if isinstance(extension_gate.get("blocked_inline_extensions"), list):
        blocked = deepcopy(extension_gate.get("blocked_inline_extensions"))
    if is_cloud and not blocked:
        blocked = ["adetailer", "highres_lab", "controlnet", "ip_adapter", "lora_stack", "regional_conditioning"]
    if is_cloud and not allowed:
        allowed = ["wildcards", "style_stack", "prompt_extensions"]
    checks = [
        {"check_id": "backend_profile_exists", "state": "required", "reason": "Replay must bind to an Admin backend profile before execution."},
        {"check_id": "backend_profile_enabled", "state": "required", "reason": "Disabled profiles cannot run replay jobs."},
        {"check_id": "provider_matches_record", "state": "required", "reason": "Provider drift changes payload semantics."},
        {"check_id": "model_available", "state": "required", "reason": "Replay should use the saved model or ask the user to choose a replacement."},
        {"check_id": "input_assets_available", "state": "required" if input_assets else "not_needed", "reason": "Image edit/multi-image replay needs saved source/reference assets."},
    ]
    if is_cloud:
        checks.append({"check_id": "api_key_configured", "state": "required", "reason": "Cloud API replay cannot run without a configured API key."})
    return {
        "schema_version": "neo.image.replay_validation.v1",
        "surface": "image",
        "provider_id": provider_id,
        "backend_profile_id": str(binding.get("backend_profile_id") or ""),
        "connection_type": connection_type,
        "mode": str(binding.get("mode") or record.get("mode") or "generate"),
        "model": str(binding.get("model") or ""),
        "checks": checks,
        "extension_policy": {
            "provider_neutral_allowed": allowed,
            "inline_blocked": blocked,
            "unsupported_inline_controls": deepcopy(unsupported),
            "post_output_bridge_required_for_blocked_extensions": bool(blocked),
        },
        "source_assets": {
            "count": len(input_assets),
            "asset_ids": [str(item.get("asset_id") or "") for item in input_assets if isinstance(item, dict)],
            "roles": [str(item.get("role") or "") for item in input_assets if isinstance(item, dict)],
        },
        "state": "requires_live_revalidation",
    }


def build_image_output_record(
    *,
    mode: str | None = "generate",
    subtab: str | None = None,
    job_id: str | None = None,
    provider_id: str | None = None,
    backend_profile_id: str | None = None,
    status: str = "completed",
    positive_prompt: str | None = None,
    negative_prompt: str | None = None,
    effective_positive_prompt: str | None = None,
    effective_negative_prompt: str | None = None,
    prompt_conditioning: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
    route_metadata: dict[str, Any] | None = None,
    route_snapshot: dict[str, Any] | None = None,
    output_files: list[dict[str, Any]] | None = None,
    active_file: str | None = None,
    backend_output_ref: str | None = None,
    comfy_view_url: str | None = None,
    created_at: str | None = None,
    result_id: str | None = None,
    run_timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical core Image output metadata sidecar.

    This is storage/schema only. Actual file persistence is wired in a later phase.
    """
    category = normalize_image_output_category(subtab or mode or "generate")
    clean_job_id = sanitize_path_part(job_id or "job", fallback="job")
    clean_result_id = sanitize_path_part(result_id or f"{category}_{clean_job_id}", fallback="image_output")
    files = [deepcopy(item) for item in (output_files or []) if isinstance(item, dict)]
    active = active_file or (files[0].get("file_id") if files else "")
    timing = normalize_run_timing(run_timing)
    route = deepcopy(route_metadata) if isinstance(route_metadata, dict) else build_route_metadata(
        mode=mode,
        provider_id=provider_id,
        params=params if isinstance(params, dict) else {},
        model=model if isinstance(model, dict) else {},
        extensions=extensions if isinstance(extensions, dict) else {},
    )

    return {
        "schema_version": IMAGE_OUTPUT_RECORD_SCHEMA_VERSION,
        "result_id": clean_result_id,
        "surface": "image",
        "mode": mode or "generate",
        "subtab": category,
        "job": {
            "job_id": job_id or "",
            "provider_id": provider_id or "",
            "backend_profile_id": backend_profile_id or "",
            "status": status,
        },
        "prompt": {
            "positive": positive_prompt or "",
            "negative": negative_prompt or "",
            "effective_positive": effective_positive_prompt if effective_positive_prompt is not None else (positive_prompt or ""),
            "effective_negative": effective_negative_prompt if effective_negative_prompt is not None else (negative_prompt or ""),
            "conditioning": deepcopy(prompt_conditioning or {}),
        },
        "params": deepcopy(params or {}),
        "model": {
            "family": (model or {}).get("family", "") if isinstance(model, dict) else "",
            "loader": (model or {}).get("loader", "") if isinstance(model, dict) else "",
            "model": (model or {}).get("model", "") if isinstance(model, dict) else "",
            "vae": (model or {}).get("vae", "") if isinstance(model, dict) else "",
        },
        "route": route,
        "route_snapshot": deepcopy(route_snapshot) if isinstance(route_snapshot, dict) else build_route_snapshot(
            route_metadata=route,
            mode=mode,
            provider_id=provider_id,
            params=params if isinstance(params, dict) else {},
            model=model if isinstance(model, dict) else {},
            extensions=extensions if isinstance(extensions, dict) else {},
        ),
        "extensions": extension_metadata_with_run_timing(extensions, timing) if timing else normalize_extension_metadata(extensions),
        "run_timing": timing,
        "outputs": {
            "files": files,
            "active_file": active or "",
        },
        "source": {
            "backend_output_ref": backend_output_ref or "",
            "comfy_view_url": comfy_view_url or "",
            "input_assets": [],
        },
        "provider_binding": {},
        "replay_validation": {},
        "workflow_memory": {
            "event_id": "",
            "namespace": "image",
            "assistant_summary": "",
        },
        "replay": {},
        "replay_payload": {},
        "assistant_summary": "",
        "created_at": created_at or utc_now_iso(),
    }


def build_output_replay_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Build the non-breaking Replay/Resume sidecar block for Results.

    This block describes what the saved output can truthfully restore today.
    IMG-R6 is still metadata-only: latent and phase checkpoints are explicitly
    represented as unavailable until a provider-owned latent store exists.
    """
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    active_file_id = str(outputs.get("active_file") or "")
    active_file = next((item for item in files if isinstance(item, dict) and item.get("file_id") == active_file_id), files[0] if files and isinstance(files[0], dict) else {})
    replay_context = params.get("_neo_replay_context") if isinstance(params.get("_neo_replay_context"), dict) else {}
    preview_action = params.get("_neo_preview_action") if isinstance(params.get("_neo_preview_action"), dict) else {}
    source_result_id = str(replay_context.get("source_result_id") or preview_action.get("result_id") or params.get("_neo_source_result_id") or "")
    source_file_id = str(
        replay_context.get("output_source", {}).get("file_id")
        if isinstance(replay_context.get("output_source"), dict)
        else ""
    ) or str(preview_action.get("file_id") or params.get("_neo_source_output_id") or "")
    replay_source = str(replay_context.get("replay_source") or replay_context.get("replay_kind") or "full_recipe")
    replay_kind = str(replay_context.get("replay_kind") or replay_source or "full_recipe")
    branch_restore = replay_context.get("branch_restore") if isinstance(replay_context.get("branch_restore"), dict) else {}
    branch_restore_point = str(branch_restore.get("restore_point") or "")
    source_is_output_image = bool(replay_context.get("source_is_output_image") or replay_source == "final_output_as_source" or params.get("source_image"))
    latent_capture = normalize_latent_capture_request(params)
    latent_artifacts = normalize_latent_capture_artifacts(params)
    latent_restore_points = []
    for artifact in latent_artifacts:
        restore_point = str(artifact.get("restore_point") or "")
        if restore_point and restore_point not in latent_restore_points:
            latent_restore_points.append(restore_point)
    available_restore_points = ["full_recipe", "base_generation_only"]
    if active_file or source_is_output_image:
        available_restore_points.append("final_output_as_source")
    for restore_point in latent_restore_points:
        if restore_point not in available_restore_points:
            available_restore_points.append(restore_point)
    all_latent_restore_points = ["base_generation_only", "before_high_res_fix", "after_high_res_fix", "before_adetailer", "final_latent"]
    gated_restore_points = [item for item in all_latent_restore_points if item not in latent_restore_points and item != "base_generation_only"]
    latent_resume_available = bool(latent_restore_points)
    phase_checkpoint_available = any(item in latent_restore_points for item in ("base_generation_only", "before_high_res_fix", "after_high_res_fix", "before_adetailer"))
    return {
        "schema_version": "neo.image.replay.v1",
        "surface": "image",
        "result_id": str(record.get("result_id") or ""),
        "replay_kind": replay_kind,
        "replay_source": replay_source,
        "trigger": str(replay_context.get("trigger") or ("derived_post_fix" if preview_action else "original_generation")),
        "source_result_id": source_result_id,
        "source_file_id": source_file_id,
        "source_restore_point": branch_restore_point,
        "branch_restore": {
            "schema_version": "neo.image.branch_restore.v1",
            "source_result_id": str(branch_restore.get("source_result_id") or source_result_id or ""),
            "restore_point": branch_restore_point,
            "restore_point_label": str(branch_restore.get("restore_point_label") or branch_restore_point),
            "artifact_id": str(branch_restore.get("artifact_id") or ""),
            "artifact_path": str(branch_restore.get("artifact_path") or ""),
            "artifact_format": str(branch_restore.get("artifact_format") or ""),
            "provider_id": str(branch_restore.get("provider_id") or ""),
            "backend": str(branch_restore.get("backend") or ""),
            "provider_owned": bool(branch_restore.get("provider_owned")),
            "source_filename": str(branch_restore.get("source_filename") or ""),
            "source_subfolder": str(branch_restore.get("source_subfolder") or ""),
            "source_type": str(branch_restore.get("source_type") or ""),
            "comfy_load_name": str(branch_restore.get("comfy_load_name") or branch_restore.get("load_latent_name") or ""),
            "no_png_reencode": bool(branch_restore.get("no_png_reencode", True)) if branch_restore else False,
            "resume_policy": str(branch_restore.get("resume_policy") or ""),
            "state": str(branch_restore.get("state") or ""),
        } if branch_restore else {},
        "generated_from_replay": bool(source_result_id or replay_context),
        "source_is_output_image": source_is_output_image,
        "available_restore_points": available_restore_points,
        "latent_capture": {
            "request": latent_capture,
            "artifacts": latent_artifacts,
            "provider_hook_state": "available" if latent_artifacts else ("requested_pending_provider_support" if latent_capture.get("requested") else "not_requested"),
        },
        "latent_restore_points": latent_restore_points,
        "postfix_restore_points": ["final_output_as_source"] if active_file or source_is_output_image else [],
        "gated_restore_points": gated_restore_points,
        "restore_point_support": {
            "full_recipe": {
                "state": "available",
                "kind": "metadata_replay",
                "label": "Full recipe",
            },
            "final_output_as_source": {
                "state": "available" if (active_file or source_is_output_image) else "unavailable",
                "kind": "output_source_staging",
                "label": "Final output as source",
            },
            "base_generation_only": {
                "state": "available",
                "kind": "metadata_branch",
                "reason": "Available from saved base recipe; enhancement and finish extensions are stripped for review before generation.",
            },
            "before_high_res_fix": {
                "state": "available" if "before_high_res_fix" in latent_restore_points else "gated",
                "kind": "phase_checkpoint" if "before_high_res_fix" in latent_restore_points else "latent_capture_required",
                "reason": "Provider-owned phase checkpoint available" if "before_high_res_fix" in latent_restore_points else "Needs provider-owned phase checkpoint before High-Res Fix",
            },
            "after_high_res_fix": {
                "state": "available" if "after_high_res_fix" in latent_restore_points else "gated",
                "kind": "phase_checkpoint" if "after_high_res_fix" in latent_restore_points else "latent_capture_required",
                "reason": "Provider-owned phase checkpoint available" if "after_high_res_fix" in latent_restore_points else "Needs provider-owned phase checkpoint after High-Res Fix",
            },
            "before_adetailer": {
                "state": "available" if "before_adetailer" in latent_restore_points else "gated",
                "kind": "phase_checkpoint" if "before_adetailer" in latent_restore_points else "latent_capture_required",
                "reason": "Provider-owned phase checkpoint available" if "before_adetailer" in latent_restore_points else "Needs provider-owned phase checkpoint before ADetailer",
            },
            "final_latent": {
                "state": "available" if "final_latent" in latent_restore_points else "gated",
                "kind": "final_latent" if "final_latent" in latent_restore_points else "latent_capture_required",
                "reason": "Provider-owned final latent available" if "final_latent" in latent_restore_points else "Needs saved final latent before VAE decode",
            },
        },
        "provider_binding": deepcopy(record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else build_provider_binding_metadata(record)),
        "provider_revalidation": deepcopy(record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) else build_provider_replay_validation_metadata(record)),
        "capabilities": {
            "metadata_replay": True,
            "output_source_staging": bool(active_file or source_is_output_image),
            "latent_capture_request": bool(latent_capture.get("requested")),
            "latent_resume": latent_resume_available,
            "metadata_base_branch_resume": True,
            "phase_checkpoint_resume": phase_checkpoint_available,
            "exact_regeneration_guaranteed": False,
            "branch_from_restore_point": bool(branch_restore),
        },
        "policy": "branch_restore_draft_only_until_provider_resume_hook_executes" if branch_restore else "latent_capture_request_metadata_only_until_provider_artifacts_exist",
        "legacy_policy": "metadata_replay_only_no_latent_or_phase_resume",
    }


def build_assistant_output_summary(record: dict[str, Any]) -> str:
    """Return a compact Assistant-readable workflow summary for this output."""
    route = record.get("route") if isinstance(record.get("route"), dict) else {}
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    assets = source.get("input_assets") if isinstance(source.get("input_assets"), list) else []
    used_extensions = []
    for item in extensions.get("used", []) if isinstance(extensions.get("used"), list) else []:
        if isinstance(item, dict) and item.get("extension_id"):
            used_extensions.append(str(item.get("extension_id")))
        elif isinstance(item, str):
            used_extensions.append(item)
    route_bits = [
        _first_nonempty(route.get("backend"), "backend unknown"),
        _first_nonempty(route.get("family"), "family unknown"),
        _first_nonempty(route.get("loader"), "loader unknown"),
        _first_nonempty(route.get("mode"), record.get("mode"), "mode unknown"),
    ]
    summary = f"Image workflow generated with {' / '.join(route_bits)}."
    if assets:
        labels = ", ".join(str(item.get("label") or item.get("role") or item.get("asset_id")) for item in assets if isinstance(item, dict))
        if labels:
            summary += f" Input assets: {labels}."
    if used_extensions:
        summary += f" Extensions used: {', '.join(sorted(set(used_extensions)))}."
    summary += _cfg_fix_assistant_summary(extensions)
    summary += _lora_stack_assistant_summary(extensions)
    summary += _embeddings_ti_assistant_summary(extensions)
    summary += _controlnet_assistant_summary(extensions)
    summary += _ip_adapter_assistant_summary(extensions)
    summary += _scene_director_assistant_summary(extensions)
    summary += _adetailer_assistant_summary(extensions)
    summary += _high_res_lab_assistant_summary(extensions)
    summary += _image_upscale_assistant_summary(extensions)
    summary += _background_removal_assistant_summary(extensions)
    summary += _wildcards_assistant_summary(extensions)
    summary += _style_stack_assistant_summary(extensions)
    positive = str(prompt.get("positive") or "").strip()
    if positive:
        summary += f" Prompt: {positive[:240]}{'…' if len(positive) > 240 else ''}"
    return summary


def build_output_replay_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build Assistant/reuse-safe workflow replay data without hidden UI/backend handoff fields."""
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    extensions = normalize_extension_metadata(record.get("extensions") if isinstance(record.get("extensions"), dict) else {})
    safe_params = {
        str(key): deepcopy(value)
        for key, value in params.items()
        if not str(key).startswith("_neo_")
        and not str(key).startswith("comfy_")
        and str(key) not in {"backend_output_root"}
    }
    replay_extensions = {
        "used": deepcopy(extensions.get("used", [])),
        "payloads": deepcopy(extensions.get("payloads", {})),
        "restore_policy": "defer_to_extension_runtime",
        "revalidation_required": True,
    }
    replay_payloads = extensions.get("replay_payloads") if isinstance(extensions.get("replay_payloads"), dict) else {}
    if replay_payloads:
        replay_extensions["replay_payloads"] = deepcopy(replay_payloads)
    if "cfg_fix_dynamic_thresholding" in replay_extensions.get("payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["cfg_fix_dynamic_thresholding"] = "revalidate_route_nodes_and_cfg_before_enable"
    if "lora_stack" in replay_extensions.get("payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["lora_stack"] = "revalidate_route_lora_catalog_and_lora_loader_before_enable"
    if "embeddings_ti" in replay_extensions.get("payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["embeddings_ti"] = "revalidate_route_embedding_library_and_prompt_targets_before_enable"
    if "image.controlnet" in replay_extensions.get("payloads", {}) or "image.controlnet" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.controlnet"] = "revalidate_route_nodes_controlnet_models_and_assets_before_enable"
    if "image.ip_adapter" in replay_extensions.get("payloads", {}) or "image.ip_adapter" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.ip_adapter"] = "revalidate_route_nodes_ip_adapter_models_clip_vision_faceid_and_assets_before_enable"
    if "image.scene_director" in replay_extensions.get("payloads", {}) or "image.scene_director" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.scene_director"] = "revalidate_route_nodes_scene_director_regions_and_checkpoint_family_before_enable"
    if "image.adetailer" in replay_extensions.get("payloads", {}) or "image.adetailer" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.adetailer"] = "revalidate_route_nodes_detector_model_and_impact_pack_before_enable"
    if "image.high_res_lab" in replay_extensions.get("payloads", {}) or "image.high_res_lab" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.high_res_lab"] = "revalidate_route_nodes_high_res_lab_before_enable"
    if "image.image_upscale" in replay_extensions.get("payloads", {}) or "image.image_upscale" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["image.image_upscale"] = "revalidate_route_nodes_image_upscale_source_assets_and_optional_models_before_enable"
    if "image.background_removal" in replay_extensions.get("payloads", {}) or "image.background_removal" in replay_extensions.get("replay_payloads", {}):
        background_payload = replay_extensions.get("payloads", {}).get("image.background_removal") if isinstance(replay_extensions.get("payloads"), dict) else {}
        background_params = background_payload.get("params") if isinstance(background_payload, dict) and isinstance(background_payload.get("params"), dict) else {}
        background_policy = (
            "revalidate_requested_engine_models_runtime_source_review_mask_and_sam_prompts_before_enable"
            if str(background_params.get("workflow_mode") or "") == "interactive_sam"
            else "revalidate_requested_engine_models_runtime_source_and_review_mask_before_enable"
        )
        replay_extensions.setdefault("extension_restore_policies", {})["image.background_removal"] = background_policy
    if "wildcards" in replay_extensions.get("payloads", {}) or "wildcards" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["wildcards"] = "restore_wildcard_library_payload_and_revalidate_tokens_before_enable"
    if "style_stack" in replay_extensions.get("payloads", {}) or "style_stack" in replay_extensions.get("replay_payloads", {}):
        replay_extensions.setdefault("extension_restore_policies", {})["style_stack"] = "restore_style_chips_and_revalidate_style_library_before_enable"
    return {
        "schema_version": "neo.image.workflow_replay.v1",
        "surface": "image",
        "mode": record.get("mode") or safe_params.get("mode") or "generate",
        "subtab": record.get("subtab") or "generate",
        "prompt": {
            "positive": prompt.get("positive") or "",
            "negative": prompt.get("negative") or "",
            "effective_positive": prompt.get("effective_positive") or prompt.get("positive") or "",
            "effective_negative": prompt.get("effective_negative") or prompt.get("negative") or "",
            "conditioning": deepcopy(prompt.get("conditioning") if isinstance(prompt.get("conditioning"), dict) else {}),
        },
        "params": safe_params,
        "model": deepcopy(model),
        "route": deepcopy(record.get("route") if isinstance(record.get("route"), dict) else {}),
        "provider_binding": deepcopy(record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else build_provider_binding_metadata(record)),
        "replay_validation": deepcopy(record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) else build_provider_replay_validation_metadata(record)),
        "job": deepcopy(record.get("job") if isinstance(record.get("job"), dict) else {}),
        "extensions": replay_extensions,
        "input_assets": deepcopy((record.get("source") or {}).get("input_assets", []) if isinstance(record.get("source"), dict) else []),
    }


def output_record_schema_payload() -> dict[str, Any]:
    """Expose the stable metadata contract for UI/debug/admin checks."""
    return {
        "schema_version": IMAGE_OUTPUT_RECORD_SCHEMA_VERSION,
        "surface": "image",
        "owned_by": "core",
        "file": "neo_app/image/output_records.py",
        "extension_slots": list(EXTENSION_METADATA_KEYS),
        "workflow_memory": ["event_id", "namespace", "assistant_summary"],
        "assistant_ready_slots": ["assistant_summary", "replay", "replay_payload", "provider_binding", "replay_validation", "source.input_assets", "extensions.replay_payloads", "extensions.assistant_summaries", "extensions.memory_events"],
        "required_top_level_keys": [
            "schema_version",
            "result_id",
            "surface",
            "mode",
            "subtab",
            "job",
            "prompt",
            "params",
            "model",
            "route",
            "route_snapshot",
            "extensions",
            "outputs",
            "source",
            "provider_binding",
            "replay_validation",
            "replay",
            "created_at",
        ],
        "rules": [
            "Core owns the neo.image.output.v1 metadata shell.",
            "Every generated output must include route metadata: family, loader, backend, mode, workflow_type, parameter_profile, assets, provider_nodes, and extension ids used.",
            "Every generated output must include a route_snapshot with route, ui.visible_fields, payload.submitted_keys, and workflow.node_classes so regressions can compare last-known-good vs current runs.",
            "Extensions may append only into extensions.used, extensions.payloads, extensions.workflow_patches, extensions.validation, extensions.replay_payloads, extensions.assistant_summaries, and extensions.memory_events.",
            "Extensions must provide stable extension_id values when they affect generation.",
            "Provider output refs remain source references until the persistence service copies files into Neo_Data.",
            "The top-level replay block is metadata-only until provider-owned latent restore points are implemented.",
            "Provider binding and replay validation blocks must never store API keys or secrets; they only record profile/model/capability requirements.",
        ],
    }
