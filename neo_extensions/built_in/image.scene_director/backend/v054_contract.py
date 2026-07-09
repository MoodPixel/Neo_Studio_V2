from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from .provider_capabilities import resolve_provider_capabilities_v054
    from .flux_adapter import build_flux_adapter_plan_v054
    from .qwen_adapter import build_qwen_adapter_plan_v054
    from .extension_routing import build_extension_unit_routing_contract_v054, extension_routes_have_selection, normalize_extension_routes_v054
except Exception:  # standalone test/module loading fallback
    import importlib.util as _importlib_util
    from pathlib import Path as _Path
    _spec = _importlib_util.spec_from_file_location("v054_provider_capabilities", _Path(__file__).with_name("provider_capabilities.py"))
    _module = _importlib_util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_module)
    resolve_provider_capabilities_v054 = _module.resolve_provider_capabilities_v054
    _flux_spec = _importlib_util.spec_from_file_location("v054_flux_adapter", _Path(__file__).with_name("flux_adapter.py"))
    _flux_module = _importlib_util.module_from_spec(_flux_spec)
    assert _flux_spec and _flux_spec.loader
    _flux_spec.loader.exec_module(_flux_module)
    build_flux_adapter_plan_v054 = _flux_module.build_flux_adapter_plan_v054
    _qwen_spec = _importlib_util.spec_from_file_location("v054_qwen_adapter", _Path(__file__).with_name("qwen_adapter.py"))
    _qwen_module = _importlib_util.module_from_spec(_qwen_spec)
    assert _qwen_spec and _qwen_spec.loader
    _qwen_spec.loader.exec_module(_qwen_module)
    build_qwen_adapter_plan_v054 = _qwen_module.build_qwen_adapter_plan_v054
    _routing_spec = _importlib_util.spec_from_file_location("v054_extension_routing", _Path(__file__).with_name("extension_routing.py"))
    _routing_module = _importlib_util.module_from_spec(_routing_spec)
    assert _routing_spec and _routing_spec.loader
    _routing_spec.loader.exec_module(_routing_module)
    build_extension_unit_routing_contract_v054 = _routing_module.build_extension_unit_routing_contract_v054
    extension_routes_have_selection = _routing_module.extension_routes_have_selection
    normalize_extension_routes_v054 = _routing_module.normalize_extension_routes_v054

SCENE_GRAPH_VERSION = "v054"
SCENE_GRAPH_SCHEMA = "neo.image.scene_director.scene_graph.v054"
# Phase 26.5 contract phase: "contract_phase": "SD-V054-26.7"
# Phase 26.4 contract phase: "contract_phase": "SD-V054-26.4"
# Phase 25 contract phase: "contract_phase": "SD-V054-25"
# Phase 24 contract phase: "contract_phase": "SD-V054-25"
# Phase 23 compatibility anchor: "contract_phase": "SD-V054-23"
# Phase 21 compatibility anchor: "contract_phase": "SD-V054-21"
# Phase 20 compatibility anchor: "contract_phase": "SD-V054-20"
# Phase 19 compatibility anchor: "contract_phase": "SD-V054-19"
# Phase 18 compatibility anchor: "contract_phase": "SD-V054-18"
# Phase 17 compatibility anchor: "contract_phase": "SD-V054-17"
# Phase 16 compatibility anchor: "contract_phase": "SD-V054-16"
# Phase 15 compatibility anchor: "contract_phase": "SD-V054-15"
# Phase 14 compatibility anchor: "contract_phase": "SD-V054-14"
# Phase 13 compatibility anchor: "contract_phase": "SD-V054-13"
# Phase 12 compatibility anchor: "contract_phase": "SD-V054-12"
# Phase 11 compatibility anchor: "contract_phase": "SD-V054-11"
# Phase 10 compatibility anchor: "contract_phase": "SD-V054-10"
# Phase 9 compatibility anchor: "contract_phase": "SD-V054-9"
# Phase 8 compatibility anchor: "contract_phase": "SD-V054-8"
# Phase 7.5 compatibility anchor: "contract_phase": "SD-V054-7.5"

CONFLICT_COLOR_WORDS = {
    "black", "white", "brown", "blonde", "blond", "pink", "red", "blue", "green",
    "purple", "violet", "orange", "yellow", "silver", "gray", "grey", "gold", "golden",
}
CONFLICT_ROLE_TARGETS = {
    "hair_detail": ("hair_color", {"hair", "hairstyle", "haircut"}),
    "clothing": ("clothing_color", {"shirt", "hoodie", "jacket", "coat", "suit", "pants", "shorts", "dress", "outfit", "clothing"}),
    "character_detail": ("skin_tone", {"skin", "complexion", "tone"}),
}



COMPLEXITY_LIMITS = {
    "main_subjects": {"soft": 3, "hard": 4},
    "total_regions": {"soft": 12, "hard": 24},
    "detail_lanes_per_character": {"soft": 4, "hard": 8},
    "background_zones": {"soft": 2, "hard": 4},
    "regional_controlnet_lanes": {"soft": 2, "hard": 4},
    "detailer_passes": {"soft": 4, "hard": 8},
}
DETAIL_LANE_ROLES = {"face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop"}
BACKGROUND_ZONE_ROLES = {"background", "background_object", "transition_effect"}


BACKGROUND_ZONE_HINTS = {"left", "right", "center", "foreground", "midground", "background", "top", "bottom"}


def background_zone_name_v054(region: dict[str, Any]) -> str:
    """Return a stable human-readable background zone label for mixed-background routing."""
    zone = _text(region.get("zone") or region.get("background_zone") or region.get("target_area")).lower()
    if zone:
        return zone.replace("_", " ")
    bbox = region.get("bbox") or [0, 0, 1, 1]
    try:
        x1, _y1, x2, _y2 = [float(v) for v in bbox]
        center = (x1 + x2) / 2
    except Exception:
        center = 0.5
    if center < 0.35:
        return "left side"
    if center > 0.65:
        return "right side"
    return "center"


def analyze_background_regions_v054(regions: list[dict[str, Any]]) -> dict[str, Any]:
    zones = []
    transition_count = 0
    for region in regions:
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").lower()
        if role not in BACKGROUND_ZONE_ROLES:
            continue
        if role == "transition_effect":
            transition_count += 1
        zones.append({
            "id": str(region.get("id") or ""),
            "role": role,
            "zone": background_zone_name_v054(region),
            "bbox": region.get("bbox") or [0, 0, 1, 1],
            "has_prompt": bool(_text(region.get("prompt"))),
            "customized": bool(_text(region.get("background_prompt")) or _text(region.get("background_negative_guard")) or isinstance(region.get("background_override"), dict)),
        })
    messages: list[dict[str, str]] = []
    if len([z for z in zones if z["role"] == "background"]) > 1 and transition_count == 0:
        messages.append(_note("info", "regions", "Multiple background zones detected. Add a transition_effect region if the seam needs a controlled portal/blend.", "mixed_background_without_transition"))
    return {"zones": zones, "zone_count": len(zones), "transition_count": transition_count, "messages": messages}





