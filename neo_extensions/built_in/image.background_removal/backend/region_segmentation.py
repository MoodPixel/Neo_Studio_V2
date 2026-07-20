"""Face, clothes, fashion, and accessory segmentation contracts."""
from __future__ import annotations

import json
from typing import Any


SCHEMA_ID = "neo.image.background_removal.region_segmentation.v1"
SCHEMA_VERSION = 1
MAX_REGION_TARGETS = 8
MAX_CLASS_SELECTIONS = 32
VALID_REGION_ADAPTERS = {"auto", "face", "clothes", "fashion", "accessories"}
VALID_MASK_OPERATIONS = {"union", "intersection", "subtract"}

_ADAPTER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "face": {
        "label": "Face parsing · skin, hair, eyes, lips",
        "node_class": "FaceSegment",
        "required": ("images",),
        "mask_output": 1,
        "classes": ("Skin", "Nose", "Eyeglasses", "Left-eye", "Right-eye", "Left-eyebrow", "Right-eyebrow", "Left-ear", "Right-ear", "Mouth", "Upper-lip", "Lower-lip", "Hair", "Earring", "Neck"),
        "default_classes": ("Skin", "Nose", "Left-eye", "Right-eye", "Mouth"),
    },
    "clothes": {
        "label": "Clothes parsing · garments, body parts, shoes",
        "node_class": "ClothesSegment",
        "required": ("images",),
        "mask_output": 1,
        "classes": ("Hat", "Hair", "Face", "Sunglasses", "Upper-clothes", "Skirt", "Dress", "Belt", "Pants", "Left-arm", "Right-arm", "Left-leg", "Right-leg", "Bag", "Scarf", "Left-shoe", "Right-shoe", "Background"),
        "default_classes": ("Upper-clothes",),
    },
    "fashion": {
        "label": "Fashion parsing · garments and fashion details",
        "node_class": "FashionSegmentClothing",
        "required": ("images",),
        "mask_output": 1,
        "classes": ("coat", "jacket", "cardigan", "vest", "sweater", "hood", "shirt, blouse", "top, t-shirt, sweatshirt", "sleeve", "dress", "jumpsuit", "cape", "pants", "shorts", "skirt", "tights, stockings", "sock", "shoe", "glasses", "hat", "headband, head covering, hair accessory", "tie", "glove", "watch", "belt", "leg warmer", "bag, wallet", "scarf", "umbrella", "collar", "lapel", "epaulette", "pocket", "neckline", "buckle", "zipper", "applique", "bead", "bow", "flower", "fringe", "ribbon", "rivet", "ruffle", "sequin", "tassel"),
        "default_classes": ("shirt, blouse",),
    },
    "accessories": {
        "label": "Accessories and details · hats, bags, jewelry, trims",
        "node_class": "FashionSegmentClothing",
        "options_node_class": "FashionSegmentAccessories",
        "required": ("images", "accessories_options"),
        "mask_output": 1,
        "classes": ("hat", "glasses", "headband, head covering, hair accessory", "scarf", "tie", "glove", "watch", "belt", "leg warmer", "bag, wallet", "umbrella", "collar", "lapel", "neckline", "epaulette", "pocket", "buckle", "zipper", "applique", "bow", "flower", "bead", "fringe", "ribbon", "rivet", "ruffle", "sequin", "tassel"),
        "default_classes": ("hat",),
    },
}


