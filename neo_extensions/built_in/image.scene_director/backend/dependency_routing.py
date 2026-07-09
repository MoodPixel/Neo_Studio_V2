from __future__ import annotations

from copy import deepcopy
from typing import Any

IP_ADAPTER_EXTENSION_ID = "image.ip_adapter"
LORA_STACK_EXTENSION_ID = "lora_stack"


def _block(payload: dict[str, Any] | None, extension_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get(extension_id), dict):
        return payload.get(extension_id) or {}
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(extension_id), dict):
        return payloads.get(extension_id) or {}
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(extension_id), dict):
        return extensions.get(extension_id) or {}
    return {}


def extension_enabled(payload: dict[str, Any] | None, extension_id: str) -> bool:
    return bool(_block(payload, extension_id).get("enabled"))


def dependency_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "ip_adapter": {
            "extension_id": IP_ADAPTER_EXTENSION_ID,
            "enabled": extension_enabled(payload, IP_ADAPTER_EXTENSION_ID),
            "present": bool(_block(payload, IP_ADAPTER_EXTENSION_ID)),
            "required_for": ["regional_identity_profiles", "regional_faceid_ipadapter"],
        },
        "lora_stack": {
            "extension_id": LORA_STACK_EXTENSION_ID,
            "enabled": extension_enabled(payload, LORA_STACK_EXTENSION_ID),
            "present": bool(_block(payload, LORA_STACK_EXTENSION_ID)),
            "required_for": ["regional_lora_targets", "identity_profile_optional_lora"],
        },
    }


def _region_index_from_apply_to(apply_to: Any, regions: list[dict[str, Any]]) -> int | None:
    target = str(apply_to or "").strip()
    if not target:
        return None
    if target.startswith("scene_region_"):
        suffix = target.replace("scene_region_", "", 1)
        if suffix.isdigit():
            index = int(suffix)
            return index if 1 <= index <= len(regions) else None
    for idx, region in enumerate(regions, start=1):
        if target == str(region.get("id") or ""):
            return idx
    return None


def _subject_slot_by_region(regions: list[dict[str, Any]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    slot = 1
    for region in regions:
        if str(region.get("type") or region.get("role") or "").strip().lower() != "character":
            continue
        rid = str(region.get("id") or "").strip()
        if rid and slot <= 4:
            mapping[rid] = slot
        slot += 1
    return mapping


def regional_lora_bindings_from_lora_stack(payload: dict[str, Any] | None, regions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Map image.lora_stack rows with apply_to=scene_region_* into Scene Director regional bindings.

    The LoRA Stack remains the owner of LoRA selection/library metadata. Scene
    Director only consumes rows that explicitly target a Scene Director region.
    Global rows stay with LoRA Stack's normal graph patch path.
    """
    block = _block(payload, LORA_STACK_EXTENSION_ID)
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    bindings: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not rows:
        return bindings, warnings
    if not block.get("enabled"):
        targeted = [row for row in rows if isinstance(row, dict) and str(row.get("apply_to") or "").startswith("scene_region_")]
        if targeted:
            warnings.append("regional_lora_targets_requested_but_lora_stack_disabled")
        return bindings, warnings
    subject_slots = _subject_slot_by_region(regions)
    for slot, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        region_index = _region_index_from_apply_to(row.get("apply_to"), regions)
        if not region_index:
            continue
        region = regions[region_index - 1]
        region_id = str(region.get("id") or "")
        subject_slot = subject_slots.get(region_id)
        try:
            strength = float(row.get("strength", 0.8))
        except Exception:
            strength = 0.8
        strength = max(-4.0, min(4.0, strength))
        row_uid = str(row.get("uid") or row.get("row_id") or f"lora_{slot}")
        bindings.append({
            "uid": f"scene_lora_stack_target_{slot}_region_{region_index}",
            "lora_row_id": row_uid,
            "row_id": row_uid,
            "region_id": region_id,
            "region_index": region_index,
            "subject_slot": subject_slot,
            "attn_mask_output_index": (5 + subject_slot) if subject_slot else None,
            "label": str(region.get("label") or f"Region {region_index}"),
            "slot": slot,
            "name": str(row.get("name") or row.get("lora_name") or ""),
            "source_record_id": str(row.get("source_record_id") or row.get("record_id") or ""),
            "source_record_trigger_words": str(row.get("source_record_trigger_words") or row.get("trigger_words") or ""),
            "source_record_activation_text": str(row.get("source_record_activation_text") or row.get("activation_text") or ""),
            "apply_to": str(row.get("apply_to") or ""),
            "target": str(row.get("target") or "both"),
            "weight_mode": "slot_default",
            "strength": round(strength, 4),
            "owner_row": deepcopy(row),
            "source": "neo_lora_stack_apply_to_targeting",
            "dependency_extension_id": LORA_STACK_EXTENSION_ID,
        })
    return bindings, warnings


def dependency_warnings(
    payload: dict[str, Any] | None,
    *,
    identity_units: list[dict[str, Any]] | None = None,
    ipadapter_bindings: list[dict[str, Any]] | None = None,
    lora_bindings: list[dict[str, Any]] | None = None,
) -> list[str]:
    deps = dependency_status(payload)
    warnings: list[str] = []
    identity_requested = bool(identity_units or ipadapter_bindings)
    # Scene Director can hard-bridge Character Profile identity units into
    # masked IPAdapter/FaceID nodes using the installed Comfy node pack even
    # when the standalone image.ip_adapter UI master switch is off. Warn only
    # when the owner extension/node-pack is missing from the payload context;
    # otherwise this warning is misleading after the hard bridge succeeds.
    if identity_requested and not deps["ip_adapter"]["present"]:
        warnings.append("regional_identity_requested_but_image.ip_adapter_not_available")
    if lora_bindings and not deps["lora_stack"]["enabled"]:
        warnings.append("regional_lora_requested_but_lora_stack_not_enabled")
    return warnings


def interop_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "dependencies": dependency_status(payload),
        "routing_policy": {
            "identity_profiles": "scene_director_intent_requires_image.ip_adapter_for_regional_faceid_ipadapter_execution",
            "regional_loras": "image.lora_stack_owns_lora_selection_scene_director_consumes_apply_to_scene_region_targets",
        },
    }