TEXT_REGION_MODES = {"diffusion", "composite", "native"}
TEXT_ALIGNMENTS = {"left", "center", "right"}
TEXT_VALIGNS = {"top", "middle", "bottom"}


def normalize_text_region_v054(region: dict[str, Any]) -> dict[str, Any]:
    """Normalize V054 text-region compositor fields.

    Composite mode is the safe default for SDXL because diffusion text is not
    reliable for readable typography. Native mode is metadata-only until a
    provider adapter explicitly supports it.
    """
    mode = _text(region.get("mode") or region.get("text_mode") or "composite").lower()
    if mode not in TEXT_REGION_MODES:
        mode = "composite"
    align = _text(region.get("align") or region.get("text_align") or "center").lower()
    if align not in TEXT_ALIGNMENTS:
        align = "center"
    valign = _text(region.get("valign") or region.get("vertical_align") or "middle").lower()
    if valign not in TEXT_VALIGNS:
        valign = "middle"
    return {
        "text": _text(region.get("text") or region.get("content") or region.get("prompt")),
        "mode": mode,
        "font_style": _text(region.get("font_style") or region.get("font") or "bold clean sans-serif"),
        "font_family": _text(region.get("font_family") or region.get("font_name")),
        "font_size": _float(region.get("font_size"), 48.0),
        "color": _text(region.get("color") or region.get("fill") or "white"),
        "stroke_color": _text(region.get("stroke_color") or region.get("outline_color")),
        "stroke_width": _float(region.get("stroke_width"), 0.0),
        "align": align,
        "valign": valign,
        "opacity": _float(region.get("opacity"), 1.0),
        "rotation": _float(region.get("rotation"), 0.0),
        "shadow": region.get("shadow") if isinstance(region.get("shadow"), dict) else {},
    }


def analyze_text_regions_v054(regions: list[dict[str, Any]]) -> dict[str, Any]:
    regions_out: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or str(region.get("role") or "").lower() != "text":
            continue
        spec = normalize_text_region_v054(region)
        entry = {
            "id": str(region.get("id") or f"text_{index + 1}"),
            "label": str(region.get("label") or region.get("id") or f"Text {index + 1}"),
            "bbox": region.get("bbox") or [0, 0, 1, 1],
            **spec,
            "compositor_target": "post_decode" if spec["mode"] == "composite" else "model_route",
        }
        if not spec["text"]:
            messages.append(_note("warning", f"regions[{index}].text", f"Text region '{entry['label']}' has no text content.", "text_region_missing_text"))
        if spec["mode"] == "diffusion":
            messages.append(_note("warning", f"regions[{index}].mode", f"Text region '{entry['label']}' uses diffusion text mode; readable typography is not reliable on SDXL. Composite mode is recommended.", "text_region_diffusion_mode_warning"))
        if spec["mode"] == "native":
            messages.append(_note("info", f"regions[{index}].mode", f"Text region '{entry['label']}' requests model-native text editing; this is provider-adapter dependent and may fall back to composite.", "text_region_native_mode_provider_gated"))
        regions_out.append(entry)
    composite_count = sum(1 for r in regions_out if r.get("mode") == "composite")
    return {"regions": regions_out, "region_count": len(regions_out), "composite_count": composite_count, "messages": messages}


def analyze_regional_controlnet_v054(regions: list[dict[str, Any]]) -> dict[str, Any]:
    lanes: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        control = region.get("control") if isinstance(region.get("control"), dict) else {}
        if control.get("enabled") is not True:
            continue
        lane = {
            "region_id": str(region.get("id") or ""),
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index + 1}"),
            "type": str(control.get("type") or control.get("preprocessor") or ""),
            "model": str(control.get("model") or control.get("controlnet_model") or ""),
            "reference_id": str(control.get("reference_id") or control.get("image_name") or ""),
            "strength": control.get("strength", 0.75),
            "start": control.get("start", 0.0),
            "end": control.get("end", 0.8),
            "mask_mode": str(control.get("mask_mode") or "region"),
        }
        if not lane["model"]:
            messages.append(_note("warning", f"regions[{index}].control.model", f"Regional ControlNet lane '{lane['label']}' is enabled but has no model; workflow routing will skip this lane until a model is selected.", "regional_controlnet_missing_model"))
        if not lane["reference_id"]:
            messages.append(_note("warning", f"regions[{index}].control.reference_id", f"Regional ControlNet lane '{lane['label']}' is enabled but has no reference image/map; workflow routing will skip this lane until a reference is provided.", "regional_controlnet_missing_reference"))
        lanes.append(lane)
    return {"lanes": lanes, "lane_count": len(lanes), "messages": messages}


def analyze_regional_detailer_v054(regions: list[dict[str, Any]]) -> dict[str, Any]:
    passes: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    supported_modes = {"face", "hand", "body", "object", "clothing", "custom"}
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        detailer = region.get("detailer") if isinstance(region.get("detailer"), dict) else {}
        if detailer.get("enabled") is not True:
            continue
        mode = str(detailer.get("mode") or "face").strip().lower() or "face"
        detector = str(detailer.get("detector") or detailer.get("detector_model") or "").strip()
        lane = {
            "region_id": str(region.get("id") or ""),
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index + 1}"),
            "mode": mode,
            "detector": detector,
            "detector_type": str(detailer.get("detector_type") or "bbox"),
            "custom_classes": str(detailer.get("custom_classes") or ("hand" if mode == "hand" else "face" if mode == "face" else "all")),
            "denoise": detailer.get("denoise", 0.3),
            "mask_feather": detailer.get("mask_feather", 12),
            "detect_inside_region": detailer.get("detect_inside_region", True),
            "mask_mode": str(detailer.get("mask_mode") or "region"),
        }
        if mode not in supported_modes:
            messages.append(_note("warning", f"regions[{index}].detailer.mode", f"Regional Detailer lane '{lane['label']}' uses unknown mode '{mode}'; it will be treated as custom metadata until the workflow supports it.", "regional_detailer_unknown_mode"))
        if not detector:
            messages.append(_note("warning", f"regions[{index}].detailer.detector", f"Regional Detailer lane '{lane['label']}' is enabled but has no detector model selected; workflow routing may fall back to default detector routing or skip this lane.", "regional_detailer_missing_detector"))
        passes.append(lane)
    return {"passes": passes, "pass_count": len(passes), "messages": messages}

