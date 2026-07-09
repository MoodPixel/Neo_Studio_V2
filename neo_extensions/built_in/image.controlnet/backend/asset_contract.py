from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

CONTROL_ASSET_KEYS = ("control_images", "control_masks", "generated_maps")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
FIT_MODES = {"contain", "cover", "stretch", "native"}
MASK_MODES = {"none", "control_mask", "inpaint_mask"}


def _truthy(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def _asset_ref(value: Any) -> dict[str, Any]:
    """Normalize a user/UI asset value into a small traceable reference.

    Phase F does not move/copy files. It prepares safe references that later
    workflow patch code can resolve into Comfy LoadImage inputs.
    """
    if isinstance(value, dict):
        ref = deepcopy(value)
    else:
        ref = {"ref": str(value or "").strip()}
    text = str(ref.get("ref") or ref.get("path") or ref.get("url") or ref.get("id") or "").strip()
    if text:
        ref.setdefault("ref", text)
        suffix = Path(text.split("?", 1)[0]).suffix.lower()
        if suffix:
            ref["extension"] = suffix
            ref["valid_image_extension"] = suffix in IMAGE_EXTENSIONS
        if text.startswith("/api/image/source-file/") or text.startswith("/api/image/mask-file/"):
            ref.setdefault("storage", "neo_data")
        elif text.startswith("/api/"):
            ref.setdefault("storage", "asset_ref")
        elif Path(text).is_absolute():
            ref.setdefault("storage", "local_path")
        else:
            ref.setdefault("storage", "asset_ref")
    return ref


def normalize_controlnet_unit_assets(unit: dict[str, Any], raw_assets: dict[str, Any] | None = None) -> dict[str, Any]:
    uid = str(unit.get("uid") or "unit_1")
    raw_assets = raw_assets if isinstance(raw_assets, dict) else {}
    control_images = raw_assets.get("control_images") if isinstance(raw_assets.get("control_images"), dict) else {}
    control_masks = raw_assets.get("control_masks") if isinstance(raw_assets.get("control_masks"), dict) else {}
    generated_maps = raw_assets.get("generated_maps") if isinstance(raw_assets.get("generated_maps"), dict) else {}

    result: dict[str, Any] = {"uid": uid}
    image_value = unit.get("control_image") or unit.get("control_image_name") or control_images.get(uid) or control_images.get("default") or control_images.get("primary")
    mask_value = unit.get("control_mask") or unit.get("control_mask_name") or control_masks.get(uid) or control_masks.get("default") or control_masks.get("primary")
    map_value = unit.get("generated_map") or generated_maps.get(uid)

    if _truthy(image_value):
        result["control_image"] = _asset_ref(image_value)
    if str(unit.get("mask_mode") or "none") == "control_mask" and _truthy(mask_value):
        result["control_mask"] = _asset_ref(mask_value)
    if _truthy(map_value):
        result["generated_map"] = _asset_ref(map_value)

    result["fit_mode"] = unit.get("fit_mode") if unit.get("fit_mode") in FIT_MODES else "contain"
    result["mask_mode"] = unit.get("mask_mode") if unit.get("mask_mode") in MASK_MODES else "none"
    result["preprocessor"] = str(unit.get("preprocessor") or unit.get("unit") or "none")
    result["needs_preprocess"] = result["preprocessor"] not in {"", "none"}
    result["has_control_source"] = bool(result.get("control_image") or result.get("generated_map"))
    return result


def normalize_assets(
    raw_assets: dict[str, Any] | None,
    *,
    active_unit_uids: set[str] | None = None,
    mask_requested: bool = True,
) -> dict[str, Any]:
    raw = raw_assets if isinstance(raw_assets, dict) else {}
    active = {str(uid) for uid in (active_unit_uids or set())}
    normalized: dict[str, Any] = {}
    for key in CONTROL_ASSET_KEYS:
        value = raw.get(key)
        if key == "control_masks" and not mask_requested:
            continue
        if isinstance(value, dict):
            filtered = {
                str(uid): _asset_ref(asset)
                for uid, asset in value.items()
                if not active or str(uid) in active or str(uid) in {"default", "primary"}
            }
            if filtered:
                normalized[key] = filtered
        elif isinstance(value, list):
            filtered_list = [_asset_ref(item) for item in value if _truthy(item)]
            if filtered_list and (not active or active):
                normalized[key] = filtered_list
        elif _truthy(value) and (not active or active):
            normalized[key] = _asset_ref(value)
    return normalized


def validate_asset_contract(units: list[dict[str, Any]], assets: dict[str, Any] | None = None) -> dict[str, Any]:
    notes: list[dict[str, Any]] = []
    normalized_units: list[dict[str, Any]] = []
    for index, unit in enumerate(units or []):
        normalized = normalize_controlnet_unit_assets(unit, assets)
        uid = normalized["uid"]
        if not normalized.get("has_control_source"):
            notes.append({"level": "warning", "field": f"units[{index}].control_image", "uid": uid, "message": "ControlNet unit has no control image or generated map attached yet."})
        for field in ("control_image", "control_mask", "generated_map"):
            ref = normalized.get(field)
            if isinstance(ref, dict) and ref.get("extension") and not ref.get("valid_image_extension", True):
                notes.append({"level": "error", "field": f"units[{index}].{field}", "uid": uid, "message": "ControlNet asset must be PNG, JPG, WEBP, BMP, or a Neo asset reference."})
        normalized_units.append(normalized)
    return {
        "ok": not any(note.get("level") == "error" for note in notes),
        "schema_version": "neo.image.controlnet.assets.v1",
        "units": normalized_units,
        "assets": normalize_assets(assets, active_unit_uids={str(unit.get("uid") or f"unit_{i+1}") for i, unit in enumerate(units or [])}, mask_requested=any(str(unit.get("mask_mode") or "none") == "control_mask" for unit in units or [])),
        "validation": notes,
    }


def asset_summary(assets: dict[str, Any] | None) -> dict[str, int]:
    normalized = normalize_assets(assets)
    summary: dict[str, int] = {}
    for key, value in normalized.items():
        if isinstance(value, dict):
            summary[key] = len(value)
        elif isinstance(value, list):
            summary[key] = len(value)
        else:
            summary[key] = 1 if value else 0
    return summary
