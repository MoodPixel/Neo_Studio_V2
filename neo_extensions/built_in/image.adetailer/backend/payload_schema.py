from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from .constants import (
    CFG_SAFETY_CAP,
    DEFAULT_PARAMS,
    BOOLEAN_PARAMS,
    DETAILER_PASS_KEYS,
    DETAILER_PASS_MODES,
    DETAILER_PASS_REFERENCE_LOCKS,
    DETAILER_PASS_TARGET_MODES,
    DETAILER_PASS_INTEGER_KEYS,
    DETECTOR_TYPES,
    EXTENSION_ID,
    INTEGER_PARAMS,
    NUMERIC_LIMITS,
    MAX_DETAILER_PASSES,
    PHASE,
    RUNTIME_PARAMS,
    STRING_PARAMS,
    TARGET_ORDERS,
    TARGET_SPLIT_MODES,
    VERSION,
)

BOX_RE = re.compile(r"[-+]?\d*\.?\d+")


def default_payload_block() -> dict[str, Any]:
    return {
        "enabled": False,
        "version": VERSION,
        "inputs": {},
        "params": deepcopy(DEFAULT_PARAMS),
        "assets": {},
        "metadata": {
            "phase": PHASE,
            "payload_runtime_ready": True,
            "workflow_patch_ready": True,
        },
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def _as_number(value: Any, *, default: Any, integer: bool = False) -> tuple[Any, str | None]:
    if value is None or value == "":
        return default, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default, "invalid_number"
    if integer:
        number = int(round(number))
    return number, None


def _clamp(key: str, value: Any) -> tuple[Any, str | None]:
    lo, hi = NUMERIC_LIMITS[key]
    original = value
    if value is None and key == "cfg":
        return None, None
    if value < lo:
        value = lo
    if value > hi:
        value = hi
    if key == "cfg" and value is not None and value > CFG_SAFETY_CAP:
        value = CFG_SAFETY_CAP
        return value, "cfg_safety_capped"
    return value, "clamped" if value != original else None


def parse_sep_targets(text: Any) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    return [part.strip() for part in text.split("[SEP]") if part.strip()]


def parse_custom_classes(text: Any) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    return [part.strip() for part in re.split(r"[,\n]+", text) if part.strip()]


def parse_manual_boxes(value: Any) -> tuple[list[dict[str, float]], list[str]]:
    """Parse V1-compatible manual box text without requiring the visual drawer.

    Accepted forms:
    - JSON array: [{"x":0,"y":0,"w":100,"h":100}, ...]
    - line text: x,y,w,h[,label]
    - semicolon/newline separated numeric groups
    """
    warnings: list[str] = []
    boxes: list[dict[str, float]] = []
    if value in (None, ""):
        return boxes, warnings

    if isinstance(value, list):
        candidates = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return boxes, warnings
        try:
            decoded = json.loads(stripped)
            candidates = decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            candidates = []
            for line in re.split(r"[;\n]+", stripped):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{") or line.startswith("["):
                    try:
                        decoded_line = json.loads(line)
                        if isinstance(decoded_line, list):
                            candidates.extend(decoded_line)
                        else:
                            candidates.append(decoded_line)
                        continue
                    except json.JSONDecodeError:
                        pass
                candidates.append(line)
    else:
        warnings.append("manual_boxes_ignored_invalid_type")
        return boxes, warnings

    for idx, item in enumerate(candidates):
        label = ""
        if isinstance(item, dict):
            keys = ("x", "y", "w", "h")
            if all(k in item for k in keys):
                nums = [item.get(k) for k in keys]
                label = str(item.get("label") or item.get("target") or "")
            elif all(k in item for k in ("x1", "y1", "x2", "y2")):
                x1, y1, x2, y2 = [float(item.get(k) or 0) for k in ("x1", "y1", "x2", "y2")]
                nums = [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]
                label = str(item.get("label") or item.get("target") or "")
            else:
                warnings.append(f"manual_box_{idx}_ignored_missing_coordinates")
                continue
        else:
            text = str(item)
            numbers = BOX_RE.findall(text)
            if len(numbers) < 4:
                warnings.append(f"manual_box_{idx}_ignored_needs_four_numbers")
                continue
            nums = numbers[:4]
            tail = BOX_RE.sub("", text).strip(" ,:|[](){}")
            label = tail or ""
        try:
            x, y, w, h = [float(n) for n in nums]
        except (TypeError, ValueError):
            warnings.append(f"manual_box_{idx}_ignored_invalid_number")
            continue
        if w <= 0 or h <= 0:
            warnings.append(f"manual_box_{idx}_ignored_non_positive_size")
            continue
        boxes.append({"x": x, "y": y, "w": w, "h": h, "label": label})
    return boxes, warnings


def _extract_block(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if EXTENSION_ID in payload and isinstance(payload[EXTENSION_ID], dict):
        return payload[EXTENSION_ID]
    for container_key in ("extensions", "payloads"):
        container = payload.get(container_key)
        if isinstance(container, dict) and isinstance(container.get(EXTENSION_ID), dict):
            return container[EXTENSION_ID]
    return None



def _pass_id(value: Any, index: int) -> str:
    text = str(value or "").strip()
    if not text:
        return "primary" if index == 0 else f"pass-{index + 1}"
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", text)[:80] or ("primary" if index == 0 else f"pass-{index + 1}")


def detailer_pass_from_flat(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a V1-compatible primary pass from legacy flat fields.

    Phase F2 makes detailer_passes[] the runtime contract, but V2's Phase I
    workflow patch still consumes mirrored flat primary fields. This helper keeps
    both worlds aligned without dropping old saves/replay payloads.
    """
    src = params if isinstance(params, dict) else {}
    return {
        "id": "primary",
        "label": "Primary pass",
        "enabled": _as_bool(src.get("enabled", True)),
        "mode": src.get("mode") or "face",
        "detector_type": src.get("detector_type") or "bbox",
        "detector_model": src.get("detector_model") or "",
        "target_order": src.get("target_order") or "auto",
        "start_index": src.get("start_index", 1),
        "count": src.get("count", src.get("top_k", 1)),
        "min_area": src.get("min_area", 0),
        "max_area": src.get("max_area", 0),
        "target_mode": src.get("target_mode") or ("manual_boxes" if src.get("manual_boxes") else "auto_detect"),
        "manual_boxes": src.get("manual_boxes") or "",
        "reference_lock": src.get("reference_lock") or "none",
        "positive_prompt": src.get("positive_prompt") or "",
        "negative_prompt": src.get("negative_prompt") or "",
    }


def normalize_detailer_pass(item: Any, index: int = 0) -> dict[str, Any]:
    src = item if isinstance(item, dict) else {}
    default = {
        "id": "primary" if index == 0 else f"pass-{index + 1}",
        "label": "Primary pass" if index == 0 else f"Pass {index + 1}",
        "enabled": True,
        "mode": "face",
        "detector_type": "bbox",
        "detector_model": "",
        "target_order": "auto",
        "start_index": 1,
        "count": 1,
        "min_area": 0,
        "max_area": 0,
        "target_mode": "auto_detect",
        "manual_boxes": "",
        "reference_lock": "none",
        "positive_prompt": "",
        "negative_prompt": "",
    }
    clean = dict(default)
    clean["id"] = _pass_id(src.get("id"), index)
    clean["label"] = str(src.get("label") or default["label"]).strip()[:80]
    for key, value in src.items():
        if key not in DETAILER_PASS_KEYS:
            continue
        if key == "id":
            clean[key] = _pass_id(value, index)
        elif key == "label":
            clean[key] = str(value or default[key]).strip()[:80]
        elif key == "enabled":
            clean[key] = _as_bool(value)
        elif key in DETAILER_PASS_INTEGER_KEYS:
            number, _issue = _as_number(value, default=default[key], integer=True)
            lo, hi = NUMERIC_LIMITS[key]
            clean[key] = max(lo, min(hi, int(number)))
        elif key == "detector_type":
            text = str(value).strip().lower()
            clean[key] = text if text in DETECTOR_TYPES else default[key]
        elif key == "target_order":
            text = str(value).strip().lower()
            clean[key] = text if text in TARGET_ORDERS else default[key]
        elif key == "target_mode":
            text = str(value).strip().lower()
            clean[key] = text if text in DETAILER_PASS_TARGET_MODES else default[key]
        elif key == "mode":
            text = str(value).strip().lower()
            clean[key] = text if text in DETAILER_PASS_MODES else default[key]
        elif key == "reference_lock":
            text = str(value).strip().lower()
            clean[key] = text if text in DETAILER_PASS_REFERENCE_LOCKS else default[key]
        else:
            clean[key] = str(value).strip() if value is not None else ""
    if clean["manual_boxes"] and clean["target_mode"] != "manual_boxes":
        clean["target_mode"] = "manual_boxes"
    if clean["count"] == 0 and clean["target_mode"] == "auto_detect":
        # V1 uses count=0 as "all targets"; keep that valid.
        clean["count"] = 0
    return clean


def derive_detailer_pass(pass_data: dict[str, Any], index: int) -> dict[str, Any]:
    manual_boxes, box_warnings = parse_manual_boxes(pass_data.get("manual_boxes"))
    positive_targets = parse_sep_targets(pass_data.get("positive_prompt"))
    negative_targets = parse_sep_targets(pass_data.get("negative_prompt"))
    return {
        "index": index,
        "id": pass_data.get("id") or ("primary" if index == 0 else f"pass-{index + 1}"),
        "label": pass_data.get("label") or ("Primary pass" if index == 0 else f"Pass {index + 1}"),
        "enabled": bool(pass_data.get("enabled", True)),
        "mode": pass_data.get("mode") or "face",
        "detector_type": pass_data.get("detector_type") or "bbox",
        "detector_model": pass_data.get("detector_model") or "",
        "target_mode": pass_data.get("target_mode") or "auto_detect",
        "reference_lock": pass_data.get("reference_lock") or "none",
        "manual_boxes_parsed": manual_boxes,
        "manual_box_count": len(manual_boxes),
        "positive_targets": positive_targets,
        "negative_targets": negative_targets,
        "sep_target_count": max(len(positive_targets), len(negative_targets)),
        "warnings": box_warnings,
    }


def _normalize_detailer_passes(value: Any, source: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    raw_passes: list[Any]
    if isinstance(value, list):
        raw_passes = value
    elif value in (None, "", []):
        raw_passes = [detailer_pass_from_flat(source)]
    else:
        warnings.append("detailer_passes_ignored_invalid_type")
        raw_passes = [detailer_pass_from_flat(source)]
    if len(raw_passes) > MAX_DETAILER_PASSES:
        warnings.append(f"detailer_passes_truncated_to_{MAX_DETAILER_PASSES}")
        raw_passes = raw_passes[:MAX_DETAILER_PASSES]
    return [normalize_detailer_pass(item, idx) for idx, item in enumerate(raw_passes)]

def normalize_params(params: dict[str, Any] | None = None, *, enabled: bool | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    source = params if isinstance(params, dict) else {}
    normalized = deepcopy(DEFAULT_PARAMS)
    warnings: list[str] = []
    ignored: list[str] = []
    clamped: list[str] = []

    for key, value in source.items():
        if key not in RUNTIME_PARAMS:
            ignored.append(key)
            continue
        if key == "detailer_passes":
            # Normalize after scalar/shared params are processed so legacy flat
            # fields can safely seed the primary pass when needed.
            continue
        if key == "enabled":
            normalized[key] = _as_bool(value)
        elif key in BOOLEAN_PARAMS:
            normalized[key] = _as_bool(value)
        elif key in NUMERIC_LIMITS:
            number, issue = _as_number(value, default=DEFAULT_PARAMS[key], integer=key in INTEGER_PARAMS)
            if issue:
                warnings.append(f"{key}_{issue}")
            number, clamp_issue = _clamp(key, number)
            if clamp_issue:
                clamped.append(key if clamp_issue == "clamped" else f"{key}:{clamp_issue}")
            normalized[key] = number
        elif key in STRING_PARAMS:
            normalized[key] = str(value).strip() if value is not None else ""
        elif key == "detector_type":
            text = str(value).strip().lower()
            if text not in DETECTOR_TYPES:
                warnings.append("detector_type_reset_to_default")
                text = DEFAULT_PARAMS[key]
            normalized[key] = text
        elif key == "target_order":
            text = str(value).strip().lower()
            if text not in TARGET_ORDERS:
                warnings.append("target_order_reset_to_default")
                text = DEFAULT_PARAMS[key]
            normalized[key] = text
        elif key == "target_split_mode":
            text = str(value).strip().lower()
            if text not in TARGET_SPLIT_MODES:
                warnings.append("target_split_mode_reset_to_default")
                text = DEFAULT_PARAMS[key]
            normalized[key] = text

    if enabled is not None:
        normalized["enabled"] = bool(enabled)

    normalized["detailer_passes"] = _normalize_detailer_passes(source.get("detailer_passes"), normalized, warnings)
    detailer_passes = normalized["detailer_passes"]
    if (
        normalized.get("enabled")
        and len(detailer_passes) == 1
        and str(detailer_passes[0].get("id") or "").lower() == "primary"
        and not detailer_passes[0].get("enabled", True)
    ):
        # Primary pass is the required/staged unit whenever the top-level
        # ADetailer toggle is enabled and no additional enabled pass exists.
        # Older UI builds could serialize the protected primary card as
        # enabled=false, causing the whole extension to validate but never
        # mutate the workflow. Repair that single-primary stale state here
        # while preserving the explicit multi-pass case where users disable
        # primary and run a later card instead.
        detailer_passes[0]["enabled"] = True
        warnings.append("primary_pass_reenabled_for_requested_run")
    active_passes = [p for p in detailer_passes if p.get("enabled", True)]
    primary_runtime_pass = active_passes[0] if active_passes else (detailer_passes[0] if detailer_passes else detailer_pass_from_flat(normalized))

    # Keep flat primary fields as a compatibility mirror for Phase I's single-pass
    # graph patch. F2's first-class runtime contract is detailer_passes[].
    for flat_key, pass_key in (
        ("detector_model", "detector_model"),
        ("detector_type", "detector_type"),
        ("mode", "mode"),
        ("target_order", "target_order"),
        ("start_index", "start_index"),
        ("count", "count"),
        ("min_area", "min_area"),
        ("max_area", "max_area"),
        ("target_mode", "target_mode"),
        ("manual_boxes", "manual_boxes"),
        ("reference_lock", "reference_lock"),
        ("positive_prompt", "positive_prompt"),
        ("negative_prompt", "negative_prompt"),
    ):
        normalized[flat_key] = primary_runtime_pass.get(pass_key, normalized.get(flat_key))

    positive_targets = parse_sep_targets(normalized.get("positive_prompt"))
    negative_targets = parse_sep_targets(normalized.get("negative_prompt"))
    custom_classes = parse_custom_classes(normalized.get("custom_classes"))
    manual_boxes, box_warnings = parse_manual_boxes(normalized.get("manual_boxes"))
    warnings.extend(box_warnings)

    pass_derived = [derive_detailer_pass(p, idx) for idx, p in enumerate(detailer_passes)]
    pass_warnings: list[str] = []
    for item in pass_derived:
        for warning in item.get("warnings") or []:
            pass_warnings.append(f"pass_{item.get('index')}_{warning}")
    warnings.extend(pass_warnings)

    all_manual_boxes: list[dict[str, float]] = []
    for item in pass_derived:
        all_manual_boxes.extend(item.get("manual_boxes_parsed") or [])
    max_sep_targets = max([item.get("sep_target_count", 0) for item in pass_derived] + [max(len(positive_targets), len(negative_targets))])

    derived = {
        "ignored_params": sorted(ignored),
        "warnings": warnings,
        "clamped_params": clamped,
        "positive_targets": positive_targets,
        "negative_targets": negative_targets,
        "custom_classes_list": custom_classes,
        "manual_boxes_parsed": manual_boxes,
        "manual_box_count": len(manual_boxes),
        "all_manual_boxes_parsed": all_manual_boxes,
        "all_manual_box_count": len(all_manual_boxes),
        "sep_target_count": max_sep_targets,
        "uses_sam": bool(normalized.get("sam_model")),
        "uses_manual_boxes": bool(all_manual_boxes),
        "detailer_passes": pass_derived,
        "detailer_pass_count": len(detailer_passes),
        "enabled_detailer_pass_count": len(active_passes),
        "primary_runtime_pass_id": primary_runtime_pass.get("id"),
        "primary_runtime_pass_index": detailer_passes.index(primary_runtime_pass) if primary_runtime_pass in detailer_passes else 0,
        "multi_pass_payload_ready": True,
        "multi_pass_ui_ready": True,
    }
    return normalized, derived

def normalize_block(payload: Any) -> dict[str, Any]:
    clean = default_payload_block()
    block = _extract_block(payload)
    if not isinstance(block, dict):
        clean["params"], derived = normalize_params({}, enabled=False)
        clean["metadata"].update({"requested_enabled": False, "normalization": derived})
        return clean

    raw_inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    raw_params = block.get("params") if isinstance(block.get("params"), dict) else {}
    raw_assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    raw_metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    requested_enabled = _as_bool(block.get("enabled") or raw_params.get("enabled") or raw_inputs.get("enabled"))
    params, derived = normalize_params(raw_params, enabled=requested_enabled)
    if raw_params.get("detailer_output_pass") or raw_metadata.get("detailer_output_pass"):
        params["detailer_output_pass"] = True

    clean["enabled"] = requested_enabled
    clean["params"] = params
    clean["inputs"] = {k: v for k, v in raw_inputs.items() if k in {"enabled", "source_image", "source_output_id", "detection_snapshot", "preview_action_source"}}
    clean["assets"] = {k: str(v).strip() for k, v in raw_assets.items() if k in {"detector_model_path", "sam_model_path", "source_image_ref"} and v}
    clean["metadata"].update({
        "requested_enabled": requested_enabled,
        "normalization": derived,
        "stale_hidden_fields_removed": bool(derived["ignored_params"]),
        "source_mode": raw_metadata.get("source_mode") or raw_params.get("source_mode") or "standard",
        "preview_action_source": raw_metadata.get("preview_action_source") or raw_inputs.get("preview_action_source"),
        "detailer_output_pass": bool(raw_metadata.get("detailer_output_pass") or raw_params.get("detailer_output_pass")),
    })
    return clean


def extension_payload(payload: Any) -> dict[str, Any]:
    """Return the clean top-level V2 extension payload block."""
    return {"extensions": {EXTENSION_ID: normalize_block(payload)}}