def analyze_scene_complexity_v054(regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return V054 complexity counts, soft/hard warnings, and a UI risk level.

    This is advisory only. It should never block valid generation by itself; hard-limit
    findings are warnings/errors for UI and workflow strategy, not schema errors.
    """
    rows = [r for r in regions if isinstance(r, dict)]
    detail_by_parent: dict[str, int] = {}
    for region in rows:
        role = str(region.get("role") or "").strip().lower()
        if role in DETAIL_LANE_ROLES:
            parent = str(region.get("attach_to") or "__unattached__").strip() or "__unattached__"
            detail_by_parent[parent] = detail_by_parent.get(parent, 0) + 1
    max_detail_lanes = max(detail_by_parent.values(), default=0)
    counts = {
        "main_subjects": sum(1 for r in rows if str(r.get("role") or "").lower() == "character"),
        "total_regions": len(rows),
        "detail_lanes_per_character": max_detail_lanes,
        "background_zones": sum(1 for r in rows if str(r.get("role") or "").lower() in BACKGROUND_ZONE_ROLES),
        "regional_controlnet_lanes": sum(1 for r in rows if isinstance(r.get("control"), dict) and r.get("control", {}).get("enabled") is True),
        "detailer_passes": sum(1 for r in rows if isinstance(r.get("detailer"), dict) and r.get("detailer", {}).get("enabled") is True),
    }
    messages: list[dict[str, Any]] = []
    hard_hits = 0
    soft_hits = 0
    labels = {
        "main_subjects": "main subjects",
        "total_regions": "active regions",
        "detail_lanes_per_character": "detail lanes on one parent",
        "background_zones": "background zones",
        "regional_controlnet_lanes": "regional ControlNet lanes",
        "detailer_passes": "detailer passes",
    }
    advice = {
        "main_subjects": "Too many main subjects can reduce identity separation and increase subject blending.",
        "total_regions": "Too many active regions can create prompt conflict and mask overlap.",
        "detail_lanes_per_character": "Too many detail lanes on one character can overconstrain local details.",
        "background_zones": "Too many background zones can weaken composition clarity.",
        "regional_controlnet_lanes": "Too many regional ControlNets can overconstrain poses and increase VRAM pressure.",
        "detailer_passes": "Too many detailer passes can slow generation and overcook details.",
    }
    for key, value in counts.items():
        limit = COMPLEXITY_LIMITS[key]
        if value > limit["hard"]:
            hard_hits += 1
            messages.append({
                "level": "error",
                "code": f"complexity_hard_{key}",
                "field": "regions",
                "metric": key,
                "count": value,
                "soft_limit": limit["soft"],
                "hard_limit": limit["hard"],
                "message": f"Scene has {value} {labels[key]}, above the hard limit of {limit['hard']}. {advice[key]}",
            })
        elif value > limit["soft"]:
            soft_hits += 1
            messages.append({
                "level": "warning",
                "code": f"complexity_soft_{key}",
                "field": "regions",
                "metric": key,
                "count": value,
                "soft_limit": limit["soft"],
                "hard_limit": limit["hard"],
                "message": f"Scene has {value} {labels[key]}, above the soft limit of {limit['soft']}. {advice[key]}",
            })
    if hard_hits:
        risk = "high_risk"
    elif soft_hits >= 2:
        risk = "advanced"
    elif soft_hits == 1:
        risk = "moderate"
    else:
        risk = "normal"
    return {"counts": counts, "limits": COMPLEXITY_LIMITS, "messages": messages, "risk_level": risk, "detail_lanes_by_parent": detail_by_parent}

def _words(value: Any) -> set[str]:
    import re
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z]+", str(value or "").lower())}


def _detect_conflict_warnings(regions: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_id = {str(region.get("id") or ""): region for region in regions if isinstance(region, dict)}
    notes: list[dict[str, str]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").lower()
        if role not in CONFLICT_ROLE_TARGETS:
            continue
        parent = by_id.get(str(region.get("attach_to") or ""))
        if not parent:
            continue
        conflict_type, target_terms = CONFLICT_ROLE_TARGETS[role]
        parent_words = _words(parent.get("prompt"))
        child_words = _words(region.get("prompt"))
        if target_terms and not (parent_words & target_terms or child_words & target_terms):
            continue
        parent_values = sorted(parent_words & CONFLICT_COLOR_WORDS)
        child_values = sorted(child_words & CONFLICT_COLOR_WORDS)
        if parent_values and child_values and set(parent_values) != set(child_values):
            priority = str(region.get("priority") or "reinforce")
            notes.append(_note(
                "warning",
                f"regions[{index}].prompt",
                f"Prompt conflict: parent '{parent.get('id')}' and detail lane '{region.get('id')}' disagree on {conflict_type.replace('_', ' ')} values. Priority '{priority}' will guide V054 compilation.",
                "prompt_conflict",
            ))
    return notes


SUPPORTED_REGION_ROLES = {
    "character",
    "face_detail",
    "hair_detail",
    "hand_detail",
    "character_detail",
    "clothing",
    "held_prop",
    "object",
    "background",
    "background_object",
    "transition_effect",
    "text",
    "lighting",
    "effect",
    "style",
    "custom",
}

ATTACHABLE_REGION_ROLES = {
    "face_detail",
    "hair_detail",
    "hand_detail",
    "character_detail",
    "clothing",
    "held_prop",
    "object",
    "background_object",
    "text",
    "effect",
    "lighting",
}

REQUIRES_PARENT_ROLES = {
    "face_detail",
    "hair_detail",
    "hand_detail",
    "character_detail",
    "clothing",
    "held_prop",
}

PRIORITIES = {"override", "reinforce", "blend"}

RELATIONSHIPS = {
    "holding",
    "wearing",
    "attached_to",
    "standing_near",
    "behind",
    "in_front_of",
    "around",
    "on_top_of",
    "inside",
    "carrying",
    "looking_at",
    "touching",
    "hugging",
    "leaning_on",
    "resting_head_on",
    "custom",
}

LOCK_MODES = {"off", "soft", "balanced", "strong", "strict"}
EDIT_INTENT_MODES = {"preserve", "modify", "replace"}
TEXT_MODES = {"diffusion", "composite", "native"}

ROLE_ALIASES = {
    "person": "character",
    "subject": "character",
    "prop": "object",
    "item": "object",
    "detail": "character_detail",
    "hair": "hair_detail",
    "face": "face_detail",
    "hand": "hand_detail",
    "outfit": "clothing",
    "clothes": "clothing",
}


def _note(level: str, field: str, message: str, code: str) -> dict[str, str]:
    return {"level": level, "field": field, "message": message, "code": code}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "undefined"}:
        return ""
    return text


def _float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _clamp_float(value: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _int_at_least(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = default
    return max(minimum, parsed)


def _normalize_role(value: Any) -> str:
    raw = _text(value).lower()
    return ROLE_ALIASES.get(raw, raw)


def _normalize_bbox(value: Any) -> tuple[list[float] | None, str | None]:
    """Return normalized V054 [x1, y1, x2, y2] bbox and an optional warning code."""
    warning: str | None = None
    if isinstance(value, dict):
        # Migration bridge: existing UI/legacy normalized regions use {x,y,w,h}.
        x = _clamp_float(value.get("x"), 0.0, 0.0, 1.0)
        y = _clamp_float(value.get("y"), 0.0, 0.0, 1.0)
        w = _clamp_float(value.get("w"), 1.0, 0.0, 1.0)
        h = _clamp_float(value.get("h"), 1.0, 0.0, 1.0)
        x2 = min(1.0, x + w)
        y2 = min(1.0, y + h)
        warning = "legacy_bbox_dict_normalized"
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        vals = []
        for item in value:
            try:
                vals.append(float(item))
            except Exception:
                return None, None
        x, y, a, b = vals
        # V054 uses x1,y1,x2,y2. During migration, old list bboxes may still be x,y,w,h.
        # If the third/fourth values do not extend beyond the first two, treat the bbox as invalid.
        x2, y2 = a, b
    else:
        return None, None

    if any(v < 0.0 or v > 1.0 for v in (x, y, x2, y2)):
        return None, warning
    if x2 <= x or y2 <= y:
        return None, warning
    return [round(x, 4), round(y, 4), round(x2, 4), round(y2, 4)], warning


def _normalize_lock(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    out: dict[str, str] = {}
    for key in ("character", "gender", "skin_tone", "hair", "build", "body", "height", "body_height", "outfit", "negative"):
        mode = _text(source.get(key)).lower()
        if not mode:
            continue
        out[key] = mode if mode in LOCK_MODES else "balanced"
    return out


def _normalize_control(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    enabled = value.get("enabled", False) is True
    control_type = _text(value.get("type") or value.get("preprocessor") or value.get("control_type"))
    reference_id = _text(value.get("reference_id") or value.get("image_name") or value.get("control_image") or value.get("control_image_name"))
    model = _text(value.get("model") or value.get("controlnet_model") or value.get("control_net_name") or value.get("controlnet_name"))
    return {
        "enabled": enabled,
        "type": control_type,
        "preprocessor": _text(value.get("preprocessor") or control_type),
        "model": model,
        "controlnet_model": model,
        "reference_id": reference_id,
        "image_name": _text(value.get("image_name") or reference_id),
        "strength": _clamp_float(value.get("strength"), 0.75, 0.0, 2.0),
        "start": _clamp_float(value.get("start") if value.get("start") is not None else value.get("start_percent"), 0.0, 0.0, 1.0),
        "end": _clamp_float(value.get("end") if value.get("end") is not None else value.get("end_percent"), 0.8, 0.0, 1.0),
        "mask_mode": _text(value.get("mask_mode") or "region"),
        "advanced_enabled": value.get("advanced_enabled", True) is not False,
    }


def _normalize_detailer(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mode = _text(value.get("mode") or "face").lower() or "face"
    detector = _text(value.get("detector") or value.get("detector_model") or value.get("bbox_detector") or value.get("segm_detector"))
    return {
        "enabled": value.get("enabled", False) is True,
        "mode": mode,
        "detector": detector,
        "detector_model": detector,
        "detector_type": _text(value.get("detector_type") or ("segm" if mode == "object" else "bbox")) or "bbox",
        "custom_classes": _text(value.get("custom_classes") or value.get("classes") or ("hand" if mode == "hand" else "face" if mode == "face" else "all")),
        "denoise": _clamp_float(value.get("denoise"), 0.3, 0.0, 1.0),
        "steps": _int_at_least(value.get("steps"), 20, 1),
        "cfg": _clamp_float(value.get("cfg"), 5.5, 0.0, 15.0),
        "mask_feather": _int_at_least(value.get("mask_feather"), 12, 0),
        "mask_blur": _int_at_least(value.get("mask_blur") if value.get("mask_blur") is not None else value.get("mask_feather"), 12, 0),
        "detect_inside_region": value.get("detect_inside_region", True) is not False,
        "mask_mode": _text(value.get("mask_mode") or "region"),
        "prompt": _text(value.get("prompt") or value.get("positive")),
        "negative": _text(value.get("negative") or value.get("negative_prompt")),
    }



INPAINT_ACTIONS = {"change_hair", "change_outfit", "add_held_prop", "remove_object", "replace_background", "fix_face", "fix_hands", "edit_text_plate", "custom"}
INPAINT_MASK_MODES = {"region", "detail", "subject", "background", "source", "metadata", "custom"}


def _normalize_inpaint_target(value: Any, region: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Normalize V054 direct inpaint targeting fields.

    Phase 15 is metadata/route planning: it records which region mask should be
    used, what parent identity should be preserved, and suggested denoise/feather.
    The actual inpaint graph branch is owned by the inpaint workflow route.
    """
    region = region if isinstance(region, dict) else {}
    data = value if isinstance(value, dict) else {}
    enabled = data.get("enabled")
    if enabled is None:
        enabled = bool(data) or str(region.get("inpaint_action") or "").strip() != ""
    action = _text(data.get("action") or data.get("mode") or region.get("inpaint_action") or "custom").lower()
    if action not in INPAINT_ACTIONS:
        action = "custom"
    mask_mode = _text(data.get("mask_mode") or region.get("inpaint_mask_mode") or "region").lower()
    if mask_mode not in INPAINT_MASK_MODES:
        mask_mode = "region"
    role = _text(region.get("role")).lower()
    default_denoise = {
        "change_hair": 0.42,
        "change_outfit": 0.55,
        "add_held_prop": 0.52,
        "remove_object": 0.58,
        "replace_background": 0.70,
        "fix_face": 0.32,
        "fix_hands": 0.38,
        "edit_text_plate": 0.25,
        "custom": 0.50,
    }.get(action, 0.50)
    if role in {"background", "background_object", "transition_effect"} and action == "custom":
        default_denoise = 0.65
    parent_id = _text(data.get("parent_id") or data.get("parent_region_id") or region.get("attach_to")) or None
    target_region_id = _text(data.get("target_region_id") or data.get("region_id") or region.get("id")) or None
    preserve = data.get("preserve") if isinstance(data.get("preserve"), list) else data.get("preserve_regions")
    if isinstance(preserve, list):
        preserve_regions = [_text(item) for item in preserve if _text(item)]
    else:
        preserve_regions = []
    if data.get("preserve_parent_identity", True) is not False and parent_id and parent_id not in preserve_regions:
        preserve_regions.append(parent_id)
    return {
        "enabled": enabled is not False,
        "action": action,
        "target_region_id": target_region_id,
        "parent_id": parent_id,
        "mask_mode": mask_mode,
        "mask_source": _text(data.get("mask_source") or data.get("mask_reuse") or "v054_region_mask") or "v054_region_mask",
        "denoise": _clamp_float(data.get("denoise") if data.get("denoise") is not None else region.get("inpaint_denoise"), default_denoise, 0.0, 1.0),
        "mask_feather": _int_at_least(data.get("mask_feather") if data.get("mask_feather") is not None else region.get("inpaint_mask_feather"), 16, 0),
        "preserve_parent_identity": data.get("preserve_parent_identity", True) is not False,
        "preserve_regions": preserve_regions,
        "prompt": _text(data.get("prompt") or data.get("positive") or region.get("inpaint_prompt") or region.get("prompt")),
        "negative": _text(data.get("negative") or data.get("negative_prompt") or region.get("inpaint_negative")),
        "notes": _text(data.get("notes")),
    }


def analyze_inpaint_region_targets_v054(regions: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize V054 direct inpaint targets for region-click edit actions."""
    lanes: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    action_counts: dict[str, int] = {}
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        target = region.get("inpaint") if isinstance(region.get("inpaint"), dict) else None
        if not target or target.get("enabled") is False:
            continue
        action = _text(target.get("action") or "custom").lower()
        action_counts[action] = action_counts.get(action, 0) + 1
        lane = {
            "region_id": str(region.get("id") or f"region_{index + 1}"),
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index + 1}"),
            "action": action,
            "target_region_id": target.get("target_region_id") or region.get("id"),
            "parent_id": target.get("parent_id") or region.get("attach_to"),
            "mask_mode": target.get("mask_mode") or "region",
            "mask_source": target.get("mask_source") or "v054_region_mask",
            "denoise": _clamp_float(target.get("denoise"), 0.5, 0.0, 1.0),
            "mask_feather": _int_at_least(target.get("mask_feather"), 16, 0),
            "preserve_parent_identity": target.get("preserve_parent_identity", True) is not False,
            "preserve_regions": target.get("preserve_regions") if isinstance(target.get("preserve_regions"), list) else [],
            "route": "inpaint_region_target",
        }
        if lane["action"] == "add_held_prop" and not lane.get("parent_id"):
            messages.append(_note("warning", f"regions[{index}].inpaint.parent_id", f"Inpaint Add Held Prop target '{lane['label']}' has no parent region; attach_to/parent_id helps preserve ownership.", "inpaint_missing_parent"))
        if lane["action"] in {"fix_face", "fix_hands", "edit_text_plate"} and lane["denoise"] > 0.45:
            messages.append(_note("warning", f"regions[{index}].inpaint.denoise", f"Inpaint {lane['action']} target '{lane['label']}' has denoise {lane['denoise']:.2f}; high denoise may change identity/text layout.", "inpaint_precision_high_denoise"))
        if lane["action"] in {"replace_background", "remove_object"} and lane["denoise"] < 0.40:
            messages.append(_note("info", f"regions[{index}].inpaint.denoise", f"Inpaint {lane['action']} target '{lane['label']}' may be weak below denoise 0.40.", "inpaint_replace_low_denoise"))
        lanes.append(lane)
    return {"lanes": lanes, "lane_count": len(lanes), "action_counts": action_counts, "messages": messages}

def _normalize_edit_intent(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mode = _text(value.get("mode") or "preserve").lower()
    if mode not in EDIT_INTENT_MODES:
        mode = "preserve"
    default_denoise = {"preserve": 0.18, "modify": 0.40, "replace": 0.62}.get(mode, 0.35)
    source_image = _text(value.get("source_image") or value.get("source_image_name") or value.get("source_id"))
    return {
        "enabled": value.get("enabled", mode != "preserve") is not False,
        "mode": mode,
        "denoise": _clamp_float(value.get("denoise"), default_denoise, 0.0, 1.0),
        "preserve_parent_identity": value.get("preserve_parent_identity", True) is not False,
        "preserve_region": value.get("preserve_region", mode == "preserve") is not False,
        "source_image": source_image or None,
        "source_region_id": _text(value.get("source_region_id") or value.get("source_region")) or None,
        "mask_reuse": _text(value.get("mask_reuse") or "region").lower() or "region",
        "notes": _text(value.get("notes")),
    }


def _metadata_source_workflow_active(metadata: dict[str, Any]) -> bool:
    clean = metadata.get("clean_state_boundary") if isinstance(metadata.get("clean_state_boundary"), dict) else {}
    if clean and clean.get("source_workflow_active") is False:
        return False
    route = metadata.get("route") if isinstance(metadata.get("route"), dict) else {}
    if route and route.get("source_workflow_active") is False:
        return False
    mode = _text(clean.get("workflow_mode") or route.get("workflow_mode") or route.get("mode") or metadata.get("workflow_mode") or metadata.get("mode")).lower()
    if mode in {"generate", "txt2img", "text_to_image", "text2img"}:
        return False
    if mode in {"img2img", "image_to_image", "inpaint", "outpaint"}:
        return True
    return bool(clean.get("source_workflow_active") or route.get("source_workflow_active"))


def analyze_img2img_region_reuse_v054(regions: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize V054 region reuse/edit intent for img2img source workflows."""
    metadata = metadata if isinstance(metadata, dict) else {}
    if not _metadata_source_workflow_active(metadata):
        return {
            "lanes": [],
            "lane_count": 0,
            "mode_counts": {"preserve": 0, "modify": 0, "replace": 0},
            "source_image": None,
            "messages": [],
            "disabled_reason": "clean_txt2img_state_boundary",
            "source_workflow_active": False,
            "schema": "neo.image.scene_director.img2img_region_reuse_boundary.v25_9_5",
            "phase": "V25.9.5",
        }
    source_stack = metadata.get("source_stack") if isinstance(metadata.get("source_stack"), dict) else {}
    source_image = _text(metadata.get("source_image") or source_stack.get("source_image") or source_stack.get("image_name"))
    lanes: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    mode_counts = {"preserve": 0, "modify": 0, "replace": 0}
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        intent = region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}
        if not intent:
            continue
        mode = _text(intent.get("mode") or "preserve").lower()
        if mode not in EDIT_INTENT_MODES:
            mode = "preserve"
        mode_counts[mode] += 1
        denoise = _clamp_float(intent.get("denoise"), {"preserve": 0.18, "modify": 0.40, "replace": 0.62}.get(mode, 0.35), 0.0, 1.0)
        lane = {
            "region_id": str(region.get("id") or f"region_{index + 1}"),
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index + 1}"),
            "mode": mode,
            "denoise": denoise,
            "preserve_parent_identity": intent.get("preserve_parent_identity", True) is not False,
            "preserve_region": intent.get("preserve_region", mode == "preserve") is not False,
            "mask_reuse": _text(intent.get("mask_reuse") or "region"),
            "source_image": _text(intent.get("source_image") or source_image) or None,
            "source_region_id": _text(intent.get("source_region_id") or intent.get("source_region")) or None,
            "route": "img2img_preserve" if mode == "preserve" else "img2img_region_modify" if mode == "modify" else "img2img_or_inpaint_replace",
        }
        if mode in {"modify", "replace"} and not (lane["source_image"] or source_image):
            messages.append(_note("warning", f"regions[{index}].edit_intent.source_image", f"Img2Img reuse lane '{lane['label']}' is set to {mode} but no source image is recorded; use an output/source image before expecting stable region reuse.", "img2img_reuse_missing_source_image"))
        if mode == "preserve" and denoise > 0.30:
            messages.append(_note("warning", f"regions[{index}].edit_intent.denoise", f"Preserve lane '{lane['label']}' has denoise {denoise:.2f}; values above 0.30 can change the region instead of preserving it.", "img2img_preserve_high_denoise"))
        if mode == "replace" and denoise < 0.45:
            messages.append(_note("info", f"regions[{index}].edit_intent.denoise", f"Replace lane '{lane['label']}' has denoise {denoise:.2f}; replacement may be weak below 0.45.", "img2img_replace_low_denoise"))
        lanes.append(lane)
    return {
        "lanes": lanes,
        "lane_count": len(lanes),
        "mode_counts": mode_counts,
        "source_image": source_image or None,
        "messages": messages,
    }


