from __future__ import annotations
from typing import Any

from neo_app.providers.comfy_model_paths import discover_comfy_model_files

STANDARD_REQUIRED = ["CLIPVisionLoader", "IPAdapterModelLoader", "IPAdapterAdvanced"]
FACEID_REQUIRED = ["CLIPVisionLoader", "IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceID"]
OPTIONAL = ["ImageBatch", "IPAdapterInsightFaceLoader"]


FACEID_MARKERS = ("faceid", "face_id", "face-id", "insightface")
FACEID_PRESET_LABELS = {"model", "faceid", "faceid plus", "faceid plus v2", "faceid portrait", "faceid portrait unnorm"}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value or "").strip().replace("\\", "/")
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def split_ip_adapter_model_names(values: list[str]) -> dict[str, list[str]]:
    """Split generic Comfy IP Adapter choices into executable model families."""

    faceid = [name for name in values if looks_like_faceid_model(name)]
    standard = [name for name in values if not looks_like_faceid_model(name)]
    return {"ip_adapter": _dedupe(standard), "faceid": _dedupe(faceid)}


def _split_ip_adapter_models(values: list[str]) -> dict[str, list[str]]:
    """Compatibility wrapper for older extension imports/tests."""

    return split_ip_adapter_model_names(values)


def _registered_model_inputs(backend_details: dict[str, Any] | None = None) -> dict[str, list[str]]:
    details = backend_details or {}
    folders = details.get("comfy_model_folders") if isinstance(details.get("comfy_model_folders"), dict) else {}
    ip_files: list[str] = []
    clip_files: list[str] = []
    for key in ("ipadapter", "ip_adapter"):
        values = folders.get(key) or []
        if isinstance(values, (list, tuple)):
            ip_files.extend(str(item) for item in values)
    for key in ("clip_vision", "clipvision"):
        values = folders.get(key) or []
        if isinstance(values, (list, tuple)):
            clip_files.extend(str(item) for item in values)
    split = _split_ip_adapter_models(_dedupe(ip_files))
    return {"clip_vision": _dedupe(clip_files), **split}


