from __future__ import annotations

from typing import Any, Final

from neo_app.video.performance_profiles import build_video_performance_contract
from neo_app.video.low_vram_adapter import low_vram_probe_payload

SCHEMA_VERSION: Final[str] = "neo.video.performance_probe.vg13"
PHASE: Final[str] = "V-G13"

OPTIMIZER_NODE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "sage_attention": (
        "PathchSageAttentionKJ",
        "PatchSageAttentionKJ",
        "Patch Sage Attention KJ",
        "SageAttentionKJ",
    ),
    "teacache": (
        "TeaCache",
        "ApplyTeaCache",
        "TeaCachePatch",
        "WanVideoTeaCacheKJ",
        "WanVideoTeaCache",
        "LTXVTeaCache",
        "TeaCacheModelPatch",
        "WanVideoApplyTeaCache",
        "TeaCacheForWanVideo",
        "LTXVApplyTeaCache",
    ),
    "wan_block_swap": (
        "WanVideoBlockSwap",
        "WanVideoBlockSwapAdvanced",
        "WanVideoBlockSwapKJ",
    ),
    "ltx_low_vram": (
        "LTXVModelLoaderLowVRAM",
        "LTXVModelLoaderAdvanced",
        "LTXVModelLoaderKJ",
    ),
    "vae_tiled_or_offload": (
        "VAEDecodeTiled",
        "VAEDecodeTiledKJ",
        "VAELoaderKJ",
    ),
}


def _available_nodes(object_info: dict[str, Any] | None) -> set[str]:
    return {str(key) for key in (object_info or {}).keys()}


def _matches(nodes: set[str], aliases: tuple[str, ...]) -> list[str]:
    folded = {node.casefold(): node for node in nodes}
    found: list[str] = []
    for alias in aliases:
        exact = folded.get(alias.casefold())
        if exact:
            found.append(exact)
            continue
        needle = alias.casefold().replace(" ", "").replace("_", "")
        fuzzy = next((node for node in nodes if needle and needle in node.casefold().replace(" ", "").replace("_", "")), None)
        if fuzzy:
            found.append(fuzzy)
    return list(dict.fromkeys(found))


def _combo_values(object_info: dict[str, Any] | None, class_type: str, input_name: str) -> list[str]:
    entry = (object_info or {}).get(class_type, {})
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    for kind in ("required", "optional"):
        data = inputs.get(kind, {}) if isinstance(inputs, dict) else {}
        spec = data.get(input_name) if isinstance(data, dict) else None
        if isinstance(spec, list) and spec:
            values = spec[0]
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _node_output_types(object_info: dict[str, Any] | None, class_type: str) -> list[str]:
    entry = (object_info or {}).get(class_type, {})
    if not isinstance(entry, dict):
        return []
    raw = entry.get("output") or entry.get("outputs") or entry.get("return_types") or entry.get("return")
    if isinstance(raw, (list, tuple)):
        return [str(item).strip().upper() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip().upper()]
    return []


def _teacache_model_patch_compatible(object_info: dict[str, Any] | None, class_type: str) -> bool:
    outputs = set(_node_output_types(object_info, class_type))
    if outputs:
        return "MODEL" in outputs
    entry = (object_info or {}).get(class_type, {})
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    known: set[str] = set()
    for kind in ("required", "optional"):
        data = inputs.get(kind, {}) if isinstance(inputs, dict) else {}
        if isinstance(data, dict):
            known.update(str(key).casefold() for key in data.keys())
    return any(item in known for item in ("model", "model_in", "diffusion_model"))