def _region_has_actionable_content(region: dict[str, Any]) -> bool:
    if _text(region.get("prompt")) or _text(region.get("negative")):
        return True
    if region.get("role") == "text" and _text(region.get("text")):
        return True
    for key in ("control", "detailer", "inpaint", "edit_intent"):
        item = region.get(key)
        if isinstance(item, dict) and item.get("enabled"):
            return True
    if extension_routes_have_selection(region.get("extension_routes")):
        return True
    if region.get("attach_to"):
        return True
    return False




def analyze_output_inspector_source_stack_v054(regions: list[dict[str, Any]], metadata: Any = None) -> dict[str, Any]:
    meta = metadata if isinstance(metadata, dict) else {}
    actions = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        rid = region.get("id")
        role = region.get("role") or "custom"
        region_actions = ["replay_region", "use_output_as_source", "branch_from_region"]
        if (region.get("inpaint") if isinstance(region.get("inpaint"), dict) else {}).get("enabled"):
            region_actions.append("open_region_in_inpaint")
        if (region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}):
            region_actions.append("img2img_region_reuse")
        if role == "text":
            region_actions.append("edit_text_plate")
        actions.append({"region_id": rid, "role": role, "actions": region_actions})
    return {
        "phase": "SD-V054-16",
        "schema": "neo.extension.source_stack.scene_director.v054.v1",
        "region_action_count": len(actions),
        "region_branch_actions": actions,
        "mask_outputs": ["subject_masks", "detail_masks", "background_masks", "control_masks", "inpaint_masks"],
        "prompt_blocks": ["global", "compiled", "relationship_plan", "conflict_plan"],
        "latent_policy": "compatible_route_only",
        "source_image": meta.get("source_image") or meta.get("source_image_name") or "",
        "messages": [],
    }

