from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo_app.providers.comfy_model_paths import discover_comfy_model_files

AVAILABLE = "available"
EXPERIMENTAL_AVAILABLE = "experimental_available"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"

LOADER_CANDIDATES = ("ControlNetLoader",)
STANDARD_APPLY_CANDIDATES = ("ControlNetApply",)
ADVANCED_APPLY_CANDIDATES = (
    "ControlNetApplyAdvanced",
    "ACN_AdvancedControlNetApply",
    "ACN_ControlNetApplyAdvanced",
    "AdvancedControlNetApply",
    "ControlNetApplyAdvanced_ACN",
    "ACN_ApplyAdvancedControlNet",
)
APPLY_CANDIDATES = ADVANCED_APPLY_CANDIDATES + STANDARD_APPLY_CANDIDATES

QWEN_DIFFSYNTH_PATCH_LOADER_CANDIDATES = (
    "ModelPatchLoader",
    "QwenImageModelPatchLoader",
    "DiffSynthModelPatchLoader",
)
QWEN_DIFFSYNTH_APPLY_CANDIDATES = (
    "QwenImageDiffsynthControlnet",
    "QwenImageDiffSynthControlNet",
    "QwenImageDiffSynthControlnet",
    "QwenImageDiffsynthControlNet",
)
QWEN_INSTANTX_LOADER_CANDIDATES = (
    "ControlNetLoader",
    "QwenImageControlNetLoader",
)
QWEN_INSTANTX_APPLY_CANDIDATES = (
    "ControlNetApplyAdvanced",
    "QwenImageControlNetApply",
    "QwenImageControlNetApplyAdvanced",
    "QwenImageApplyControlNet",
    "QwenImageApplyControlNetAdvanced",
)

FLUX_INPAINT_LOADER_CANDIDATES = (
    "ControlNetLoader",
    "FluxControlNetLoader",
    "LoadFluxControlNet",
    "FluxLoadControlNet",
)
FLUX_INPAINT_APPLY_CANDIDATES = (
    "ControlNetApplyAdvanced",
    "FluxControlNetApply",
    "FluxControlNetApplyAdvanced",
    "ApplyFluxControlNet",
    "ApplyFluxControlNetAdvanced",
    "XlabsApplyFluxControlNet",
)
FLUX2_KLEIN_FUN_UNION_LOADER_CANDIDATES = (
    "Flux2FunControlNetLoader",
    "Flux2ControlNetLoader",
    "FluxFunControlNetLoader",
    "FluxControlNetLoader",
    "ControlNetLoader",
)
FLUX2_KLEIN_FUN_UNION_APPLY_CANDIDATES = (
    "Flux2FunControlNetApplyAdvanced",
    "Flux2ControlNetApplyAdvanced",
    "FluxFunControlNetApplyAdvanced",
    "FluxControlNetApplyAdvanced",
    "ControlNetApplyAdvanced",
)

Z_IMAGE_FUN_UNION_PATCH_LOADER_CANDIDATES = (
    "ModelPatchLoader",
    "ZImageControlNetLoader",
    "ZImageFunControlNetLoader",
    "ZImageTurboFunControlNetLoader",
    "ZImageTurboControlNetLoader",
)
Z_IMAGE_FUN_UNION_APPLY_CANDIDATES = (
    "ZImageControlNetApply",
    "ZImageControlNetApplyAdvanced",
    "ZImageFunControlNetApply",
    "ZImageFunControlNetApplyAdvanced",
    "ZImageTurboFunControlNetApply",
    "ZImageTurboFunControlNetApplyAdvanced",
    "ControlNetApplyAdvanced",
)

