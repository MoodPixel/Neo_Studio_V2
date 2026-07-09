from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any, Final

from neo_app.video.parameter_profiles import normalize_video_vram_profile, video_parameter_profile_payload
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.vram_engine.v10"

PROFILE_POLICY: Final[dict[str, dict[str, Any]]] = {
    "low": {
        "label": "Low VRAM Draft",
        "target_vram_gb": "8-12",
        "intent": "safe short preview clips first; upscale/interpolate later",
        "decode_mode": "tiled",
        "tile_size": 384,
        "temporal_tile_size": 4096,
        "batch_count": 1,
        "allow_quality_raise": False,
    },
    "balanced": {
        "label": "Balanced",
        "target_vram_gb": "12",
        "intent": "default stable working mode for WAN 5B and LTX tests",
        "decode_mode": "tiled",
        "tile_size": 512,
        "temporal_tile_size": 4096,
        "batch_count": 1,
        "allow_quality_raise": False,
    },
    "quality": {
        "label": "Quality",
        "target_vram_gb": "16+",
        "intent": "slower quality tests with larger frame/resolution budgets",
        "decode_mode": "tiled",
        "tile_size": 512,
        "temporal_tile_size": 4096,
        "batch_count": 1,
        "allow_quality_raise": True,
    },
    "manual": {
        "label": "Manual / Experimental",
        "target_vram_gb": "advanced",
        "intent": "expert overrides with hard warnings before queueing",
        "decode_mode": "tiled",
        "tile_size": 512,
        "temporal_tile_size": 4096,
        "batch_count": 1,
        "allow_quality_raise": True,
    },
}

NUMERIC_FIELDS: Final[tuple[str, ...]] = ("width", "height", "frames", "fps", "steps", "guidance", "batch_count", "tile_size", "temporal_tile_size", "chunk_feed_forward", "image_strength", "transition_strength", "first_strength", "last_strength", "control_strength", "motion_strength")


def _to_int(value: Any, fallback: int) -> int:
    try:
        if value is None or value == "":
            return int(fallback)
        return int(float(value))
    except (TypeError, ValueError):
        return int(fallback)


def _to_float(value: Any, fallback: float) -> float:
    try:
        if value is None or value == "":
            return float(fallback)
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _incoming_has_value(values: dict[str, Any], key: str) -> bool:
    return key in values and values.get(key) is not None and values.get(key) != ""