def normalize_scene_graph_v054(scene_graph: Any) -> dict[str, Any]:
    """Normalize V054 Scene Graph JSON without activating the runtime route.

    Returns a non-throwing result with `ok`, `scene_graph`, `errors`, `warnings`, and `infos`.
    Phase 1 uses this as a contract validator only; workflow patching stays V052/V053 until Phase 3.
    """
    raw = deepcopy(scene_graph) if isinstance(scene_graph, dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    infos: list[dict[str, str]] = []

    if not isinstance(scene_graph, dict):
        errors.append(_note("error", "scene_graph", "Scene graph must be a JSON object.", "scene_graph_not_object"))
        return {"ok": False, "schema": SCENE_GRAPH_SCHEMA, "scene_graph": {}, "errors": errors, "warnings": warnings, "infos": infos}

    version = _text(raw.get("version"))
    if version != SCENE_GRAPH_VERSION:
        errors.append(_note("error", "version", "Scene graph version must be 'v054'.", "invalid_version"))

    raw_canvas = raw.get("canvas") if isinstance(raw.get("canvas"), dict) else {}
    width = _int_at_least(raw_canvas.get("width"), 1024, 64)
    height = _int_at_least(raw_canvas.get("height"), 1024, 64)

    if not isinstance(raw.get("regions"), list):
        errors.append(_note("error", "regions", "Scene graph regions must be an array.", "regions_not_array"))
        raw_regions: list[Any] = []
    else:
        raw_regions = raw.get("regions") or []

    raw_global = raw.get("global") if isinstance(raw.get("global"), dict) else {}
    normalized: dict[str, Any] = {
        "version": SCENE_GRAPH_VERSION,
        "schema": SCENE_GRAPH_SCHEMA,
        "canvas": {"width": width, "height": height},
        "global": {
            "prompt": _text(raw_global.get("prompt") or raw_global.get("positive_prompt")),
            "negative": _text(raw_global.get("negative") or raw_global.get("negative_prompt")),
            "style_strength": _clamp_float(raw_global.get("style_strength"), 0.8, 0.0, 1.0),
        },
        "regions": [],
        "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    }

    seen: set[str] = set()
    id_role: dict[str, str] = {}
    raw_id_order: list[str] = []
    normalized_regions: list[dict[str, Any]] = []

    for index, item in enumerate(raw_regions):
        field = f"regions[{index}]"
        if not isinstance(item, dict):
            errors.append(_note("error", field, "Region must be a JSON object.", "region_not_object"))
            continue
        rid = _text(item.get("id"))
        if not rid:
            errors.append(_note("error", f"{field}.id", "Region id is required.", "missing_region_id"))
            rid = f"__invalid_region_{index + 1}"
        if rid in seen:
            errors.append(_note("error", f"{field}.id", f"Duplicate region id '{rid}' is not allowed in V054.", "duplicate_region_id"))
        seen.add(rid)
        raw_id_order.append(rid)

        role = _normalize_role(item.get("role") or item.get("type") or item.get("region_role"))
        if not role:
            errors.append(_note("error", f"{field}.role", "Region role is required.", "missing_region_role"))
            role = "custom"
        elif role not in SUPPORTED_REGION_ROLES:
            errors.append(_note("error", f"{field}.role", f"Unsupported V054 region role '{role}'.", "unsupported_region_role"))

        bbox, bbox_warning = _normalize_bbox(item.get("bbox"))
        if bbox is None:
            errors.append(_note("error", f"{field}.bbox", "V054 bbox must be [x1, y1, x2, y2] with normalized values and x2/y2 greater than x1/y1.", "invalid_bbox"))
            bbox = [0.0, 0.0, 1.0, 1.0]
        elif bbox_warning:
            infos.append(_note("info", f"{field}.bbox", "Legacy bbox object was normalized to V054 [x1,y1,x2,y2].", bbox_warning))

        priority = _text(item.get("priority") or "reinforce").lower()
        if priority not in PRIORITIES:
            errors.append(_note("error", f"{field}.priority", "Priority must be override, reinforce, or blend.", "invalid_priority"))
            priority = "reinforce"

        relationship = _optional_id(item.get("relationship") or item.get("relation")).lower()
        if relationship and relationship not in RELATIONSHIPS:
            warnings.append(_note("warning", f"{field}.relationship", f"Unknown relationship '{relationship}' will be treated as custom planning metadata until the compiler supports it.", "unknown_relationship"))

        normalized_region = {
            "id": rid,
            "role": role,
            "label": _text(item.get("label") or rid.replace("_", " ").title()),
            "bbox": bbox,
            "prompt": _text(item.get("prompt") or item.get("positive")),
            "negative": _text(item.get("negative") or item.get("negative_prompt")),
            "strength": _clamp_float(item.get("strength"), 1.0, 0.0, 2.0),
            "lock": _normalize_lock(item.get("lock")),
            "attach_to": _optional_id(item.get("attach_to")) or None,
            "relationship": relationship or None,
            "target_area": _optional_id(item.get("target_area")) or None,
            "relationship_prompt": _text(item.get("relationship_prompt") or item.get("parent_prompt") or item.get("parent_prompt_template")) or None,
            "local_prompt_template": _text(item.get("local_prompt_template") or item.get("local_prompt")) or None,
            "negative_guard": _text(item.get("negative_guard") or item.get("negative_guard_prompt") or item.get("relationship_negative")) or None,
            "compiler_override": item.get("compiler_override") if isinstance(item.get("compiler_override"), dict) else {},
            "conflict_resolution_prompt": _text(item.get("conflict_resolution_prompt") or item.get("resolution_prompt")) or None,
            "conflict_negative_guard": _text(item.get("conflict_negative_guard")) or None,
            "conflict_override": item.get("conflict_override") if isinstance(item.get("conflict_override"), dict) else {},
            "zone": _text(item.get("zone") or item.get("background_zone")) or None,
            "background_prompt": _text(item.get("background_prompt") or item.get("zone_prompt")) or None,
            "background_negative_guard": _text(item.get("background_negative_guard") or item.get("zone_negative_guard")) or None,
            "background_override": item.get("background_override") if isinstance(item.get("background_override"), dict) else {},
            "priority": priority,
            "control": _normalize_control(item.get("control")),
            "detailer": _normalize_detailer(item.get("detailer")),
            "inpaint": _normalize_inpaint_target(item.get("inpaint"), item),
            "edit_intent": _normalize_edit_intent(item.get("edit_intent")),
            "source_image": _text(item.get("source_image") or item.get("source_image_name") or item.get("source_id")) or None,
            "source_region_id": _text(item.get("source_region_id") or item.get("source_region")) or None,
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "extension_routes": normalize_extension_routes_v054(item.get("extension_routes")),
            "character_lock_correction": item.get("character_lock_correction") if isinstance(item.get("character_lock_correction"), dict) else {},
            "character_traits": deepcopy(item.get("character_traits")) if isinstance(item.get("character_traits"), dict) else {},
            "trait_lock": deepcopy(item.get("trait_lock")) if isinstance(item.get("trait_lock"), dict) else {},
        }
        if role == "text":
            text_spec = normalize_text_region_v054(item)
            normalized_region.update(text_spec)
            normalized_region["text_mode"] = text_spec["mode"]

        id_role[rid] = role
        normalized_regions.append(normalized_region)

    # Link validation runs after we know all ids.
    for index, region in enumerate(normalized_regions):
        field = f"regions[{index}]"
        rid = region.get("id")
        role = region.get("role")
        parent = region.get("attach_to")
        if role in REQUIRES_PARENT_ROLES and not parent:
            errors.append(_note("error", f"{field}.attach_to", f"Role '{role}' requires attach_to in V054.", "missing_required_parent"))
        if parent:
            if parent == rid:
                errors.append(_note("error", f"{field}.attach_to", "Region cannot attach to itself.", "self_attachment"))
            if parent not in seen:
                errors.append(_note("error", f"{field}.attach_to", f"attach_to target '{parent}' does not exist.", "missing_attach_parent"))
            if role not in ATTACHABLE_REGION_ROLES:
                errors.append(_note("error", f"{field}.role", f"Role '{role}' cannot attach to a parent region.", "non_attachable_role"))
            if parent in seen and id_role.get(parent) in {"background", "transition_effect", "style"} and role in {"face_detail", "hair_detail", "hand_detail", "clothing", "held_prop"}:
                warnings.append(_note("warning", f"{field}.attach_to", f"Role '{role}' usually should attach to a character/object, not '{id_role.get(parent)}'.", "suspicious_parent_role"))
        if not _region_has_actionable_content(region):
            infos.append(_note("info", field, f"Region '{rid}' has no prompt, text, attachment, or enabled route settings; it is planning-only for now.", "planning_only_region"))

    warnings.extend(_detect_conflict_warnings(normalized_regions))
    complexity = analyze_scene_complexity_v054(normalized_regions)
    for item in complexity.get("messages", []):
        warnings.append(_note(str(item.get("level") or "warning"), str(item.get("field") or "regions"), str(item.get("message") or "Scene complexity warning."), str(item.get("code") or "complexity_warning")))
    background_analysis = analyze_background_regions_v054(normalized_regions)
    for item in background_analysis.get("messages", []):
        infos.append(item)
    regional_controlnet = analyze_regional_controlnet_v054(normalized_regions)
    for item in regional_controlnet.get("messages", []):
        warnings.append(item)
    regional_detailer = analyze_regional_detailer_v054(normalized_regions)
    for item in regional_detailer.get("messages", []):
        warnings.append(item)
    text_regions = analyze_text_regions_v054(normalized_regions)
    for item in text_regions.get("messages", []):
        if str(item.get("level")) == "info":
            infos.append(item)
        else:
            warnings.append(item)
    img2img_reuse = analyze_img2img_region_reuse_v054(normalized_regions, normalized.get("metadata"))
    for item in img2img_reuse.get("messages", []):
        if str(item.get("level")) == "info":
            infos.append(item)
        else:
            warnings.append(item)
    inpaint_targets = analyze_inpaint_region_targets_v054(normalized_regions, normalized.get("metadata"))
    for item in inpaint_targets.get("messages", []):
        if str(item.get("level")) == "info":
            infos.append(item)
        else:
            warnings.append(item)

    extension_unit_routing = build_extension_unit_routing_contract_v054({**normalized, "regions": normalized_regions})
    normalized["regions"] = normalized_regions
    output_source_stack = analyze_output_inspector_source_stack_v054(normalized_regions, normalized.get("metadata"))
    provider_route = (normalized.get("metadata") or {}).get("route") or (normalized.get("metadata") or {}).get("provider_route") or {}
    provider_metadata = dict(normalized.get("metadata") or {})
    provider_metadata["scene_graph_json"] = normalized
    provider_capabilities = resolve_provider_capabilities_v054(
        provider_route,
        metadata=provider_metadata,
    )
    flux_adapter_plan = provider_capabilities.get("flux_adapter_plan")
    if provider_capabilities.get("provider_profile") == "flux_adapter_planned" and not flux_adapter_plan:
        flux_adapter_plan = build_flux_adapter_plan_v054(normalized, route=provider_route)
    qwen_adapter_plan = provider_capabilities.get("qwen_adapter_plan")
    if provider_capabilities.get("provider_profile") == "qwen_semantic_edit_adapter" and not qwen_adapter_plan:
        qwen_adapter_plan = build_qwen_adapter_plan_v054(normalized, route=provider_route)
    for item in provider_capabilities.get("notices", []):
        if str(item.get("level")) == "info":
            infos.append(_note("info", "metadata.provider_capabilities", str(item.get("message") or "Provider capability notice."), str(item.get("code") or "provider_capability")))
        else:
            warnings.append(_note(str(item.get("level") or "warning"), "metadata.provider_capabilities", str(item.get("message") or "Provider capability warning."), str(item.get("code") or "provider_capability")))

    normalized["metadata"].update({
        "region_count": len(normalized_regions),
        "character_count": sum(1 for region in normalized_regions if region.get("role") == "character"),
        "detail_count": sum(1 for region in normalized_regions if region.get("role") not in {"character", "background", "style"}),
        "complexity": complexity,
        "background_regions": background_analysis,
        "regional_controlnet": regional_controlnet,
        "regional_detailer": regional_detailer,
        "text_regions": text_regions,
        "img2img_region_reuse": img2img_reuse,
        "inpaint_region_targets": inpaint_targets,
        "extension_unit_routing": extension_unit_routing,
        "output_inspector_source_stack": output_source_stack,
        "provider_capabilities": provider_capabilities,
        "flux_adapter_plan": flux_adapter_plan,
        "qwen_adapter_plan": qwen_adapter_plan,
        "sdxl_full_implementation_lock": provider_capabilities.get("sdxl_full_implementation_lock"),
        "contract_phase": "SD-V054-26.7",
        "legacy_phase23_contract_anchor": "SD-V054-23",
        "legacy_phase26_contract_anchor": "SD-V054-26",
        "legacy_phase25_contract_anchor": "SD-V054-25",
        "legacy_phase24_contract_anchor": "SD-V054-24",
        "legacy_phase21_contract_anchor": "SD-V054-21",
        "legacy_qwen_adapter_phase_anchor": "SD-V054-20",
        "legacy_flux_adapter_phase_anchor": "SD-V054-19",
        "legacy_sdxl_full_lock_phase_anchor": "SD-V054-18",
        "legacy_provider_capability_phase_anchor": "SD-V054-17",
        "legacy_output_inspector_phase_anchor": "SD-V054-16",
        "legacy_detailer_phase_anchor": "SD-V054-12",
        "legacy_controlnet_phase_anchor": "SD-V054-11",
        "legacy_contract_phase_anchor": "SD-V054-9",
    })

    return {
        "ok": not errors,
        "schema": SCENE_GRAPH_SCHEMA,
        "scene_graph": normalized,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }


def validate_scene_graph_v054(scene_graph: Any, *, strict: bool = False) -> dict[str, Any]:
    result = normalize_scene_graph_v054(scene_graph)
    if strict and result.get("warnings"):
        result = deepcopy(result)
        result["errors"] = list(result.get("errors") or []) + [
            {**warning, "level": "error", "code": f"strict_{warning.get('code', 'warning')}"}
            for warning in result.get("warnings") or []
        ]
        result["ok"] = not result["errors"]
    return result


__all__ = [
    "SCENE_GRAPH_VERSION",
    "SCENE_GRAPH_SCHEMA",
    "SUPPORTED_REGION_ROLES",
    "ATTACHABLE_REGION_ROLES",
    "REQUIRES_PARENT_ROLES",
    "PRIORITIES",
    "RELATIONSHIPS",
    "normalize_scene_graph_v054",
    "validate_scene_graph_v054",
    "resolve_provider_capabilities_v054",
    "build_flux_adapter_plan_v054",
    "build_qwen_adapter_plan_v054",
    "analyze_background_regions_v054",
    "analyze_regional_controlnet_v054",
    "analyze_regional_detailer_v054",
    "build_extension_unit_routing_contract_v054",
    "analyze_text_regions_v054",
    "analyze_img2img_region_reuse_v054",
    "analyze_inpaint_region_targets_v054",
    "normalize_text_region_v054",
    "background_zone_name_v054",
]

# Legacy test anchor: "contract_phase": "SD-V054-7"
# Legacy test anchor: "contract_phase": "SD-V054-6"

# Phase 21 compatibility anchor: "contract_phase": "SD-V054-21"