PREPROCESSOR_CANDIDATES: dict[str, tuple[str, ...]] = {
    "canny": ("CannyEdgePreprocessor", "CannyPreprocessor"),
    "softedge": ("HEDPreprocessor", "HEDPreprocessor_safe", "PiDiNetPreprocessor", "PiDiNetPreprocessor_safe", "TEEDPreprocessor"),
    "lineart": ("LineArtPreprocessor", "LineartPreprocessor", "LineartStandardPreprocessor"),
    "lineart_anime": ("AnimeLineArtPreprocessor", "LineartAnimePreprocessor", "LineArtAnimePreprocessor"),
    "scribble": ("ScribblePreprocessor", "Scribble_XDoG_Preprocessor", "Scribble_PiDiNet_Preprocessor", "FakeScribblePreprocessor"),
    "openpose": (
        "DWPreprocessor",
        "DWPose_Preprocessor",
        "OpenposePreprocessor",
        "OpenPosePreprocessor",
        "OpenPoseHandPreprocessor",
        "OpenposeHandPreprocessor",
        "OpenPoseFacePreprocessor",
        "OpenposeFacePreprocessor",
    ),
    "depth": ("MiDaS-DepthMapPreprocessor", "Zoe-DepthMapPreprocessor", "LeReS-DepthMapPreprocessor", "DepthAnythingPreprocessor", "DepthAnythingV2Preprocessor"),
    "normalbae": ("NormalBaePreprocessor", "BAE-NormalMapPreprocessor", "NormalMapPreprocessor"),
    "tile": ("TilePreprocessor",),
}

# V1 has local fallback map builders for these modes. Phase C records this so
# provider gating stays precise: a missing canny node is not the same as missing
# ControlNetApply. Actual map generation remains a later phase.
LOCAL_FALLBACK_PREPROCESSORS = {"canny", "softedge", "scribble", "lineart", "lineart_anime", "depth", "normalbae"}
NODE_REQUIRED_PREPROCESSORS = {"openpose", "tile"}

PREPROCESSOR_ALIASES = {
    "normal": "normalbae",
    "normal_bae": "normalbae",
    "normalbae": "normalbae",
    "lineart anime": "lineart_anime",
    "lineart-anime": "lineart_anime",
    "lineart_anime": "lineart_anime",
    "open_pose": "openpose",
    "dwpose": "openpose",
    "dwpse": "openpose",
    "pose": "openpose",
    "hed": "softedge",
    "pidinet": "softedge",
    "soft edge": "softedge",
    "soft-edge": "softedge",
}

CONTROLNET_MODEL_INPUT_NAMES = ("control_net_name", "controlnet_name", "model", "model_name")
CONTROLNET_MODEL_FOLDER_KEYS = ("controlnet", "controlnets")
CONTROLNET_EXTRA_MODEL_CATEGORIES = {"controlnet", "controlnets", "control_net", "control_net_models"}
COMMON_NODE_INPUT_NAMES = ("image", "control_net", "positive", "negative", "vae", "strength", "start_percent", "end_percent", "mask", "control_mask", "model", "model_patch", "patch", "model_patch_name")


def _unwrap_object_info(object_info: Mapping[str, Any] | set[str] | list[str] | tuple[str, ...] | None) -> Mapping[str, Any]:
    if not object_info:
        return {}
    if isinstance(object_info, Mapping):
        if isinstance(object_info.get("object_info"), Mapping):
            return object_info["object_info"]  # type: ignore[index]
        if isinstance(object_info.get("nodes"), Mapping):
            return object_info["nodes"]  # type: ignore[index]
        if isinstance(object_info.get("nodes"), (set, list, tuple)):
            return {str(name): {} for name in object_info.get("nodes") or []}
        return object_info
    if isinstance(object_info, (set, list, tuple)):
        return {str(name): {} for name in object_info}
    return {}


def _node_names(object_info: Mapping[str, Any] | set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    return {str(key) for key in _unwrap_object_info(object_info).keys()}


def _node_entry(object_info: Mapping[str, Any] | set[str] | list[str] | tuple[str, ...] | None, node_name: str | None) -> Mapping[str, Any]:
    if not node_name:
        return {}
    nodes = _unwrap_object_info(object_info)
    entry = nodes.get(node_name) if isinstance(nodes, Mapping) else None
    return entry if isinstance(entry, Mapping) else {}


def _node_inputs(object_info: Mapping[str, Any] | None, node_name: str | None) -> dict[str, dict[str, Any]]:
    entry = _node_entry(object_info, node_name)
    input_block = entry.get("input") if isinstance(entry.get("input"), Mapping) else {}
    return {
        "required": dict(input_block.get("required") or {}) if isinstance(input_block.get("required"), Mapping) else {},
        "optional": dict(input_block.get("optional") or {}) if isinstance(input_block.get("optional"), Mapping) else {},
        "hidden": dict(input_block.get("hidden") or {}) if isinstance(input_block.get("hidden"), Mapping) else {},
    }


def _node_outputs(object_info: Mapping[str, Any] | None, node_name: str | None) -> list[str]:
    entry = _node_entry(object_info, node_name)
    outputs = entry.get("output") or entry.get("outputs") or []
    if isinstance(outputs, (list, tuple)):
        return [str(item) for item in outputs]
    return []


def _extract_option_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item) for item in first]
    return []