def _multiple_of_16(value: int) -> int:
    return max(256, int(value // 16) * 16)


def _profile_side_limits(constraints: dict[str, Any]) -> tuple[int, int]:
    """Return orientation-neutral short/long side caps for a VRAM profile.

    Older V-G10 logic treated max_width/max_height as literal axes. That silently
    crushed vertical WAN requests such as 544x960 into 544x704 when Quality was
    selected. For video, those caps are really profile buckets: 1280x704 means
    the long side may be 1280 and the short side may be 704, regardless of
    landscape/portrait orientation.
    """
    max_width = _to_int(constraints.get("max_width"), 2048)
    max_height = _to_int(constraints.get("max_height"), 1152)
    max_long = _to_int(constraints.get("max_long_side"), max(max_width, max_height))
    max_short = _to_int(constraints.get("max_short_side"), min(max_width, max_height))
    return max_short, max_long


def _fit_dimensions_preserving_aspect(width: int, height: int, constraints: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    requested_width = int(width)
    requested_height = int(height)
    max_short, max_long = _profile_side_limits(constraints)
    short_side = min(requested_width, requested_height)
    long_side = max(requested_width, requested_height)
    scale = 1.0
    reasons: list[str] = []
    if short_side > max_short:
        scale = min(scale, max_short / max(1, short_side))
        reasons.append("short_side_cap")
    if long_side > max_long:
        scale = min(scale, max_long / max(1, long_side))
        reasons.append("long_side_cap")

    effective_width = requested_width
    effective_height = requested_height
    if scale < 1.0:
        effective_width = _multiple_of_16(int(requested_width * scale))
        effective_height = _multiple_of_16(int(requested_height * scale))

    effective_width = max(256, effective_width)
    effective_height = max(256, effective_height)
    orientation = "portrait" if requested_height > requested_width else ("landscape" if requested_width > requested_height else "square")
    info = {
        "schema_version": "neo.video.resolution_normalization.vg13_12",
        "requested_width": requested_width,
        "requested_height": requested_height,
        "effective_width": effective_width,
        "effective_height": effective_height,
        "requested_orientation": orientation,
        "max_short_side": max_short,
        "max_long_side": max_long,
        "aspect_preserved": (requested_width * effective_height == requested_height * effective_width) if scale >= 1.0 else abs((requested_width / max(1, requested_height)) - (effective_width / max(1, effective_height))) < 0.02,
        "scale": round(scale, 4),
        "changed": effective_width != requested_width or effective_height != requested_height,
        "reason": " + ".join(reasons) if reasons else "within_profile_bucket",
    }
    return effective_width, effective_height, info


def _clamp_number(value: float | int, *, minimum: float | int | None = None, maximum: float | int | None = None) -> float | int:
    result = value
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _risk_score(params: dict[str, Any], family: str, loader: str, profile_id: str) -> dict[str, Any]:
    width = _to_int(params.get("width"), 832)
    height = _to_int(params.get("height"), 480)
    frames = _to_int(params.get("frames"), 41)
    steps = _to_int(params.get("steps"), 20)
    pixels = width * height
    work_units = pixels * frames * max(1, steps)
    # Practical relative score. This is not pretending to be exact VRAM math; it is a safety heuristic.
    multiplier = 1.0
    if family == "ltx23":
        multiplier += 0.35
    if loader == "gguf":
        multiplier -= 0.12
    if profile_id == "manual":
        multiplier += 0.15
    relative = work_units / (832 * 480 * 41 * 20) * multiplier
    if relative <= 1.1:
        tier = "safe"
    elif relative <= 2.2:
        tier = "moderate"
    elif relative <= 4.0:
        tier = "heavy"
    else:
        tier = "danger"
    return {"tier": tier, "relative_work_units": round(relative, 2), "pixels_per_frame": pixels, "estimated_frames": frames, "estimated_steps": steps}


def apply_video_vram_engine(
    values: dict[str, Any] | None = None,
    *,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    vram_profile: str | None = None,
) -> dict[str, Any]:
    """Normalize and guard Video compiler parameters before a workflow is built.

    The engine is intentionally conservative: it clamps the current request to the selected
    profile, keeps batch count at 1, defaults decode to tiled for low/mid VRAM, and returns
    warnings instead of silently pretending the request is cheap.
    """
    incoming = deepcopy(values or {})
    nf = normalize_video_family(family or incoming.get("family"))
    nl = normalize_video_loader(loader or incoming.get("loader"))
    nt = normalize_video_generation_type(generation_type or incoming.get("generation_type") or incoming.get("mode"))
    vp_id = normalize_video_vram_profile(vram_profile or incoming.get("vram_profile"))
    route = find_video_route(nf, nl, nt, include_planned=True)
    parameter_profile = video_parameter_profile_payload(family=nf, loader=nl, generation_type=nt, vram_profile=vp_id)
    defaults = dict(parameter_profile.get("defaults") or {})
    constraints = dict(parameter_profile.get("vram_profile", {}).get("constraints") or {})
    policy = PROFILE_POLICY.get(vp_id, PROFILE_POLICY["balanced"])

    preserve_user_overrides = _as_bool(incoming.get("preserve_user_overrides"), False)
    supplied: dict[str, Any] = {**defaults, **{k: v for k, v in incoming.items() if v is not None and v != ""}}
    normalized: dict[str, Any] = dict(supplied)
    changes: list[dict[str, Any]] = []

    def set_clamped(key: str, value: Any, reason: str) -> None:
        old = normalized.get(key)
        normalized[key] = value
        if old != value:
            changes.append({"field": key, "from": old, "to": value, "reason": reason})

    requested_width = _multiple_of_16(_to_int(normalized.get("width"), defaults.get("width", 832)))
    requested_height = _multiple_of_16(_to_int(normalized.get("height"), defaults.get("height", 480)))
    user_size_override = preserve_user_overrides and (_incoming_has_value(incoming, "width") or _incoming_has_value(incoming, "height"))
    if user_size_override:
        width = int(_clamp_number(requested_width, minimum=256, maximum=2048))
        height = int(_clamp_number(requested_height, minimum=256, maximum=1152))
        orientation = "portrait" if requested_height > requested_width else ("landscape" if requested_width > requested_height else "square")
        resolution_normalization = {
            "schema_version": "neo.video.resolution_normalization.vg13_10m",
            "requested_width": requested_width,
            "requested_height": requested_height,
            "effective_width": width,
            "effective_height": height,
            "requested_orientation": orientation,
            "max_short_side": _profile_side_limits(constraints)[0],
            "max_long_side": _profile_side_limits(constraints)[1],
            "aspect_preserved": True,
            "scale": 1.0,
            "changed": width != requested_width or height != requested_height,
            "reason": "user_override_preserved_with_hard_absolute_guard",
        }
    else:
        width, height, resolution_normalization = _fit_dimensions_preserving_aspect(requested_width, requested_height, constraints)
    set_clamped("width", width, "user override preserved" if user_size_override else "profile bucket + multiple-of-16 safety with aspect preservation")
    set_clamped("height", height, "user override preserved" if user_size_override else "profile bucket + multiple-of-16 safety with aspect preservation")
    normalized["resolution_normalization"] = resolution_normalization

    frames = _to_int(normalized.get("frames"), defaults.get("frames", 41))
    steps = _to_int(normalized.get("steps"), defaults.get("steps", 20))
    frame_max = 241 if preserve_user_overrides and _incoming_has_value(incoming, "frames") else constraints.get("max_frames", 241)
    step_max = 80 if preserve_user_overrides and _incoming_has_value(incoming, "steps") else constraints.get("max_steps", 80)
    frames = int(_clamp_number(frames, minimum=1, maximum=frame_max))
    steps = int(_clamp_number(steps, minimum=1, maximum=step_max))
    set_clamped("frames", frames, "user frame override preserved" if frame_max == 241 else "profile max frame budget")
    set_clamped("steps", steps, "user step override preserved" if step_max == 80 else "profile max step budget")

    fps = _to_float(normalized.get("fps"), defaults.get("fps", 16))
    fps = float(_clamp_number(fps, minimum=1, maximum=60))
    if vp_id == "low" and not (preserve_user_overrides and _incoming_has_value(incoming, "fps")):
        fps = min(fps, 12.0)
    set_clamped("fps", int(fps) if fps.is_integer() else fps, "user fps override preserved" if preserve_user_overrides and _incoming_has_value(incoming, "fps") else "profile fps budget")

    set_clamped("batch_count", 1, "video batching is locked until a dedicated queue-batch phase")
    set_clamped("decode_mode", str(normalized.get("decode_mode") or policy["decode_mode"]), "profile decode policy")
    if not (preserve_user_overrides and _incoming_has_value(incoming, "decode_mode")) and (normalized.get("decode_mode") != "standard" or vp_id in {"low", "balanced", "quality", "manual"}):
        set_clamped("decode_mode", policy["decode_mode"], "tiled decode is the safe default for video")
    set_clamped("tile_size", _to_int(normalized.get("tile_size"), policy["tile_size"]), "user tile override preserved" if preserve_user_overrides and _incoming_has_value(incoming, "tile_size") else "profile tile policy")
    if vp_id == "low" and not (preserve_user_overrides and _incoming_has_value(incoming, "tile_size")):
        set_clamped("tile_size", min(_to_int(normalized.get("tile_size"), 384), 384), "low VRAM tile cap")
    set_clamped("temporal_tile_size", _to_int(normalized.get("temporal_tile_size"), policy["temporal_tile_size"]), "user temporal tile override preserved" if preserve_user_overrides and _incoming_has_value(incoming, "temporal_tile_size") else "profile temporal tile policy")

    if nf == "ltx23":
        set_clamped("chunk_feed_forward", int(_clamp_number(_to_int(normalized.get("chunk_feed_forward"), defaults.get("chunk_feed_forward", 2)), minimum=1, maximum=8)), "LTX chunk feed-forward guard")
        set_clamped("tiled_vae_decode", bool(normalized.get("tiled_vae_decode", True)), "LTX VAE decode guard")
        set_clamped("fps_sync", bool(normalized.get("fps_sync", True)), "LTX int/float FPS sync")

    normalized["guidance"] = _to_float(normalized.get("guidance"), defaults.get("guidance", 5 if nf == "wan22" else 1))
    normalized["seed"] = _to_int(normalized.get("seed"), -1)
    normalized["sampler"] = str(normalized.get("sampler") or defaults.get("sampler") or ("euler_ancestral" if nf == "ltx23" else "uni_pc"))
    normalized["scheduler"] = str(normalized.get("scheduler") or defaults.get("scheduler") or ("ltxv" if nf == "ltx23" else "simple"))
    normalized["vram_profile"] = vp_id

    risk = _risk_score(normalized, nf, nl, vp_id)
    warnings: list[str] = []
    if changes:
        warnings.append("VRAM profile adjusted one or more requested values before compile.")
    if normalized.get("resolution_normalization", {}).get("changed"):
        warnings.append("Resolution was scaled inside the selected profile bucket while preserving aspect ratio.")
    if risk["tier"] in {"heavy", "danger"}:
        warnings.append("Selected settings are heavy for the chosen profile; reduce frames/resolution/steps or use a post upscale lane.")
    if nt == "img2vid" and not normalized.get("source_image"):
        warnings.append("Img2Vid routes require a source image before queueing.")
    if nt == "first_last_frame" and not ((normalized.get("first_image") or normalized.get("source_image")) and normalized.get("last_image")):
        warnings.append("First/Last Frame routes require both first_image and last_image before queueing.")
    if vp_id == "manual":
        warnings.append("Manual / Experimental profile keeps hard guards but may still crash low-VRAM systems.")
    if nf == "ltx23" and nl == "unet" and vp_id == "low":
        warnings.append("For low VRAM LTX, GGUF is usually the safer first choice than full UNET/diffusion safetensors.")

    recommendations = []
    if vp_id == "low":
        recommendations.extend(["Keep clips short.", "Generate draft at lower resolution, then use upscale/interpolation later."])
    if risk["tier"] in {"heavy", "danger"}:
        recommendations.extend(["Drop frames first, then resolution, then steps.", "Do not raise batch count for video generation."])

    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V10",
        "route_id": route.route_id if route else "",
        "route": route.payload() if route else None,
        "request": {"family": nf, "loader": nl, "generation_type": nt, "vram_profile": vp_id},
        "profile_policy": policy,
        "parameter_profile": parameter_profile,
        "normalized_parameters": normalized,
        "changes": changes,
        "risk": risk,
        "warnings": warnings,
        "recommendations": recommendations,
        "compile_policies": {
            "batch_count_locked": True,
            "prefer_tiled_decode": normalized.get("decode_mode") == "tiled",
            "force_multiple_of_16_size": True,
            "preflight_before_queue": True,
            "post_lanes_preferred_for_upscale_and_interpolation": True,
            "aspect_preserving_profile_buckets": True,
            "preserve_user_overrides": preserve_user_overrides,
        },
    }


def video_vram_engine_payload(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    mode: str | None = None,
    vram_profile: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    incoming = {k: v for k, v in values.items() if v is not None}
    return apply_video_vram_engine(incoming, family=family, loader=loader, generation_type=generation_type or mode, vram_profile=vram_profile)


def video_vram_preflight_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    return apply_video_vram_engine(data, family=data.get("family"), loader=data.get("loader"), generation_type=data.get("generation_type") or data.get("mode"), vram_profile=data.get("vram_profile"))
