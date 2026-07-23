from __future__ import annotations

from copy import deepcopy
from typing import Any

from .adapter import normalize_scene_director_state, scene_director_to_regional_payload
from .node_decision import detect_node_status, workflow_readiness
from .support_matrix import ACTIVE_STATES, EXTENSION_ID, get_scene_director_support, normalize_mode
from .dependency_routing import dependency_warnings, interop_metadata, regional_lora_bindings_from_lora_stack
from .prompt_authority import build_prompt_authority_contract, normalize_prompt_authority

VERSION = 1
SCHEMA_VERSION = "neo.extension.payload.v1"
SCENE_SCHEMA = "neo.image.scene_director.v2"


CHARACTER_LOCK_MODES = {"off", "soft", "balanced", "strong", "strict"}
CHARACTER_LOCK_EXECUTION_MODES = {
    "prompt_guard_only",
    "latent_attention",
    "latent_repair",
    "end_refinement",
    "latent_and_refinement",
    "off",
}


def _character_lock_mode(value: Any, default: str = "balanced") -> str:
    raw = str(value or default).strip().lower()
    aliases = {
        "none": "off",
        "false": "off",
        "disabled": "off",
        "hair_focus_soft": "balanced",
        "appearance_soft": "balanced",
        "hair_focus_strong": "strong",
        "appearance_strong": "strong",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in CHARACTER_LOCK_MODES else default


def _guard_mode(value: Any, default: str = "off") -> str:
    raw = str(value or default).strip().lower()
    aliases = {"none": "off", "false": "off", "disabled": "off", "normal": "balanced", "medium": "balanced"}
    mode = aliases.get(raw, raw)
    return mode if mode in CHARACTER_LOCK_MODES else default


def _character_lock_execution_mode(value: Any, default: str = "latent_attention") -> str:
    raw = str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "prompt": "prompt_guard_only",
        "prompt_only": "prompt_guard_only",
        "prompt_guard": "prompt_guard_only",
        "guard_only": "prompt_guard_only",
        "attention": "latent_attention",
        "attention_only": "latent_attention",
        "in_sampler": "latent_attention",
        "in_sampler_attention": "latent_attention",
        "legacy_attention": "latent_attention",
        "legacy_in_sampler_attention": "latent_attention",
        "hairlock_strong": "latent_attention",
        "latent": "latent_attention",
        "latent_pass": "latent_attention",
        "latent_correction": "latent_attention",
        "latent_correction_pass": "latent_attention",
        "character_latent": "latent_repair",
        "latent_trait_repair": "latent_repair",
        "latent_repair_pass": "latent_repair",
        "masked": "end_refinement",
        "mask": "end_refinement",
        "masked_pass": "end_refinement",
        "masked_correction": "end_refinement",
        "masked_correction_pass": "end_refinement",
        "refinement": "end_refinement",
        "end": "end_refinement",
        "end_pass": "end_refinement",
        "final": "end_refinement",
        "both": "latent_and_refinement",
        "latent_plus_refinement": "latent_and_refinement",
        "latent_and_end_refinement": "latent_and_refinement",
        "none": "off",
        "false": "off",
        "disabled": "off",
        "0": "off",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in CHARACTER_LOCK_EXECUTION_MODES else default


def _character_lock_params(raw: dict[str, Any]) -> dict[str, str]:
    source = raw.get("character_lock") if isinstance(raw.get("character_lock"), dict) else {}
    legacy = raw.get("appearance_lock") if isinstance(raw.get("appearance_lock"), dict) else {}
    mode = _character_lock_mode(source.get("character") or raw.get("character_lock_mode") or raw.get("lock_mode") or legacy.get("mode") or raw.get("appearance_lock_mode") or "balanced")
    return {
        "character": mode,
        "gender": _guard_mode(source.get("gender") or raw.get("gender_guard_mode") or raw.get("gender_guard") or ("strict" if mode == "strict" else "off")),
        "skin_tone": _guard_mode(source.get("skin_tone") or raw.get("skin_tone_guard_mode") or raw.get("skin_tone_guard") or ("strong" if mode in {"strong", "strict"} else "off")),
        "hair": _guard_mode(source.get("hair") or raw.get("hair_guard_mode") or raw.get("hair_guard")),
        "build": _guard_mode(source.get("build") or raw.get("build_guard_mode") or raw.get("build_guard")),
        "body_height": _guard_mode(source.get("body_height") or raw.get("body_height_guard_mode") or raw.get("body_height_guard") or raw.get("height_guard")),
        "outfit": _guard_mode(source.get("outfit") or raw.get("outfit_preservation_mode") or raw.get("outfit_preservation")),
        "negative": _guard_mode(source.get("negative") or raw.get("negative_identity_guard_mode") or raw.get("negative_identity_guard") or ("off" if mode == "off" else "balanced")),
    }


def _character_lock_to_legacy_appearance(character_lock: dict[str, Any], identity_strength: float, mask_feather: int) -> dict[str, Any]:
    mode = _character_lock_mode(character_lock.get("character"), "balanced")
    return {
        "enabled": mode != "off",
        "mode": "off" if mode == "off" else ("hair_focus_strong" if mode in {"strong", "strict"} else "hair_focus_soft"),
        "gain": identity_strength,
        "height": 0.42,
        "feather": mask_feather,
    }


def _appearance_lock_params(raw: dict[str, Any], character_lock: dict[str, Any], identity_strength: float, mask_feather: int) -> dict[str, Any]:
    explicit = raw.get("appearance_lock") if isinstance(raw.get("appearance_lock"), dict) else {}
    if explicit or raw.get("appearance_lock_mode"):
        mode = explicit.get("mode") if explicit.get("mode") is not None else raw.get("appearance_lock_mode")
        return {
            "enabled": bool(explicit.get("enabled", True)),
            "mode": _text(mode or "hair_focus_soft") or "hair_focus_soft",
            "gain": _clamp_float(explicit.get("gain") if explicit.get("gain") is not None else raw.get("appearance_lock_gain"), identity_strength, 0.0, 2.0),
            "height": _clamp_float(explicit.get("height") if explicit.get("height") is not None else raw.get("appearance_lock_height"), 0.42, 0.0, 1.0),
            "feather": _clamp_int(explicit.get("feather") if explicit.get("feather") is not None else raw.get("appearance_lock_feather"), mask_feather, 0, 256),
        }
    return _character_lock_to_legacy_appearance(character_lock, identity_strength, mask_feather)

DEFAULT_CONTRACTS = {
    "enabled": True,
    "use_node_auto_prompts": False,
    "count_contract": "exactly {count} visible subjects, one subject per character region, no extra subjects",
    "subject_contract": "one complete subject inside this region, not merged, not duplicated",
    "negative_contract": "extra people, missing subject, wrong number of subjects, merged bodies, fused faces",
    "style_merge": "use Neo main prompt as the scene style and composition intent",
}

REGION_TYPE_ALIASES = {
    "person": "character",
    "subject": "character",
    "character": "character",
    "main_subject": "character",
    "object": "object",
    "prop": "object",
    "held_prop": "object",
    "weapon": "object",
    "detail": "object",
    "detail_lane": "object",
    "hair_detail": "object",
    "item": "object",
    "background": "background",
    "transition_effect": "background",
    "seam": "background",
    "style": "style",
    "atmosphere": "style",
}

LEGACY_ENABLE_KEYS = (
    "scene_director",
    "scene_director_state",
    "scene_director_enabled",
    "scene_director_regions",
    "scene_director_regional_units",
    "regional_prompt_regions",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clamp_float(value: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _clamp_int(value: Any, default: int = 0, lo: int = 0, hi: int = 999) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _region_type(value: Any) -> str:
    # Phase 26.9.2: unknown region roles must not silently become character
    # subjects. Only explicit person/subject/character aliases produce subject
    # mask slots; object/detail/background roles stay out of subject masks.
    return REGION_TYPE_ALIASES.get(str(value or "object").strip().lower(), "object")


def _bbox_from_region(region: dict[str, Any]) -> dict[str, float]:
    source = region.get("bbox")
    if isinstance(source, dict):
        x, y, w, h = source.get("x"), source.get("y"), source.get("w"), source.get("h")
    elif isinstance(source, (list, tuple)):
        values = list(source) + [None, None, None, None]
        x, y, w, h = values[:4]
    else:
        x, y, w, h = region.get("x"), region.get("y"), region.get("w"), region.get("h")
    x = _clamp_float(x, 0.08, 0.0, 1.0)
    y = _clamp_float(y, 0.14, 0.0, 1.0)
    w = _clamp_float(w, 0.28, 0.01, 1.0)
    h = _clamp_float(h, 0.70, 0.01, 1.0)
    if x + w > 1.0:
        w = max(0.01, 1.0 - x)
    if y + h > 1.0:
        h = max(0.01, 1.0 - y)
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def _default_feather(region_type: str) -> int:
    if region_type == "character":
        return 8
    if region_type == "object":
        return 10
    return 18


def _identity_from_region(region: dict[str, Any]) -> dict[str, Any]:
    raw_identity = region.get("identity") if isinstance(region.get("identity"), dict) else {}
    image_names_source = (
        raw_identity.get("image_names")
        or raw_identity.get("reference_images")
        or region.get("image_names")
        or region.get("reference_images")
        or []
    )
    if isinstance(image_names_source, str):
        image_names = [item.strip() for item in image_names_source.replace("\n", ",").split(",") if item.strip()]
    elif isinstance(image_names_source, list):
        image_names = [str(item).strip() for item in image_names_source if str(item).strip()]
    else:
        image_names = []
    reference_image = _text(
        raw_identity.get("reference_image")
        or region.get("reference_image")
        or region.get("image_name")
        or (region.get("reference") if region.get("reference") != "off" else "")
    ) or None
    if reference_image and reference_image not in image_names:
        image_names.insert(0, reference_image)
    return {
        "profile_id": _text(raw_identity.get("profile_id") or region.get("profile_id") or region.get("identity_profile_id") or region.get("character_profile_id")) or None,
        "profile_name": _text(raw_identity.get("profile_name") or region.get("profile_name") or region.get("identity_profile_name") or region.get("character_profile_name")) or None,
        "reference_image": reference_image,
        "image_names": image_names,
        # V1 character profiles carried the FaceID/IPAdapter execution hints on the profile.
        # Keep them on the V2 identity intent so image.ip_adapter can execute the owner-owned route.
        "mode": _text(raw_identity.get("mode") or raw_identity.get("ipadapter_mode") or region.get("identity_profile_mode") or region.get("ipadapter_mode") or region.get("mode") or "faceid").lower(),
        "clip_vision": _text(raw_identity.get("clip_vision") or raw_identity.get("clip_vision_model") or region.get("identity_profile_clip_vision") or region.get("ipadapter_clip_vision") or region.get("clip_vision")),
        "model": _text(raw_identity.get("model") or raw_identity.get("ipadapter_model") or region.get("ipadapter_model") or region.get("ipadapter_name")),
        "faceid_preset": _text(raw_identity.get("faceid_preset") or region.get("faceid_preset")),
        "faceid_provider": _text(raw_identity.get("faceid_provider") or region.get("faceid_provider")),
        "faceid_lora_strength": raw_identity.get("faceid_lora_strength", region.get("faceid_lora_strength", region.get("identity_profile_lora_weight"))),
        "weight": raw_identity.get("weight", region.get("identity_profile_weight", region.get("ipadapter_weight"))),
        "weight_faceidv2": raw_identity.get("weight_faceidv2", region.get("weight_faceidv2", region.get("identity_profile_weight", region.get("ipadapter_weight")))),
        "start_at": raw_identity.get("start_at", region.get("identity_profile_start_at", region.get("ipadapter_start_at"))),
        "end_at": raw_identity.get("end_at", region.get("identity_profile_end_at", region.get("ipadapter_end_at"))),
        "trigger_words": _text(raw_identity.get("trigger_words") or region.get("identity_profile_trigger_words")),
        "optional_lora": _text(raw_identity.get("optional_lora") or region.get("identity_profile_optional_lora")),
        "lora_weight": raw_identity.get("lora_weight", region.get("identity_profile_lora_weight")),
        "weight_user_override": bool(raw_identity.get("weight_user_override") or raw_identity.get("ipadapter_weight_user_override") or region.get("weight_user_override") or region.get("ipadapter_weight_user_override")),
        "weight_faceidv2_user_override": bool(raw_identity.get("weight_faceidv2_user_override") or raw_identity.get("weight_user_override") or raw_identity.get("ipadapter_weight_user_override") or region.get("weight_faceidv2_user_override") or region.get("weight_user_override") or region.get("ipadapter_weight_user_override")),
        "start_at_user_override": bool(raw_identity.get("start_at_user_override") or raw_identity.get("ipadapter_start_at_user_override") or region.get("start_at_user_override") or region.get("ipadapter_start_at_user_override")),
        "end_at_user_override": bool(raw_identity.get("end_at_user_override") or raw_identity.get("ipadapter_end_at_user_override") or region.get("end_at_user_override") or region.get("ipadapter_end_at_user_override")),
        "scope_mode": raw_identity.get("scope_mode") or raw_identity.get("ipadapter_scope_mode") or region.get("scope_mode") or region.get("ipadapter_scope_mode") or "identity_only",
    }


def _has_identity_reference(region: dict[str, Any]) -> bool:
    identity = region.get("identity") if isinstance(region.get("identity"), dict) else _identity_from_region(region)
    return bool(
        identity.get("profile_id")
        or identity.get("profile_name")
        or identity.get("reference_image")
        or identity.get("image_names")
        or region.get("character_profile_enabled")
        or region.get("ipadapter")
    )


def normalize_region(region: dict[str, Any] | None, index: int = 0) -> dict[str, Any]:
    region = deepcopy(region or {})
    region_type = _region_type(region.get("type") or region.get("role") or region.get("region_role"))
    identity = region.get("identity") if isinstance(region.get("identity"), dict) else _identity_from_region(region)
    mask = region.get("mask") if isinstance(region.get("mask"), dict) else {}
    return {
        "id": _text(region.get("id") or region.get("uid") or f"scene_region_{index + 1}"),
        "enabled": region.get("enabled", True) is not False,
        "visible": region.get("visible", True) is not False,
        "locked": bool(region.get("locked", False)),
        "label": _text(region.get("label") or f"Region {index + 1}"),
        "type": region_type,
        "bbox": _bbox_from_region(region),
        "prompt": _text(region.get("prompt") or region.get("positive") or region.get("text")),
        "negative_prompt": _text(region.get("negative_prompt") or region.get("negative")),
        "strength": _clamp_float(region.get("strength") or region.get("positive_strength"), 1.0, 0.0, 2.0),
        "character_traits": deepcopy(region.get("character_traits")) if isinstance(region.get("character_traits"), dict) else {},
        "trait_lock": deepcopy(region.get("trait_lock")) if isinstance(region.get("trait_lock"), dict) else {},
        "character_lock_correction": deepcopy(region.get("character_lock_correction")) if isinstance(region.get("character_lock_correction"), dict) else {},
        "character_lock_correction_enabled": region.get("character_lock_correction_enabled", "auto"),
        "character_lock_gender_family": _text(region.get("character_lock_gender_family") or "auto"),
        "character_lock_positive_text": _text(region.get("character_lock_positive_text") or ""),
        "character_lock_negative_text": _text(region.get("character_lock_negative_text") or ""),
        "mask": {
            "source": _text(mask.get("source") or region.get("mask_source") or "region_box"),
            "feather": _clamp_int(mask.get("feather") if mask.get("feather") is not None else region.get("feather", region.get("mask_feather")), _default_feather(region_type), 0, 128),
            "refine_requested": bool(mask.get("refine_requested") or region.get("mask_refine_requested") or region.get("mask_refine_enabled")),
        },
        "identity": {
            "profile_id": identity.get("profile_id") or None,
            "profile_name": identity.get("profile_name") or None,
            "reference_image": identity.get("reference_image") or None,
            "image_names": identity.get("image_names") or [],
            "mode": identity.get("mode") or "faceid",
            "model": identity.get("model") or "",
            "clip_vision": identity.get("clip_vision") or "",
            "faceid_preset": identity.get("faceid_preset") or "",
            "faceid_provider": identity.get("faceid_provider") or "",
            "faceid_lora_strength": identity.get("faceid_lora_strength"),
            "weight": identity.get("weight"),
            "weight_faceidv2": identity.get("weight_faceidv2"),
            "start_at": identity.get("start_at"),
            "end_at": identity.get("end_at"),
            "trigger_words": identity.get("trigger_words") or "",
            "optional_lora": identity.get("optional_lora") or "",
            "lora_weight": identity.get("lora_weight"),
            "weight_user_override": bool(identity.get("weight_user_override") or identity.get("ipadapter_weight_user_override")),
            "weight_faceidv2_user_override": bool(identity.get("weight_faceidv2_user_override") or identity.get("weight_user_override") or identity.get("ipadapter_weight_user_override")),
            "start_at_user_override": bool(identity.get("start_at_user_override") or identity.get("ipadapter_start_at_user_override")),
            "end_at_user_override": bool(identity.get("end_at_user_override") or identity.get("ipadapter_end_at_user_override")),
            "scope_mode": identity.get("scope_mode") or identity.get("ipadapter_scope_mode") or "identity_only",
        },
        # Intent fields retained only long enough for binding normalization. They
        # are pruned from unsupported/disabled active payloads by normalize_scene_director_payload().
        "ipadapter": bool(region.get("ipadapter")),
        "ipadapter_slot": _clamp_int(region.get("ipadapter_slot"), (index % 8) + 1, 1, 8),
        "ipadapter_use_region_mask": region.get("ipadapter_use_region_mask", True) is not False,
        "ipadapter_weight_mode": _text(region.get("ipadapter_weight_mode") or "slot_default"),
        "ipadapter_weight": _clamp_float(region.get("ipadapter_weight"), 0.52, 0.0, 2.0),
        "ipadapter_start_at": _clamp_float(region.get("ipadapter_start_at"), 0.05, 0.0, 1.0),
        "ipadapter_end_at": _clamp_float(region.get("ipadapter_end_at"), 0.75, 0.0, 1.0),
        # Phase 26.7: preserve extension unit routing IDs through normalization.
        # Owner extensions use this to hard-gate unsupported regional routes
        # instead of accidentally consuming region-assigned units globally.
        "extension_routes": deepcopy(region.get("extension_routes")) if isinstance(region.get("extension_routes"), dict) else {},
        "lora": bool(region.get("lora")),
        "lora_slot": _clamp_int(region.get("lora_slot"), (index % 8) + 1, 1, 16),
        "lora_weight_mode": _text(region.get("lora_weight_mode") or "slot_default"),
        "lora_strength": _clamp_float(region.get("lora_strength") or region.get("strength_model"), 0.8, -2.0, 2.0),
    }


def _is_active_region(region: dict[str, Any]) -> bool:
    return bool(region.get("enabled") and region.get("visible") and (_text(region.get("prompt")) or _has_identity_reference(region)))


def active_regions(block: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    return [region for region in regions if _is_active_region(region)]


def extract_scene_director_block(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("extensions")
    if isinstance(nested, dict) and isinstance(nested.get(EXTENSION_ID), dict):
        return deepcopy(nested[EXTENSION_ID])
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return deepcopy(payloads[EXTENSION_ID])
    if isinstance(payload.get(EXTENSION_ID), dict):
        return deepcopy(payload[EXTENSION_ID])
    if any(key in payload for key in LEGACY_ENABLE_KEYS):
        return _legacy_block(payload)
    return {}


def _legacy_regions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("scene_director_regions", "scene_director_regional_units", "regional_prompt_regions"):
        if isinstance(payload.get(key), list):
            return deepcopy(payload[key])
    state = payload.get("scene_director_state")
    if isinstance(state, dict) and isinstance(state.get("regions"), list):
        return deepcopy(state["regions"])
    scene = payload.get("scene_director")
    if isinstance(scene, dict) and isinstance(scene.get("regions"), list):
        return deepcopy(scene["regions"])
    scene_json = payload.get("scene_director_v052_scene_json")
    if isinstance(scene_json, dict) and isinstance(scene_json.get("subjects"), list):
        return deepcopy(scene_json["subjects"])
    return []


def _legacy_block(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("scene_director_state") if isinstance(payload.get("scene_director_state"), dict) else {}
    scene = payload.get("scene_director") if isinstance(payload.get("scene_director"), dict) else state
    regions = _legacy_regions(payload)
    global_data = scene.get("global") if isinstance(scene.get("global"), dict) else {}
    return {
        "enabled": _truthy(payload.get("scene_director_enabled", scene.get("enabled", bool(regions)))),
        "version": VERSION,
        "inputs": {
            "regions": regions,
            "contracts": scene.get("contracts") if isinstance(scene.get("contracts"), dict) else {},
            "global": {
                "positive_prompt": global_data.get("positive_prompt") or global_data.get("prompt") or payload.get("scene_director_effective_global_prompt") or payload.get("positive") or "",
                "negative_prompt": global_data.get("negative_prompt") or payload.get("scene_director_effective_negative_prompt") or payload.get("negative") or "",
                "style_prompt": payload.get("style_positive") or payload.get("scene_director_style_prompt_source") or "",
                "prompt_authority": normalize_prompt_authority(
                    global_data.get("prompt_authority")
                    or scene.get("prompt_authority")
                    or payload.get("scene_director_prompt_authority")
                ),
            },
        },
        "params": {
            "backend_mode": payload.get("scene_director_backend_mode") or "v052_node",
            "base_weight": payload.get("scene_director_v052_base_weight", 0.35),
            "region_gain": payload.get("scene_director_v052_region_gain", 0.65),
            "prompt_authority": normalize_prompt_authority(
                payload.get("scene_director_prompt_authority")
                or global_data.get("prompt_authority")
                or scene.get("prompt_authority")
            ),
            "normalize_masks": payload.get("scene_director_v052_normalize_masks", True),
            "max_subject_slots": payload.get("scene_director_v052_max_subject_slots", 4),
            "region_context": {
                "enabled": payload.get("scene_director_region_context_enabled", True),
                "mode": payload.get("scene_director_region_context_mode", "global_and_style"),
                "weight": payload.get("scene_director_region_context_weight", 0.35),
                "position": "suffix",
            },
            "mask_refine": payload.get("scene_director_mask_refine") if isinstance(payload.get("scene_director_mask_refine"), dict) else {"enabled": _truthy(payload.get("scene_director_mask_refine_enabled"))},
            "character_lock": _character_lock_params({
                "character_lock_mode": payload.get("scene_director_character_lock_mode"),
                "gender_guard_mode": payload.get("scene_director_gender_guard_mode"),
                "skin_tone_guard_mode": payload.get("scene_director_skin_tone_guard_mode"),
                "hair_guard_mode": payload.get("scene_director_hair_guard_mode"),
                "build_guard_mode": payload.get("scene_director_build_guard_mode"),
                "body_height_guard_mode": payload.get("scene_director_body_height_guard_mode"),
                "outfit_preservation_mode": payload.get("scene_director_outfit_preservation_mode"),
                "negative_identity_guard_mode": payload.get("scene_director_negative_identity_guard_mode"),
                "appearance_lock_mode": payload.get("scene_director_appearance_lock_mode"),
            }),
            "character_lock_mode": _character_lock_mode(payload.get("scene_director_character_lock_mode") or payload.get("scene_director_appearance_lock_mode") or "balanced"),
            "identity_strength": _clamp_float(payload.get("scene_director_identity_strength", payload.get("scene_director_appearance_lock_gain", 0.55)), 0.55, 0.0, 1.0),
            "detail_strength": _clamp_float(payload.get("scene_director_detail_strength", 0.85), 0.85, 0.0, 2.0),
            "background_strength": _clamp_float(payload.get("scene_director_background_strength", 0.65), 0.65, 0.0, 2.0),
            "mask_feather": _clamp_int(payload.get("scene_director_mask_feather", payload.get("scene_director_appearance_lock_feather", 18)), 18, 0, 128),
            "appearance_lock": {
                "enabled": _truthy(payload.get("scene_director_appearance_lock_enabled")),
                "mode": payload.get("scene_director_appearance_lock_mode") or "hair_focus_soft",
                "gain": payload.get("scene_director_appearance_lock_gain", 0.55),
                "height": payload.get("scene_director_appearance_lock_height", 0.42),
                "feather": payload.get("scene_director_appearance_lock_feather", 24),
            },
            "global_context_routing": {
                "positive": payload.get("scene_director_global_context_route_positive", True),
                "negative": payload.get("scene_director_global_context_route_negative", True),
                "style": payload.get("scene_director_global_context_route_style", True),
                "source": "neo_core_prompts",
            },
        },
        "assets": {
            "identity_units": deepcopy(payload.get("scene_director_identity_units") or []),
            "ipadapter_bindings": deepcopy(payload.get("scene_director_ipadapter_bindings") or []),
            "lora_bindings": deepcopy(payload.get("scene_director_lora_bindings") or []),
        },
        "metadata": {"legacy_source": "v1_scene_director", "legacy_keys_consumed": [key for key in LEGACY_ENABLE_KEYS if key in payload]},
    }


def build_disabled_block(reason: str | None = None, *, route_state: str | None = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "version": VERSION,
        "inputs": {},
        "params": {},
        "assets": {},
        "metadata": {
            "schema": SCENE_SCHEMA,
            "payload_schema": SCHEMA_VERSION,
            "source": "scene_director_v2_ui",
            "workflow_patch_requested": False,
            "workflow_patch_allowed": False,
            "route_state": route_state or "disabled",
            "reason": reason or "disabled",
            "gated_reason": reason or "disabled",
        },
    }


def _contracts(raw: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_CONTRACTS)
    if isinstance(raw, dict):
        merged.update({key: raw[key] for key in DEFAULT_CONTRACTS if key in raw})
    merged["enabled"] = raw.get("enabled", merged["enabled"]) is not False if isinstance(raw, dict) else True
    merged["use_node_auto_prompts"] = bool(merged.get("use_node_auto_prompts"))
    return merged


def _params(raw: dict[str, Any]) -> dict[str, Any]:
    region_context = raw.get("region_context") if isinstance(raw.get("region_context"), dict) else {}
    mask_refine = raw.get("mask_refine") if isinstance(raw.get("mask_refine"), dict) else {}
    character_lock = _character_lock_params(raw)
    identity_strength = _clamp_float(raw.get("identity_strength") if raw.get("identity_strength") is not None else ((raw.get("appearance_lock") if isinstance(raw.get("appearance_lock"), dict) else {}).get("gain") if isinstance(raw.get("appearance_lock"), dict) else raw.get("appearance_lock_gain")), 0.55, 0.0, 1.0)
    mask_feather = _clamp_int(raw.get("mask_feather") if raw.get("mask_feather") is not None else ((raw.get("appearance_lock") if isinstance(raw.get("appearance_lock"), dict) else {}).get("feather") if isinstance(raw.get("appearance_lock"), dict) else raw.get("appearance_lock_feather")), 18, 0, 128)
    first_pass_lock = raw.get("first_pass_character_lock_authority") if isinstance(raw.get("first_pass_character_lock_authority"), dict) else {}
    execution_mode = _character_lock_execution_mode(
        raw.get("character_lock_execution_mode")
        or raw.get("scene_director_character_lock_execution_mode")
        or first_pass_lock.get("execution_mode")
        or first_pass_lock.get("execution")
        or "latent_attention"
    )
    return {
        "backend_mode": _text(raw.get("backend_mode") or "v052_node"),
        "authority_mode": _text(raw.get("authority_mode") or raw.get("scene_director_authority_mode") or "balanced"),
        "prompt_authority": normalize_prompt_authority(
            raw.get("prompt_authority") or raw.get("scene_director_prompt_authority")
        ),
        "base_weight": _clamp_float(raw.get("base_weight"), 0.35, 0.0, 2.0),
        "region_gain": _clamp_float(raw.get("region_gain"), 0.65, 0.0, 2.0),
        "normalize_masks": raw.get("normalize_masks", True) is not False,
        "max_subject_slots": _clamp_int(raw.get("max_subject_slots"), 4, 1, 16),
        "mask_source": _text(raw.get("mask_source") or "region_box"),
        "region_context": {
            "enabled": region_context.get("enabled", raw.get("region_context_enabled", True)) is not False,
            "mode": _text(region_context.get("mode") or raw.get("region_context_mode") or "global_and_style") or "global_and_style",
            "weight": _clamp_float(region_context.get("weight") if region_context.get("weight") is not None else raw.get("region_context_weight"), 0.35, 0.0, 2.0),
            "position": "suffix",
        },
        "mask_refine": {
            "enabled": bool(mask_refine.get("enabled") or raw.get("mask_refine_enabled")),
            "mode": _text(mask_refine.get("mode") or raw.get("mask_refine_mode") or "auto"),
        },
        "character_lock": character_lock,
        "character_lock_mode": character_lock["character"],
        "identity_strength": identity_strength,
        "detail_strength": _clamp_float(raw.get("detail_strength"), 0.85, 0.0, 2.0),
        "background_strength": _clamp_float(raw.get("background_strength"), 0.65, 0.0, 2.0),
        "mask_feather": mask_feather,
        "appearance_lock": _appearance_lock_params(raw, character_lock, identity_strength, mask_feather),
        "first_pass_character_lock_authority": {
            "schema": "neo.image.scene_director.first_pass_character_lock_authority.settings.v054.v1",
            "phase": "SD-V054-26.10.8J",
            "dedupe_phase": "V25.9.14",
            "ui_owner": "fix_pass_controls",
            "enabled": first_pass_lock.get("enabled", raw.get("character_lock_first_pass_enabled", True)),
            "apply_to": _text(first_pass_lock.get("apply_to") or raw.get("character_lock_first_pass_apply_to") or "strong_strict_only"),
            "timing": _text(first_pass_lock.get("timing") or raw.get("character_lock_first_pass_timing") or "before_adapters"),
            "denoise": _clamp_float(first_pass_lock.get("denoise") if first_pass_lock.get("denoise") is not None else raw.get("character_lock_first_pass_denoise"), 0.30, 0.0, 1.0),
            "steps": _clamp_int(first_pass_lock.get("steps") if first_pass_lock.get("steps") is not None else raw.get("character_lock_first_pass_steps"), 10, 1, 80),
            "cfg_mode": _text(first_pass_lock.get("cfg_mode") or raw.get("character_lock_first_pass_cfg_mode") or "inherit"),
            "cfg": _clamp_float(first_pass_lock.get("cfg") if first_pass_lock.get("cfg") not in {None, ""} else raw.get("character_lock_first_pass_cfg"), 0.0, 0.0, 30.0),
            "mask_source": _text(first_pass_lock.get("mask_source") or raw.get("character_lock_first_pass_mask_source") or "full_character_mask"),
            "mask_feather": _clamp_int(first_pass_lock.get("mask_feather") if first_pass_lock.get("mask_feather") is not None else raw.get("character_lock_first_pass_mask_feather"), 24, 0, 256),
            "protect_outfit": first_pass_lock.get("protect_outfit", raw.get("character_lock_first_pass_protect_outfit", True)) is not False,
            "protect_pose_contact": first_pass_lock.get("protect_pose_contact", raw.get("character_lock_first_pass_protect_pose_contact", True)) is not False,
            "execution_mode": execution_mode,
            "execution": execution_mode,
            "source": "fix_pass_controls_visible_fields",
        },
        "character_lock_execution_mode": execution_mode,
        "character_lock_pass_plan": execution_mode,
        "global_context_routing": {
            "positive": ((raw.get("global_context_routing") if isinstance(raw.get("global_context_routing"), dict) else {}).get("positive", raw.get("global_context_route_positive", True))) is not False,
            "negative": ((raw.get("global_context_routing") if isinstance(raw.get("global_context_routing"), dict) else {}).get("negative", raw.get("global_context_route_negative", True))) is not False,
            "style": ((raw.get("global_context_routing") if isinstance(raw.get("global_context_routing"), dict) else {}).get("style", raw.get("global_context_route_style", True))) is not False,
            "source": "neo_core_prompts",
        },
        "scene_director_lora_crop_denoise": raw.get("scene_director_lora_crop_denoise"),
        "scene_director_lora_crop_steps": raw.get("scene_director_lora_crop_steps"),
        "scene_director_lora_crop_padding": raw.get("scene_director_lora_crop_padding"),
        "scene_director_lora_crop_feather": raw.get("scene_director_lora_crop_feather"),
        "scene_director_lora_crop_scope": raw.get("scene_director_lora_crop_scope"),
    }


def _global_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    global_data = inputs.get("global") if isinstance(inputs.get("global"), dict) else {}
    scene = inputs.get("scene") if isinstance(inputs.get("scene"), dict) else {}
    scene_global = scene.get("global") if isinstance(scene.get("global"), dict) else {}
    return {
        "positive_prompt": _text(global_data.get("positive_prompt") or global_data.get("prompt") or scene_global.get("positive_prompt") or scene_global.get("prompt")),
        "negative_prompt": _text(global_data.get("negative_prompt") or scene_global.get("negative_prompt")),
        "style_prompt": _text(global_data.get("style_prompt") or scene_global.get("style_prompt")),
        "prompt_authority": normalize_prompt_authority(
            global_data.get("prompt_authority") or scene_global.get("prompt_authority")
        ),
    }


def _scene_lora_binding_keys(binding: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    if not isinstance(binding, dict):
        return keys
    region_id = str(binding.get("region_id") or binding.get("apply_to") or "").strip()
    for field in ("lora_row_id", "row_id", "assigned_row_id"):
        value = str(binding.get(field) or "").strip()
        if value:
            keys.add(f"row:{region_id}:{value}")
            keys.add(f"row:*:{value}")
    slot = str(binding.get("slot") or "").strip()
    if slot:
        keys.add(f"slot:{region_id}:{slot}")
    name = str(binding.get("name") or binding.get("lora_name") or "").strip().casefold()
    if name:
        keys.add(f"name:{region_id}:{name}")
    return keys


def _merge_scene_lora_route_settings(owner_bindings: list[dict[str, Any]], scene_bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Scene Director per-region LoRA route settings onto LoRA Stack owned rows.

    LoRA Stack owns row selection and file identity; Scene Director owns route settings
    such as regional visibility preset, route mode, finish/crop denoise, and compatibility
    diagnostics. This keeps Phase 26.10.8B/E controls alive even when LoRA Stack payload
    rows are the authoritative source of selected LoRAs.
    """
    if not owner_bindings or not scene_bindings:
        return owner_bindings
    scene_lookup: dict[str, dict[str, Any]] = {}
    for item in scene_bindings:
        if not isinstance(item, dict):
            continue
        for key in _scene_lora_binding_keys(item):
            scene_lookup.setdefault(key, item)
    route_fields = {
        "route_mode", "target", "strength", "strength_user_override",
        "finish_denoise", "finish_denoise_user_override", "finish_steps", "finish_steps_user_override",
        "crop_denoise", "crop_denoise_user_override", "crop_steps", "crop_steps_user_override",
        "crop_padding", "crop_feather", "crop_scope",
        "visibility_preset", "lora_visibility_preset", "visibility_preset_plan",
        "postpass_lock_policy", "character_lock_postpass_policy", "allow_character_repaint", "allow_character_lock_bypass",
        "trigger_words", "trigger_override",
        "lora_compatibility", "lora_family", "checkpoint_family", "checkpoint_name",
    }
    merged: list[dict[str, Any]] = []
    for owner in owner_bindings:
        if not isinstance(owner, dict):
            continue
        match = None
        for key in _scene_lora_binding_keys(owner):
            if key in scene_lookup:
                match = scene_lookup[key]
                break
        if not match:
            merged.append(owner)
            continue
        extra = {field: deepcopy(match[field]) for field in route_fields if field in match and match[field] not in (None, "")}
        if match.get("row_id") and not owner.get("row_id"):
            extra["row_id"] = match.get("row_id")
        if match.get("lora_row_id") and not owner.get("lora_row_id"):
            extra["lora_row_id"] = match.get("lora_row_id")
        merged.append({**owner, **extra, "scene_director_route_settings_merged": True})
    return merged


def _normalize_bindings(regions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    ip_bindings: list[dict[str, Any]] = []
    lora_bindings: list[dict[str, Any]] = []
    identity_units: list[dict[str, Any]] = []
    warnings: list[str] = []
    used_ip_slots: set[int] = set()
    subject_slot = 0
    for index, region in enumerate(regions, start=1):
        is_character_region = str(region.get("type") or region.get("role") or "").strip().lower() == "character"
        if is_character_region:
            subject_slot += 1
        identity = region.get("identity") if isinstance(region.get("identity"), dict) else {}
        if is_character_region and (identity.get("profile_id") or identity.get("profile_name") or identity.get("reference_image") or identity.get("image_names")):
            image_names = list(identity.get("image_names") or [])
            if identity.get("reference_image") and identity.get("reference_image") not in image_names:
                image_names.insert(0, identity.get("reference_image"))
            mode = str(identity.get("mode") or "faceid").strip().lower() or "faceid"
            if mode == "ipadapter":
                mode = "standard"
            if mode == "trigger_only":
                mode = "trigger_only"
            identity_units.append({
                "uid": identity.get("profile_id") or region["id"] or f"scene_identity_region_{index}",
                "region_id": region["id"],
                "region_index": index,
                "profile_id": identity.get("profile_id"),
                "profile_name": identity.get("profile_name"),
                "mode": mode,
                "model": identity.get("model") or "",
                "clip_vision": identity.get("clip_vision") or "",
                "faceid_preset": identity.get("faceid_preset") or "",
                "faceid_provider": identity.get("faceid_provider") or "",
                "faceid_lora_strength": identity.get("faceid_lora_strength"),
                "weight_faceidv2": identity.get("weight_faceidv2"),
                "weight": identity.get("weight"),
                "start_at": identity.get("start_at"),
                "end_at": identity.get("end_at"),
                "reference_image": identity.get("reference_image"),
                "image_name": image_names[0] if image_names else "",
                "image_names": image_names,
                "trigger_words": identity.get("trigger_words") or "",
                "optional_lora": identity.get("optional_lora") or "",
                "lora_weight": identity.get("lora_weight"),
                "weight_user_override": bool(identity.get("weight_user_override") or identity.get("ipadapter_weight_user_override")),
                "weight_faceidv2_user_override": bool(identity.get("weight_faceidv2_user_override") or identity.get("weight_user_override") or identity.get("ipadapter_weight_user_override")),
                "start_at_user_override": bool(identity.get("start_at_user_override") or identity.get("ipadapter_start_at_user_override")),
                "end_at_user_override": bool(identity.get("end_at_user_override") or identity.get("ipadapter_end_at_user_override")),
                "scope_mode": identity.get("scope_mode") or identity.get("ipadapter_scope_mode") or "identity_only",
                "subject_slot": max(1, min(4, subject_slot or index)),
                "attn_mask_output_index": 5 + max(1, min(4, subject_slot or index)),
                "source": "scene_director_character_profile_region",
                "missing_reference_image": not bool(image_names),
            })
        if region.get("ipadapter"):
            slot = int(region.get("ipadapter_slot") or index)
            original_slot = slot
            while slot in used_ip_slots and slot < 8:
                slot += 1
            if slot in used_ip_slots:
                warnings.append(f"Duplicate IPAdapter slot {original_slot} could not be reassigned for {region['id']}.")
                continue
            if slot != original_slot:
                warnings.append(f"auto_reassign_duplicate_ipadapter_slot:{original_slot}->{slot}:{region['id']}")
            used_ip_slots.add(slot)
            ip_bindings.append({
                "region_id": region["id"],
                "region_index": index,
                "slot": slot,
                "requested_slot": original_slot,
                "use_region_mask": region.get("ipadapter_use_region_mask", True) is not False,
                "weight_mode": region.get("ipadapter_weight_mode") or "slot_default",
                "weight": region.get("ipadapter_weight", 0.52),
                "start_at": region.get("ipadapter_start_at", 0.05),
                "end_at": region.get("ipadapter_end_at", 0.75),
                "source": "scene_director_slot_binding",
            })
        if region.get("lora"):
            lora_bindings.append({
                "region_id": region["id"],
                "region_index": index,
                "slot": int(region.get("lora_slot") or index),
                "weight_mode": region.get("lora_weight_mode") or "slot_default",
                "strength": region.get("lora_strength", 0.8),
                "source": "scene_director_region_binding",
            })
    return identity_units, ip_bindings, lora_bindings, warnings


def normalize_scene_director_payload(payload: Any, route: dict[str, Any] | None = None, node_status: Any = None, object_info: Any = None) -> dict[str, Any]:
    route = route or {}
    support = get_scene_director_support(route, node_status=node_status, object_info=object_info, require_node=True)
    route_state_value = str(support.get("state") or "unsupported")
    route_compatible_state = str(support.get("route_state") or route_state_value)
    raw = extract_scene_director_block(payload)
    if not raw or raw.get("enabled") is False:
        return {"extensions": {EXTENSION_ID: build_disabled_block("disabled", route_state=route_state_value)}}

    raw_inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    raw_assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    raw_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    scene = raw_inputs.get("scene") if isinstance(raw_inputs.get("scene"), dict) else {}
    raw_regions = raw_inputs.get("regions") if isinstance(raw_inputs.get("regions"), list) else scene.get("regions") if isinstance(scene.get("regions"), list) else []
    normalized_regions = [normalize_region(region, index) for index, region in enumerate(raw_regions)]
    active = [region for region in normalized_regions if _is_active_region(region)]
    subject_count = sum(1 for region in active if region.get("type") == "character")
    detail_count = sum(1 for region in active if region.get("type") != "character")
    params = _params(raw_params)
    contracts = _contracts(raw_inputs.get("contracts") if isinstance(raw_inputs.get("contracts"), dict) else scene.get("contracts") if isinstance(scene.get("contracts"), dict) else {})
    global_inputs = _global_inputs(raw_inputs)
    params["prompt_authority"] = normalize_prompt_authority(
        params.get("prompt_authority") or global_inputs.get("prompt_authority")
    )
    prompt_authority_contract = build_prompt_authority_contract(
        params,
        global_positive=global_inputs.get("positive_prompt"),
        global_negative=global_inputs.get("negative_prompt"),
        style_positive=global_inputs.get("style_prompt"),
        region_context_weight=(params.get("region_context") or {}).get("weight"),
    )
    node_decision = workflow_readiness(route=route, available_nodes=object_info if object_info is not None else node_status, enabled=bool(active and route_compatible_state in ACTIVE_STATES))
    workflow_patch_requested = bool(raw.get("enabled") is not False and bool(active) and route_compatible_state in ACTIVE_STATES)
    workflow_patch_allowed = bool(workflow_patch_requested and support.get("workflow_patch_allowed") and node_decision.get("workflow_patch_allowed", support.get("workflow_patch_allowed")))

    if route_state_value not in ACTIVE_STATES and route_compatible_state not in ACTIVE_STATES:
        return {"extensions": {EXTENSION_ID: build_disabled_block(str(support.get("reason") or route_state_value), route_state=route_state_value)}}
    if not active:
        block = build_disabled_block("no_active_regions", route_state=route_state_value)
        block["enabled"] = bool(raw.get("enabled", True))
        block["metadata"].update({"regional_count": 0, "subject_count": 0, "detail_region_count": 0})
        return {"extensions": {EXTENSION_ID: block}}

    identity_units, ipadapter_bindings, legacy_lora_bindings, binding_warnings = _normalize_bindings(active)
    stack_lora_bindings, stack_lora_warnings = regional_lora_bindings_from_lora_stack(payload if isinstance(payload, dict) else {}, active)
    scene_asset_lora_bindings = raw_assets.get("lora_bindings") if isinstance(raw_assets.get("lora_bindings"), list) else []
    if stack_lora_bindings and scene_asset_lora_bindings:
        stack_lora_bindings = _merge_scene_lora_route_settings(stack_lora_bindings, scene_asset_lora_bindings)
    lora_bindings = stack_lora_bindings or legacy_lora_bindings
    warnings = list(binding_warnings) + list(stack_lora_warnings)
    warnings.extend(dependency_warnings(
        payload if isinstance(payload, dict) else {},
        identity_units=identity_units,
        ipadapter_bindings=ipadapter_bindings,
        lora_bindings=lora_bindings,
    ))
    if stack_lora_bindings and legacy_lora_bindings:
        warnings.append("scene_director_region_lora_intent_deferred_to_image.lora_stack_apply_to_targets")
    if route_state_value == "experimental_available":
        warnings.append("Scene Director SD/SD1.5 route is experimental in V2.")
    interop = interop_metadata(payload if isinstance(payload, dict) else {})
    metadata = {
        "schema": SCENE_SCHEMA,
        "payload_schema": SCHEMA_VERSION,
        "source": raw_metadata.get("source") or "scene_director_v2_ui",
        "legacy_source": raw_metadata.get("legacy_source") or ("v1_scene_director" if raw_metadata.get("legacy_keys_consumed") else None),
        "route_state": route_state_value,
        "route": support.get("route") or {},
        "workflow_patch_requested": workflow_patch_requested,
        "workflow_patch_allowed": workflow_patch_allowed,
        "gated_reason": "" if workflow_patch_allowed else str(node_decision.get("reason") or support.get("reason") or "workflow_patch_not_allowed"),
        "reason": "" if workflow_patch_requested else "workflow_patch_not_requested",
        "node_status": detect_node_status(object_info if object_info is not None else node_status),
        "node_decision": node_decision,
        "regional_count": len(active),
        "subject_count": subject_count,
        "detail_region_count": detail_count,
        "suppress_global_ipadapter": bool(ipadapter_bindings or [unit for unit in identity_units if unit.get("mode") != "trigger_only" and not unit.get("missing_reference_image")]),
        "character_lock_mode": params.get("character_lock_mode"),
        "character_lock": params.get("character_lock", {}),
        "appearance_lock_enabled": bool(params.get("appearance_lock", {}).get("enabled")),
        "global_context_source": "neo_core_prompts",
        "prompt_authority": params.get("prompt_authority"),
        "prompt_authority_contract": prompt_authority_contract,
        "warnings": warnings,
        "interop": interop,
        "dependencies": interop.get("dependencies", {}),
    }
    block = {
        "enabled": True,
        "version": VERSION,
        "inputs": {
            "regions": active,
            "contracts": contracts,
            "global": global_inputs,
        },
        "params": params,
        "assets": {
            "identity_units": identity_units or raw_assets.get("identity_units", []),
            "ipadapter_bindings": ipadapter_bindings,
            "lora_bindings": lora_bindings,
        },
        "metadata": metadata,
    }
    return {"extensions": {EXTENSION_ID: block}}


def normalize_block(payload: Any, *, route: dict[str, Any] | None = None, object_info: Any = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = normalize_scene_director_payload(payload, route=route, object_info=object_info)
    block = normalized["extensions"][EXTENSION_ID]
    notes: list[dict[str, Any]] = []
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    if metadata.get("gated_reason") and not metadata.get("workflow_patch_allowed"):
        notes.append({"extension_id": EXTENSION_ID, "level": "warning", "field": "workflow_patch_allowed", "message": str(metadata.get("gated_reason"))})
    if metadata.get("reason") == "no_active_regions":
        notes.append({"extension_id": EXTENSION_ID, "level": "warning", "field": "inputs.regions", "message": "Scene Director enabled but no active regions are available."})
    for message in metadata.get("warnings") or []:
        notes.append({"extension_id": EXTENSION_ID, "level": "info", "field": "metadata.warnings", "message": str(message)})
    return block, notes


def _v2_region_to_v1_region(region: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """Convert the V2 normalized region shape back to the V1 adapter shape.

    V1 Scene Director expects region rectangles under `rect`, not only `bbox`.
    Without this bridge the V1 adapter falls back to its default full-height
    region box, which makes V2 canvas placement look ignored even though the
    NeoSceneDirectorV052/V053 node is present in the graph.
    """
    source = deepcopy(region or {})
    bbox = source.get("bbox") if isinstance(source.get("bbox"), dict) else {}
    if not bbox and isinstance(source.get("bbox"), (list, tuple)):
        vals = list(source.get("bbox")) + [None, None, None, None]
        bbox = {"x": vals[0], "y": vals[1], "w": vals[2], "h": vals[3]}
    rect = {
        "x": _clamp_float(bbox.get("x", source.get("x")), 0.08, 0.0, 1.0),
        "y": _clamp_float(bbox.get("y", source.get("y")), 0.14, 0.0, 1.0),
        "w": _clamp_float(bbox.get("w", source.get("w")), 0.28, 0.01, 1.0),
        "h": _clamp_float(bbox.get("h", source.get("h")), 0.70, 0.01, 1.0),
    }
    if rect["x"] + rect["w"] > 1.0:
        rect["w"] = max(0.01, 1.0 - rect["x"])
    if rect["y"] + rect["h"] > 1.0:
        rect["h"] = max(0.01, 1.0 - rect["y"])
    identity = source.get("identity") if isinstance(source.get("identity"), dict) else {}
    mask = source.get("mask") if isinstance(source.get("mask"), dict) else {}
    converted = {
        **source,
        "id": source.get("id") or f"scene_region_{index + 1}",
        "rect": rect,
        "x": rect["x"],
        "y": rect["y"],
        "w": rect["w"],
        "h": rect["h"],
        "type": source.get("type") or "character",
        "negative_prompt": source.get("negative_prompt") or source.get("negative") or "",
        "character_profile_id": identity.get("profile_id") or source.get("character_profile_id") or source.get("identity_profile_id") or "",
        "character_profile_name": identity.get("profile_name") or source.get("character_profile_name") or source.get("identity_profile_name") or "",
        "reference_image": identity.get("reference_image") or source.get("reference_image") or "",
        "image_names": identity.get("image_names") if isinstance(identity.get("image_names"), list) else source.get("image_names", []),
        "feather": mask.get("feather") if mask.get("feather") is not None else source.get("feather"),
        "mask_feather": mask.get("feather") if mask.get("feather") is not None else source.get("mask_feather"),
        "mask_refine_requested": bool(mask.get("refine_requested") or source.get("mask_refine_requested")),
        "mask_source": mask.get("source") or source.get("mask_source") or "region_box",
    }
    return converted


def legacy_payload_from_block(block: dict[str, Any], *, base_payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    base = deepcopy(base_payload or {})
    if not block.get("enabled"):
        return base, []
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    global_data = inputs.get("global") if isinstance(inputs.get("global"), dict) else {}
    raw_regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    v1_regions = [_v2_region_to_v1_region(region, index) for index, region in enumerate(raw_regions)]
    width = _clamp_int(base.get("width"), 1024, 64, 16384)
    height = _clamp_int(base.get("height"), 1024, 64, 16384)
    prompt_extension_merge = base.get("prompt_extension_merge") if isinstance(base.get("prompt_extension_merge"), dict) else {}
    scene_director_interop = prompt_extension_merge.get("scene_director_interop") if isinstance(prompt_extension_merge.get("scene_director_interop"), dict) else {}
    style_stack_metadata = (prompt_extension_merge.get("extension_metadata") or {}).get("style_stack") if isinstance(prompt_extension_merge.get("extension_metadata"), dict) else {}
    style_stack_global_only = bool(
        global_data.get("style_stack_global_only")
        or scene_director_interop.get("style_stack_applied")
        or (isinstance(style_stack_metadata, dict) and style_stack_metadata.get("enabled"))
    )
    style_stack_original_positive = global_data.get("style_stack_original_positive_prompt", "") or prompt_extension_merge.get("original_positive", "")
    style_stack_original_negative = global_data.get("style_stack_original_negative_prompt", "") or prompt_extension_merge.get("original_negative", "")
    scene = {
        "enabled": True,
        "global": {
            "prompt": global_data.get("positive_prompt", ""),
            "negative_prompt": global_data.get("negative_prompt", ""),
            "prompt_authority": normalize_prompt_authority(
                global_data.get("prompt_authority") or params.get("prompt_authority")
            ),
            "style_stack_original_positive_prompt": style_stack_original_positive,
            "style_stack_original_negative_prompt": style_stack_original_negative,
            "style_stack_global_only": style_stack_global_only,
            "style_stack_interop_source": global_data.get("style_stack_interop_source", "") or ("prompt_extension_merge" if style_stack_global_only else ""),
        },
        "regions": v1_regions,
        "size": {"width": width, "height": height},
        "contracts": inputs.get("contracts", DEFAULT_CONTRACTS),
        "prompt_context": params.get("region_context", {}),
        "mask_refine": params.get("mask_refine", {}),
    }
    base["scene_director"] = scene
    base["scene_director_enabled"] = True
    base["scene_director_v052_base_weight"] = params.get("base_weight", 0.35)
    base["scene_director_v052_region_gain"] = params.get("region_gain", 0.65)
    base["scene_director_authority_mode"] = params.get("authority_mode", "balanced")
    base["scene_director_prompt_authority"] = normalize_prompt_authority(
        params.get("prompt_authority") or global_data.get("prompt_authority")
    )
    context = params.get("region_context") if isinstance(params.get("region_context"), dict) else {}
    base["scene_director_region_context_enabled"] = context.get("enabled", True)
    base["scene_director_region_context_mode"] = context.get("mode", "global_and_style")
    base["scene_director_region_context_weight"] = context.get("weight", 0.35)
    base["scene_director_prompt_authority_contract"] = build_prompt_authority_contract(
        params,
        global_positive=global_data.get("positive_prompt"),
        global_negative=global_data.get("negative_prompt"),
        style_positive=global_data.get("style_prompt"),
        region_context_weight=context.get("weight", 0.35),
    )
    mask_refine = params.get("mask_refine") if isinstance(params.get("mask_refine"), dict) else {}
    base["scene_director_mask_refine_enabled"] = bool(mask_refine.get("enabled"))
    character_lock = params.get("character_lock") if isinstance(params.get("character_lock"), dict) else _character_lock_params(params)
    base["scene_director_character_lock_mode"] = character_lock.get("character", params.get("character_lock_mode", "balanced"))
    base["scene_director_gender_guard_mode"] = character_lock.get("gender", "off")
    base["scene_director_skin_tone_guard_mode"] = character_lock.get("skin_tone", "off")
    base["scene_director_hair_guard_mode"] = character_lock.get("hair", "off")
    base["scene_director_build_guard_mode"] = character_lock.get("build", "off")
    base["scene_director_body_height_guard_mode"] = character_lock.get("body_height", "off")
    base["scene_director_outfit_preservation_mode"] = character_lock.get("outfit", "off")
    base["scene_director_negative_identity_guard_mode"] = character_lock.get("negative", "balanced")
    base["scene_director_identity_strength"] = params.get("identity_strength", 0.55)
    base["scene_director_detail_strength"] = params.get("detail_strength", 0.85)
    base["scene_director_background_strength"] = params.get("background_strength", 0.65)
    base["scene_director_mask_feather"] = params.get("mask_feather", 18)
    appearance = params.get("appearance_lock") if isinstance(params.get("appearance_lock"), dict) else _character_lock_to_legacy_appearance(character_lock, float(params.get("identity_strength", 0.55)), int(params.get("mask_feather", 18)))
    base["scene_director_appearance_lock_enabled"] = bool(appearance.get("enabled"))
    base["scene_director_appearance_lock_mode"] = appearance.get("mode", "hair_focus_soft")
    base["scene_director_appearance_lock_gain"] = appearance.get("gain", params.get("identity_strength", 0.55))
    base["scene_director_appearance_lock_height"] = appearance.get("height", 0.42)
    base["scene_director_appearance_lock_feather"] = appearance.get("feather", params.get("mask_feather", 18))
    routing = params.get("global_context_routing") if isinstance(params.get("global_context_routing"), dict) else {}
    base["scene_director_global_context_route_positive"] = routing.get("positive", True)
    base["scene_director_global_context_route_negative"] = routing.get("negative", True)
    base["scene_director_global_context_route_style"] = routing.get("style", True)
    style_stack_isolation = params.get("style_stack_isolation") if isinstance(params.get("style_stack_isolation"), dict) else {}
    apply_style_to_refinement = bool(
        style_stack_isolation.get("apply_to_region_refinement")
        or params.get("apply_global_style_to_region_refinement")
        or params.get("style_stack_apply_to_region_refinement")
    )
    base["scene_director_style_stack_apply_to_region_refinement"] = apply_style_to_refinement
    base["scene_director_style_stack_region_refinement_policy"] = "styled_global_allowed" if apply_style_to_refinement else "global_style_blocked_for_region_refinement"
    first_pass_lock = params.get("first_pass_character_lock_authority") if isinstance(params.get("first_pass_character_lock_authority"), dict) else {}
    base["scene_director_first_pass_character_lock_authority"] = {
        "schema": "neo.image.scene_director.first_pass_character_lock_authority.settings.v054.v1",
        "phase": "SD-V054-26.10.8J",
        "dedupe_phase": "V25.9.14",
        "ui_owner": "fix_pass_controls",
        "enabled": first_pass_lock.get("enabled", params.get("character_lock_first_pass_enabled", True)),
        "apply_to": first_pass_lock.get("apply_to", params.get("character_lock_first_pass_apply_to", "strong_strict_only")),
        "timing": first_pass_lock.get("timing", params.get("character_lock_first_pass_timing", "before_adapters")),
        "denoise": first_pass_lock.get("denoise", params.get("character_lock_first_pass_denoise", 0.30)),
        "steps": first_pass_lock.get("steps", params.get("character_lock_first_pass_steps", 10)),
        "cfg_mode": first_pass_lock.get("cfg_mode", params.get("character_lock_first_pass_cfg_mode", "inherit")),
        "cfg": first_pass_lock.get("cfg", params.get("character_lock_first_pass_cfg", 0)),
        "mask_source": first_pass_lock.get("mask_source", params.get("character_lock_first_pass_mask_source", "full_character_mask")),
        "mask_feather": first_pass_lock.get("mask_feather", params.get("character_lock_first_pass_mask_feather", 24)),
        "protect_outfit": first_pass_lock.get("protect_outfit", params.get("character_lock_first_pass_protect_outfit", True)),
        "protect_pose_contact": first_pass_lock.get("protect_pose_contact", params.get("character_lock_first_pass_protect_pose_contact", True)),
        "execution_mode": _character_lock_execution_mode(first_pass_lock.get("execution_mode", params.get("character_lock_execution_mode", "latent_attention"))),
        "execution": _character_lock_execution_mode(first_pass_lock.get("execution", first_pass_lock.get("execution_mode", params.get("character_lock_execution_mode", "latent_attention")))),
        "source": "fix_pass_controls_visible_fields",
    }
    base["scene_director_character_lock_execution_mode"] = _character_lock_execution_mode(base["scene_director_first_pass_character_lock_authority"].get("execution_mode", "latent_attention"))
    base["scene_director_character_lock_pass_plan"] = base["scene_director_character_lock_execution_mode"]
    base["scene_director_character_lock_execution"] = {
        "schema": "neo.image.scene_director.character_lock_execution.settings.v054.v2",
        "phase": "SD-V054-27.1",
        "dedupe_phase": "V25.9.14",
        "mode": base["scene_director_character_lock_execution_mode"],
        "pass_plan": base["scene_director_character_lock_execution_mode"],
        "in_sampler_attention_enabled": base["scene_director_character_lock_execution_mode"] in {"latent_attention", "latent_repair", "latent_and_refinement"},
        "latent_repair_enabled": base["scene_director_character_lock_execution_mode"] in {"latent_repair", "latent_and_refinement"},
        "end_refinement_enabled": base["scene_director_character_lock_execution_mode"] in {"end_refinement", "latent_and_refinement"},
        "source": "fix_pass_controls_visible_fields",
    }
    base["scene_director_character_lock_first_pass_enabled"] = base["scene_director_first_pass_character_lock_authority"].get("enabled")
    base["scene_director_character_lock_first_pass_timing"] = base["scene_director_first_pass_character_lock_authority"].get("timing")
    base["scene_director_character_lock_first_pass_denoise"] = base["scene_director_first_pass_character_lock_authority"].get("denoise")
    base["scene_director_character_lock_first_pass_steps"] = base["scene_director_first_pass_character_lock_authority"].get("steps")
    advanced_fix_pass_controls = params.get("advanced_fix_pass_controls") if isinstance(params.get("advanced_fix_pass_controls"), dict) else {}
    base["scene_director_advanced_fix_pass_controls"] = deepcopy(advanced_fix_pass_controls)
    if advanced_fix_pass_controls:
        base["scene_director_fix_pass_mode"] = advanced_fix_pass_controls.get("mode", "smart_auto")
        base["scene_director_fix_pass_first_character_lock_rescue"] = advanced_fix_pass_controls.get("first_pass_character_lock_rescue", "auto")
        base["scene_director_fix_pass_background_restore"] = advanced_fix_pass_controls.get("background_restore", "auto")
        base["scene_director_fix_pass_character_trait_lanes"] = advanced_fix_pass_controls.get("character_trait_lanes", "auto")
        base["scene_director_fix_pass_final_background_reconciliation"] = advanced_fix_pass_controls.get("final_background_reconciliation", "auto")
        base["scene_director_fix_pass_environment_aware_character_lanes"] = advanced_fix_pass_controls.get("environment_aware_character_lanes", True)
    if style_stack_global_only:
        base["scene_director_style_stack_global_only"] = True
        base["scene_director_style_stack_original_positive"] = style_stack_original_positive
        base["scene_director_style_stack_original_negative"] = style_stack_original_negative
    return scene_director_to_regional_payload(base)