def _model_options(object_info: Mapping[str, Any] | None, loader_node: str | None) -> dict[str, list[str]]:
    inputs = _node_inputs(object_info, loader_node)
    merged = {**inputs["optional"], **inputs["required"]}
    models: dict[str, list[str]] = {}
    for input_name in CONTROLNET_MODEL_INPUT_NAMES:
        options = _extract_option_list(merged.get(input_name))
        if options:
            models[input_name] = options
    return models


def _dedupe_model_names(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        name = str(value or "").strip().replace("\\", "/")
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _registered_controlnet_models(backend_details: Mapping[str, Any] | None) -> list[str]:
    details = backend_details if isinstance(backend_details, Mapping) else {}
    folders = details.get("comfy_model_folders") if isinstance(details.get("comfy_model_folders"), Mapping) else {}
    values: list[str] = []
    for key in CONTROLNET_MODEL_FOLDER_KEYS:
        rows = folders.get(key) or []
        if isinstance(rows, (list, tuple)):
            values.extend(str(item) for item in rows)
    return _dedupe_model_names(values)


def discover_controlnet_model_catalog(backend_details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a transient ControlNet catalog from Comfy's real model sources."""

    details = backend_details if isinstance(backend_details, Mapping) else {}
    registered = _registered_controlnet_models(details)
    filesystem = discover_comfy_model_files(
        details,
        folder_names=("controlnet",),
        extra_model_categories=CONTROLNET_EXTRA_MODEL_CATEGORIES,
    )
    filesystem_sources = filesystem.get("sources") if isinstance(filesystem.get("sources"), Mapping) else {}
    diagnostics = filesystem.get("diagnostics") if isinstance(filesystem.get("diagnostics"), Mapping) else {}
    registered_diagnostics = details.get("comfy_model_folder_diagnostics") if isinstance(details.get("comfy_model_folder_diagnostics"), Mapping) else {}
    return {
        "models": _dedupe_model_names(registered + list(filesystem.get("models") or [])),
        "sources": {
            "comfy_model_folders": registered,
            "models_root": list(filesystem_sources.get("models_root") or []),
            "extra_model_paths": list(filesystem_sources.get("extra_model_paths") or []),
        },
        "diagnostics": {
            "schema_version": "neo.image.controlnet.model_catalog.v1",
            "path_policy": "absolute_paths_server_side_only",
            "registered_file_count": len(registered),
            "registered_folders": dict(registered_diagnostics),
            "filesystem": dict(diagnostics),
        },
    }


def _merge_controlnet_model_inputs(
    live_inputs: dict[str, list[str]],
    catalog_models: list[str],
) -> dict[str, list[str]]:
    merged = {
        str(key): _dedupe_model_names([str(item) for item in values])
        for key, values in live_inputs.items()
        if isinstance(values, list)
    }
    target_key = next((key for key in CONTROLNET_MODEL_INPUT_NAMES if key in merged), "control_net_name")
    live_values = merged.get(target_key) or []
    merged[target_key] = _dedupe_model_names(live_values + catalog_models)
    return merged


def _schema_for_node(object_info: Mapping[str, Any] | None, node_name: str | None) -> dict[str, Any]:
    if not node_name:
        return {}
    inputs = _node_inputs(object_info, node_name)
    all_inputs = set(inputs["required"]) | set(inputs["optional"]) | set(inputs["hidden"])
    interesting_inputs = sorted(input_name for input_name in all_inputs if input_name in COMMON_NODE_INPUT_NAMES or "control" in input_name.lower())
    return {
        "node": node_name,
        "required_inputs": sorted(inputs["required"].keys()),
        "optional_inputs": sorted(inputs["optional"].keys()),
        "hidden_inputs": sorted(inputs["hidden"].keys()),
        "interesting_inputs": interesting_inputs,
        "outputs": _node_outputs(object_info, node_name),
    }


def _first_present(names: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((node for node in candidates if node in names), None)


def resolve_preprocessor_group(preprocessor: str | None, *, unit: str | None = None) -> str:
    raw = str(preprocessor or unit or "").strip().lower()
    raw = raw.replace(" ", "_")
    if raw in PREPROCESSOR_CANDIDATES:
        return raw
    if raw in PREPROCESSOR_ALIASES:
        return PREPROCESSOR_ALIASES[raw]
    for group, candidates in PREPROCESSOR_CANDIDATES.items():
        lowered_candidates = {candidate.lower() for candidate in candidates}
        if raw in lowered_candidates:
            return group
    fallback = str(unit or "").strip().lower().replace(" ", "_")
    return PREPROCESSOR_ALIASES.get(fallback, fallback)


def preprocessor_status(preprocessor: str | None, node_status: Mapping[str, Any], *, unit: str | None = None) -> dict[str, Any]:
    group = resolve_preprocessor_group(preprocessor, unit=unit)
    if group not in PREPROCESSOR_CANDIDATES:
        return {"group": group, "state": UNSUPPORTED, "node": None, "reason": "Unknown ControlNet preprocessor group."}
    nodes = ((node_status.get("preprocessors") or {}).get(group) or []) if isinstance(node_status.get("preprocessors"), Mapping) else []
    if nodes:
        return {"group": group, "state": AVAILABLE, "backend": "comfy_preprocessor", "node": nodes[0], "available_nodes": list(nodes)}
    if group in LOCAL_FALLBACK_PREPROCESSORS:
        return {
            "group": group,
            "state": EXPERIMENTAL_AVAILABLE,
            "backend": "local_fallback_declared",
            "node": None,
            "reason": "No Comfy preprocessor node detected; V1 parity declares a local fallback path for this map type.",
        }
    return {
        "group": group,
        "state": PROVIDER_GATED,
        "backend": "comfy_preprocessor_required",
        "node": None,
        "reason": "This ControlNet map type requires an installed Comfy/custom preprocessor node.",
    }


def inspect_nodes(
    object_info: Mapping[str, Any] | set[str] | list[str] | tuple[str, ...] | None = None,
    *,
    backend_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    names = _node_names(object_info)
    loader = _first_present(names, LOADER_CANDIDATES)
    standard_apply = _first_present(names, STANDARD_APPLY_CANDIDATES)
    advanced = _first_present(names, ADVANCED_APPLY_CANDIDATES)
    qwen_diffsynth_patch_loader = _first_present(names, QWEN_DIFFSYNTH_PATCH_LOADER_CANDIDATES)
    qwen_diffsynth_apply = _first_present(names, QWEN_DIFFSYNTH_APPLY_CANDIDATES)
    qwen_instantx_loader = _first_present(names, QWEN_INSTANTX_LOADER_CANDIDATES)
    qwen_instantx_apply = _first_present(names, QWEN_INSTANTX_APPLY_CANDIDATES) or advanced or standard_apply
    flux_loader = _first_present(names, FLUX_INPAINT_LOADER_CANDIDATES) or loader
    flux_apply = _first_present(names, FLUX_INPAINT_APPLY_CANDIDATES) or advanced or standard_apply
    flux2_klein_loader = _first_present(names, FLUX2_KLEIN_FUN_UNION_LOADER_CANDIDATES) or flux_loader
    flux2_klein_apply = _first_present(names, FLUX2_KLEIN_FUN_UNION_APPLY_CANDIDATES) or flux_apply
    z_image_patch_loader = _first_present(names, Z_IMAGE_FUN_UNION_PATCH_LOADER_CANDIDATES)
    z_image_apply = _first_present(names, Z_IMAGE_FUN_UNION_APPLY_CANDIDATES) or advanced or standard_apply
    apply_node = advanced or standard_apply
    missing_base: list[str] = []
    if loader is None:
        missing_base.append("ControlNetLoader")
    if apply_node is None:
        missing_base.append("ControlNetApply or ControlNetApplyAdvanced")

    preprocessors = {
        group: [node for node in candidates if node in names]
        for group, candidates in PREPROCESSOR_CANDIDATES.items()
    }
    preprocessor_states = {
        group: preprocessor_status(group, {"preprocessors": preprocessors})
        for group in PREPROCESSOR_CANDIDATES
    }
    gated_preprocessors = [group for group, status in preprocessor_states.items() if status["state"] == PROVIDER_GATED]
    live_model_inputs = _model_options(object_info, loader)
    model_catalog = discover_controlnet_model_catalog(backend_details)
    model_inputs = _merge_controlnet_model_inputs(live_model_inputs, list(model_catalog.get("models") or []))

    return {
        "base_available": not missing_base,
        "loader_available": loader is not None,
        "loader_node": loader,
        "apply_available": apply_node is not None,
        "apply_node": apply_node,
        "standard_apply_available": standard_apply is not None,
        "standard_apply_node": standard_apply,
        "advanced_available": advanced is not None,
        "advanced_node": advanced,
        "preprocessors": preprocessors,
        "preprocessor_states": preprocessor_states,
        "gated_preprocessors": gated_preprocessors,
        "model_inputs": model_inputs,
        "model_input_sources": {
            "object_info": live_model_inputs,
            **dict(model_catalog.get("sources") or {}),
        },
        "model_catalog_diagnostics": dict(model_catalog.get("diagnostics") or {}),
        "input_schemas": {
            "loader": _schema_for_node(object_info, loader),
            "apply": _schema_for_node(object_info, apply_node),
            "standard_apply": _schema_for_node(object_info, standard_apply),
            "advanced_apply": _schema_for_node(object_info, advanced),
            "qwen_diffsynth_patch_loader": _schema_for_node(object_info, qwen_diffsynth_patch_loader),
            "qwen_diffsynth_apply": _schema_for_node(object_info, qwen_diffsynth_apply),
            "qwen_instantx_loader": _schema_for_node(object_info, qwen_instantx_loader),
            "qwen_instantx_apply": _schema_for_node(object_info, qwen_instantx_apply),
            "flux_loader": _schema_for_node(object_info, flux_loader),
            "flux_apply": _schema_for_node(object_info, flux_apply),
            "flux2_klein_loader": _schema_for_node(object_info, flux2_klein_loader),
            "flux2_klein_apply": _schema_for_node(object_info, flux2_klein_apply),
            "z_image_patch_loader": _schema_for_node(object_info, z_image_patch_loader),
            "z_image_apply": _schema_for_node(object_info, z_image_apply),
        },
        "flux": {
            "loader_node": flux_loader,
            "apply_node": flux_apply,
            "available": bool(flux_loader and flux_apply),
        },
        "flux2_klein": {
            "loader_node": flux2_klein_loader,
            "apply_node": flux2_klein_apply,
            "available": bool(flux2_klein_loader and flux2_klein_apply),
        },
        "qwen": {
            "diffsynth_patch_loader_node": qwen_diffsynth_patch_loader,
            "diffsynth_apply_node": qwen_diffsynth_apply,
            "diffsynth_available": bool(qwen_diffsynth_patch_loader and qwen_diffsynth_apply),
            "instantx_loader_node": qwen_instantx_loader,
            "instantx_apply_node": qwen_instantx_apply,
            "instantx_available": bool(qwen_instantx_loader and qwen_instantx_apply),
        },
        "z_image": {
            "patch_loader_node": z_image_patch_loader,
            "apply_node": z_image_apply,
            "available": bool(z_image_patch_loader and z_image_apply),
            "model_dir": "model_patches",
        },
        "missing": missing_base,
        "provider_gated": bool(missing_base),
        "object_info_present": bool(names),
    }