def _input_block(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("input")
    return value if isinstance(value, dict) else {}


def _input_names(spec: dict[str, Any]) -> set[str]:
    block = _input_block(spec)
    names: set[str] = set()
    for section in ("required", "optional", "hidden"):
        values = block.get(section)
        if isinstance(values, dict):
            names.update(str(name) for name in values)
    return names


def _find_node(object_info: dict[str, Any] | None, node_class: str) -> tuple[str, dict[str, Any]] | None:
    for raw_name, spec in (object_info or {}).items():
        if str(raw_name).casefold() == node_class.casefold() and isinstance(spec, dict):
            return str(raw_name), spec
    return None


def build_region_segmentation_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for adapter_id, definition in _ADAPTER_DEFINITIONS.items():
        found = _find_node(object_info, definition["node_class"])
        node_name, spec = found if found else ("", {})
        names = _input_names(spec)
        missing = sorted(set(definition["required"]) - names)
        options_found = _find_node(object_info, definition.get("options_node_class", "")) if definition.get("options_node_class") else None
        options_node_name, options_spec = options_found if options_found else ("", {})
        options_names = _input_names(options_spec)
        live_classes = [item for item in definition["classes"] if item in (options_names if options_found else names)]
        blockers: list[str] = []
        if not object_info:
            blockers.append("Live ComfyUI /object_info is unavailable.")
        elif not found:
            blockers.append(f"The installed Comfy profile does not expose {definition['node_class']}.")
        elif definition.get("options_node_class") and not options_found:
            blockers.append(f"The installed Comfy profile does not expose {definition['options_node_class']} required by the accessories route.")
        elif missing:
            blockers.append(f"Live node {node_name} is missing verified input(s): {', '.join(missing)}.")
        elif definition.get("options_node_class") and not options_names:
            blockers.append(f"Live node {options_node_name} exposes no verified accessory or detail selectors.")
        elif not live_classes:
            blockers.append(f"Live node {node_name} exposes no verified region class toggles.")
        rows.append({
            "id": adapter_id,
            "label": definition["label"],
            "available": not blockers,
            "node_class": node_name,
            "options_node_class": options_node_name,
            "required_inputs": list(definition["required"]),
            "input_names": sorted(names),
            "options_input_names": sorted(options_names),
            "classes": live_classes,
            "default_classes": [item for item in definition["default_classes"] if item in live_classes] or live_classes[:1],
            "mask_output": int(definition["mask_output"]),
            "blockers": blockers,
            "execution_policy": "live_object_info_exact_node_and_optional_inputs_only",
        })
    available = [row for row in rows if row["available"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "adapters": rows,
        "available": bool(available),
        "default_adapter": next((row["id"] for row in available if row["id"] == "fashion"), available[0]["id"] if available else ""),
        "mask_operations": ["union", "intersection", "subtract"],
        "limits": {"max_targets": MAX_REGION_TARGETS, "max_classes_per_target": MAX_CLASS_SELECTIONS},
        "safety": {"requires_live_object_info": True, "no_silent_fallback": True, "path_policy": "portable_identifiers_only"},
    }


def normalize_region_targets(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            value = []
    rows = value if isinstance(value, list) else []
    targets: list[dict[str, Any]] = []
    for index, item in enumerate(rows[:MAX_REGION_TARGETS], start=1):
        if isinstance(item, str):
            item = {"region": item}
        if not isinstance(item, dict):
            continue
        region = str(item.get("region") or item.get("adapter") or "").strip().lower()
        if region not in VALID_REGION_ADAPTERS - {"auto"}:
            continue
        raw_classes = item.get("classes")
        if isinstance(raw_classes, str):
            raw_classes = [part.strip() for part in raw_classes.split(",")]
        classes = [str(part).strip()[:96] for part in (raw_classes or []) if str(part).strip()][:MAX_CLASS_SELECTIONS]
        targets.append({
            "id": str(item.get("id") or f"{region}_{index}").strip()[:64] or f"{region}_{index}",
            "label": str(item.get("label") or region.title()).strip()[:120] or region.title(),
            "region": region,
            "adapter": str(item.get("adapter") or region).strip().lower(),
            "node_class": str(item.get("node_class") or "").strip(),
            "options_node_class": str(item.get("options_node_class") or "").strip(),
            "options_input_names": [str(part).strip() for part in (item.get("options_input_names") or []) if str(part).strip()][:MAX_CLASS_SELECTIONS],
            "classes": classes,
            "enabled": item.get("enabled", True) is not False,
        })
    return targets


def normalize_region_segmentation(source: dict[str, Any] | None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    adapter = str(raw.get("region_segmentation_adapter") or "auto").strip().lower()
    if adapter not in VALID_REGION_ADAPTERS:
        adapter = "auto"
    operation = str(raw.get("region_segmentation_mask_operation") or "union").strip().lower()
    if operation not in VALID_MASK_OPERATIONS:
        operation = "union"
    return {
        "enabled": raw.get("region_segmentation_enabled", raw.get("workflow_mode") == "region_segmentation") is True or str(raw.get("region_segmentation_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "adapter": adapter,
        "node_class": str(raw.get("region_segmentation_node_class") or "").strip(),
        "mask_operation": operation,
        "targets": normalize_region_targets(raw.get("region_segmentation_targets")),
    }


def resolve_region_adapter(catalog: dict[str, Any] | None, requested: str = "auto") -> dict[str, Any]:
    rows = [row for row in (catalog or {}).get("adapters", []) if isinstance(row, dict)]
    requested = requested if requested in VALID_REGION_ADAPTERS else "auto"
    if requested != "auto":
        row = next((item for item in rows if item.get("id") == requested), None)
        if not row or not row.get("available"):
            return {"ready": False, "adapter": requested, "blockers": list((row or {}).get("blockers") or [f"Region adapter is unavailable: {requested}."])}
        return {"ready": True, "adapter": requested, "row": row, "blockers": []}
    row = next((item for item in rows if item.get("available")), None)
    if not row:
        return {"ready": False, "adapter": "", "blockers": ["No verified face, clothes, fashion, or accessories adapter is available in the active Comfy profile."]}
    return {"ready": True, "adapter": str(row.get("id") or ""), "row": row, "blockers": []}