def video_performance_probe_payload(
    object_info: dict[str, Any] | None,
    *,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    performance_profile: str | None = None,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = _available_nodes(object_info)
    optimizer_nodes = {key: {"available": bool(matches := _matches(nodes, aliases)), "matched": matches, "expected_any": list(aliases)} for key, aliases in OPTIMIZER_NODE_ALIASES.items()}
    contract = build_video_performance_contract(
        family=family,
        loader=loader,
        generation_type=generation_type,
        performance_profile=performance_profile,
        values=values or {},
    )
    sage_modes: list[str] = []
    sage_class = ""
    sage_mode_field = "sage_attention"
    for class_type in optimizer_nodes["sage_attention"]["matched"]:
        for field in ("sage_attention", "mode", "attention_mode", "sage_mode"):
            values_found = _combo_values(object_info, class_type, field)
            if values_found:
                sage_modes = values_found
                sage_class = class_type
                sage_mode_field = field
                break
        if sage_modes:
            break

    action_items: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    selected = contract.get("selected", {}) if isinstance(contract.get("selected"), dict) else {}
    if selected.get("enable_sage_attention") and not optimizer_nodes["sage_attention"]["available"]:
        errors.append("Sage Attention is enabled, but no KJ Sage Attention node was detected in ComfyUI /object_info.")
        action_items.append("Install/enable ComfyUI-KJNodes Sage Attention nodes, restart ComfyUI, then refresh backend probe.")
    requested_sage_mode = str(selected.get("sage_attention_mode") or "auto")
    if selected.get("enable_sage_attention") and sage_modes and requested_sage_mode not in {"", "auto"} and requested_sage_mode not in sage_modes:
        errors.append(f"Selected Sage Attention mode is not available in this Comfy install: {requested_sage_mode}")
        action_items.append("Choose one of the Sage Attention modes detected from ComfyUI /object_info.")
    if selected.get("enable_sage_attention"):
        warnings.append("Sage Attention is experimental; if outputs are black or unstable, disable it and retest base GGUF/LightX2V.")
    teacache_class = optimizer_nodes["teacache"]["matched"][0] if optimizer_nodes["teacache"]["matched"] else ""
    # Prefer a MODEL-returning TeaCache class when multiple TeaCache nodes are installed.
    for candidate in optimizer_nodes["teacache"]["matched"]:
        if _teacache_model_patch_compatible(object_info, candidate):
            teacache_class = candidate
            break
    teacache_fields: list[str] = []
    teacache_outputs: list[str] = []
    teacache_model_patch_compatible = False
    if teacache_class:
        teacache_outputs = _node_output_types(object_info, teacache_class)
        teacache_model_patch_compatible = _teacache_model_patch_compatible(object_info, teacache_class)
        entry = (object_info or {}).get(teacache_class, {})
        inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
        for group_name in ("required", "optional"):
            group = inputs.get(group_name, {}) if isinstance(inputs, dict) else {}
            if isinstance(group, dict):
                teacache_fields.extend(str(key) for key in group.keys())
    if selected.get("enable_teacache") and not optimizer_nodes["teacache"]["available"]:
        errors.append("TeaCache is enabled, but no TeaCache node was detected in ComfyUI /object_info.")
        action_items.append("Install/enable a TeaCache node pack compatible with the selected Video route, then refresh backend probe.")
    if selected.get("enable_teacache") and teacache_class and not teacache_model_patch_compatible:
        outputs = ", ".join(teacache_outputs) or "unknown"
        errors.append(f"TeaCache node '{teacache_class}' is not compatible with the current WAN GGUF route because it returns {outputs}, not MODEL.")
        action_items.append("Disable TeaCache for the WAN GGUF KSamplerAdvanced route, or use a MODEL-returning TeaCache patch node. CACHEARGS TeaCache nodes belong to WanVideoWrapper sampler workflows.")
    if selected.get("enable_teacache"):
        warnings.append("TeaCache is experimental; start with conservative mode and compare against a base render.")
    low_vram_probe = low_vram_probe_payload(object_info, family=family, values=selected)
    for item in low_vram_probe.get("errors", []):
        if item not in errors:
            errors.append(str(item))
    for item in low_vram_probe.get("warnings", []):
        if item not in warnings:
            warnings.append(str(item))
    action_items.extend(str(item) for item in low_vram_probe.get("action_items", []) if item)

    queue_ready = bool(contract.get("queue_ready", True) and not errors)

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "route_id": contract.get("route_id", ""),
        "performance_profile": contract.get("profile", {}),
        "selected": selected,
        "optimizer_support": contract.get("optimizer_support", {}),
        "optimizer_nodes": optimizer_nodes,
        "available_modes": {"sage_attention": sage_modes},
        "sage_attention_probe": {"class_type": sage_class, "mode_field": sage_mode_field, "available_modes": sage_modes, "selected_mode": requested_sage_mode},
        "teacache_probe": {"class_type": teacache_class, "fields": list(dict.fromkeys(teacache_fields)), "outputs": teacache_outputs, "model_patch_compatible": teacache_model_patch_compatible, "selected_profile": str(selected.get("teacache_profile") or "conservative"), "selected_target": str(selected.get("teacache_target") or "both")},
        "low_vram_probe": low_vram_probe,
        "queue_ready": queue_ready,
        "warnings": list(dict.fromkeys([*contract.get("warnings", []), *warnings])),
        "errors": list(dict.fromkeys([*contract.get("errors", []), *errors])),
        "action_items": list(dict.fromkeys([*contract.get("action_items", []), *action_items])),
        "rules": [
            "V-G13 probes optional performance node packs and validates active Sage/TeaCache/low-VRAM selections.",
            "Missing optional nodes only block queueing when the matching optimizer is enabled.",
        ],
    }