def discover_model_path_catalog(backend_details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Discover real IP Adapter files from the selected Comfy model roots."""

    details = backend_details or {}
    clip_scan = discover_comfy_model_files(
        details,
        folder_names=("clip_vision",),
        extra_model_categories={"clip_vision", "clip_visions", "clipvision"},
    )
    ip_scan = discover_comfy_model_files(
        details,
        folder_names=("ipadapter",),
        extra_model_categories={"ipadapter", "ip_adapter", "ip_adapters"},
    )
    registered = _registered_model_inputs(details)
    ip_sources = ip_scan.get("sources") if isinstance(ip_scan.get("sources"), dict) else {}
    clip_sources = clip_scan.get("sources") if isinstance(clip_scan.get("sources"), dict) else {}

    direct_ip = _split_ip_adapter_models(list(ip_sources.get("models_root") or []))
    extra_ip = _split_ip_adapter_models(list(ip_sources.get("extra_model_paths") or []))
    direct_inputs = {
        "clip_vision": _dedupe([str(item) for item in (clip_sources.get("models_root") or [])]),
        **direct_ip,
    }
    extra_inputs = {
        "clip_vision": _dedupe([str(item) for item in (clip_sources.get("extra_model_paths") or [])]),
        **extra_ip,
    }
    model_inputs = merge_model_inputs(registered, direct_inputs, extra_inputs)
    return {
        "model_inputs": model_inputs,
        "sources": {
            "comfy_model_folders": registered,
            "models_root": direct_inputs,
            "extra_model_paths": extra_inputs,
        },
        "diagnostics": {
            "schema_version": "neo.image.ip_adapter.model_catalog.v1",
            "path_policy": "absolute_paths_server_side_only",
            "registered_folders": dict(details.get("comfy_model_folder_diagnostics") or {}),
            "ip_adapter_filesystem": dict(ip_scan.get("diagnostics") or {}),
            "clip_vision_filesystem": dict(clip_scan.get("diagnostics") or {}),
        },
    }


def discover_extra_model_path_inputs(backend_details: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Compatibility API returning the complete direct + extra path catalog."""

    return dict(discover_model_path_catalog(backend_details).get("model_inputs") or {})


def merge_model_inputs(*sources: dict[str, list[str]] | None) -> dict[str, list[str]]:
    merged = {"clip_vision": [], "ip_adapter": [], "faceid": []}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in merged:
            values = source.get(key) or []
            if isinstance(values, list):
                merged[key].extend(str(item) for item in values)
    return {key: _dedupe(value) for key, value in merged.items()}


def _names(object_info: Any) -> set[str]:
    if isinstance(object_info, dict):
        return {str(k) for k in object_info.keys()}
    if isinstance(object_info, (set, list, tuple)):
        return {str(v) for v in object_info}
    return set()


def _faceid_loader_contract(object_info: Any) -> dict[str, Any]:
    names = _names(object_info)
    if object_info is None:
        names = {"IPAdapterUnifiedLoaderFaceID"}
    node_name = "IPAdapterUnifiedLoaderFaceID"
    inputs: set[str] = set()
    if isinstance(object_info, dict):
        schema = object_info.get(node_name) or {}
        node_inputs = schema.get("input") or {}
        for bucket in (node_inputs.get("required") or {}, node_inputs.get("optional") or {}):
            inputs.update(str(name) for name in bucket.keys())
    available = node_name in names
    preset_owned = available and (not inputs or "preset" in inputs)
    return {
        "loader_node": node_name if available else "",
        "loader_strategy": "unified_preset_resolution" if preset_owned else "unsupported",
        "model_selection_mode": "validated_resolution_assertion" if preset_owned else "unsupported",
        "input_names": sorted(inputs),
    }


def _extract_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip() for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _node_choices(object_info: Any, node_name: str, *input_names: str) -> list[str]:
    if not isinstance(object_info, dict) or not node_name:
        return []
    node = object_info.get(node_name) or {}
    inputs = node.get("input") or {}
    required = inputs.get("required") or {}
    optional = inputs.get("optional") or {}
    values: list[str] = []
    seen: set[str] = set()
    for input_name in input_names:
        for item in _extract_choices(required.get(input_name)) + _extract_choices(optional.get(input_name)):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                values.append(item)
    return values


def _first_existing_node(object_info: Any, aliases: list[str]) -> str:
    names = _names(object_info)
    return next((alias for alias in aliases if alias in names), "")


def looks_like_faceid_model(value: Any) -> bool:
    lowered = str(value or "").strip().casefold()
    return bool(lowered) and any(marker in lowered for marker in FACEID_MARKERS)


def _looks_like_faceid_model(value: Any) -> bool:
    """Compatibility wrapper for the former private classifier."""

    return looks_like_faceid_model(value)


def _looks_like_faceid_preset(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text in FACEID_PRESET_LABELS or text.startswith("faceid plus -") or text.startswith("faceid plus ·") or text.startswith("faceid portrait ")


def extract_model_inputs(object_info: Any) -> dict[str, list[str]]:
    clip_node = _first_existing_node(object_info, ["CLIPVisionLoader", "CLIPVisionLoaderModelOnly"])
    ip_node = _first_existing_node(object_info, ["IPAdapterModelLoader", "IPAdapterUnifiedLoader", "IPAdapterLoader"])
    faceid_node = _first_existing_node(object_info, ["IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceIDModelLoader"])
    ip_choices = _node_choices(object_info, ip_node, "ipadapter_file", "ipadapter_name", "model", "model_name", "name")
    faceid_node_choices = [item for item in _node_choices(object_info, faceid_node, "model", "model_name", "ipadapter_file", "faceid_model") if not _looks_like_faceid_preset(item)]
    faceid_from_ip_models = [item for item in ip_choices if _looks_like_faceid_model(item)]
    standard_ip_models = [item for item in ip_choices if not _looks_like_faceid_model(item)]
    return {
        "clip_vision": _node_choices(object_info, clip_node, "clip_name", "clip_vision_name", "model_name"),
        "ip_adapter": _dedupe(standard_ip_models),
        "faceid": _dedupe(faceid_node_choices + faceid_from_ip_models),
    }


def inspect_nodes(object_info: Any) -> dict[str, Any]:
    names = _names(object_info)
    if object_info is None:
        names = set(STANDARD_REQUIRED + FACEID_REQUIRED + OPTIONAL)
    standard_missing = [node for node in STANDARD_REQUIRED if node not in names]
    faceid_missing = [node for node in FACEID_REQUIRED if node not in names]
    return {
        "schema_version": "neo.image.ip_adapter.nodes.v1",
        "standard_required": STANDARD_REQUIRED,
        "faceid_required": FACEID_REQUIRED,
        "optional": OPTIONAL,
        "available": sorted(names),
        "standard_available": not standard_missing,
        "faceid_available": not faceid_missing,
        "standard_missing": standard_missing,
        "faceid_missing": faceid_missing,
        "faceid_loader_contract": _faceid_loader_contract(object_info),
        "image_batch_available": "ImageBatch" in names,
        "model_inputs": extract_model_inputs(object_info),
        "unknown_object_info": object_info is None,
    }
