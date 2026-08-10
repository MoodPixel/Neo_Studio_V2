from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

SCHEMA_ID = "neo.image.family_compatibility.v1"
PHASE7_STATE = "family_by_family_compatibility_hardened"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "models" / "model_family_manifest.json"
MASKED_MODES = {"inpaint", "outpaint"}
BACKEND_CLOWNSHARK = "res4lyf_clownshark"

# These compilers have a compiler-owned, standard Comfy KSampler anchor. Phase 4-6
# Multi-KSampler and Phase 5 RES4LYF are permitted only on this shape. Route-native
# custom sampler systems must receive dedicated adapters instead of being guessed.
CORE_KSAMPLER_COMPILERS = {
    "comfy.checkpoint_sd",
    "comfy.flux_native",
    "comfy.flux_fill",
    "comfy.flux_krea",
    "comfy.flux_gguf",
    "comfy.flux_gguf.krea",
    "comfy.flux_klein",
    "comfy.flux_gguf.klein",
    "comfy.krea2",
    "comfy.krea2_gguf",
    "comfy.qwen_native",
    "comfy.qwen_native_edit",
    "comfy.qwen_rapid_aio_checkpoint",
    "comfy.qwen_gguf",
    "comfy.z_image_native",
    "comfy.z_image_gguf",
    "comfy.hidream_native",
    "comfy.hidream_gguf",
    "comfy.anima_native",
    "comfy.anima_gguf",
}

CUSTOM_ADVANCED_COMPILERS = {
    "comfy.ideogram4_native",
    "comfy.ideogram4_gguf",
}

LANPAINT_COMPILER = "comfy.lanpaint.family_aware.v1"

# Manifest values that describe an executable route. The exact token still carries
# useful UX meaning (native, experimental, mmproj-required, etc.).
EXECUTABLE_MANIFEST_STATES = {
    "available",
    "native",
    "experimental",
    "experimental_available",
    "experimental_source_latent",
    "experimental_single_source_mask",
    "experimental_single_source_canvas",
    "single_source_native",
    "single_source_native_mask",
    "single_source_native_canvas",
    "multi_source_native",
    "native_source_latent",
    "native_single_source_mask",
    "native_single_source_canvas",
    "native_turbo",
    "turbo_native_source_latent",
    "turbo_native_single_source_mask",
    "turbo_native_single_source_canvas",
    "mmproj_required",
    "single_source_mmproj_required",
    "multi_source_mmproj_required",
    "lanpaint",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _family_map() -> dict[str, dict[str, Any]]:
    return {
        str(row.get("family_id") or ""): row
        for row in (_manifest().get("families") or [])
        if isinstance(row, Mapping) and row.get("family_id")
    }


def family_manifest_state(family: str, loader: str, mode: str) -> str:
    row = _family_map().get(str(family or ""), {})
    loader_states = _mapping(_mapping(row.get("loader_mode_support")).get(loader))
    value = loader_states.get(mode)
    if value is None:
        value = _mapping(row.get("mode_support")).get(mode)
    return str(value or "unsupported")


def manifest_state_executable(value: Any) -> bool:
    return str(value or "").strip().lower() in EXECUTABLE_MANIFEST_STATES


def sampler_architecture(compiler_id: str | None, engine: str = "native") -> str:
    compiler = str(compiler_id or "")
    if engine == "lanpaint" or compiler == LANPAINT_COMPILER:
        return "lanpaint_route_native"
    if compiler in CORE_KSAMPLER_COMPILERS:
        return "core_ksampler"
    if compiler in CUSTOM_ADVANCED_COMPILERS:
        return "custom_advanced"
    if not compiler:
        return "none"
    return "unknown"


def _feature(state: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"state": state, "available": state == "available", "reason": reason, **extra}


def _route_for(*, provider_id: str, family: str, loader: str, mode: str, engine: str) -> Any:
    # Local imports avoid an image module -> providers package -> ComfyProvider cycle.
    from neo_app.providers.compile_router import select_comfy_compile_route
    from neo_app.providers.schema import NeoJob

    params: dict[str, Any] = {}
    if mode in MASKED_MODES:
        params["masked_edit_engine"] = engine
        params["inpaint_engine"] = engine
    job = NeoJob(
        surface="image",
        subtab="generate",
        provider_id=provider_id,
        family=family,
        loader=loader,
        mode=mode,
        prompt="compatibility probe",
        params=params,
    )
    return select_comfy_compile_route(job)


def route_family_compatibility(
    *,
    provider_id: str,
    family: str,
    loader: str,
    mode: str,
    engine: str = "native",
    object_info: Mapping[str, Any] | None = None,
    backend_reachable: bool = True,
) -> dict[str, Any]:
    family = str(family or "").strip()
    loader = str(loader or "").strip()
    mode = str(mode or "txt2img").strip()
    engine = "lanpaint" if str(engine or "native").strip().lower() == "lanpaint" else "native"
    route = _route_for(provider_id=provider_id, family=family, loader=loader, mode=mode, engine=engine)
    route_available = bool(route.can_compile)
    compiler_id = str(route.compiler_id or "")
    architecture = sampler_architecture(compiler_id, engine)
    manifest_state = family_manifest_state(family, loader, mode)
    info = _mapping(object_info)
    live_known = bool(info)
    if live_known:
        from neo_app.providers.comfy_workflows.res4lyf_sampler import inspect_res4lyf_sampler
        res_diag = inspect_res4lyf_sampler(info)
    else:
        res_diag = {}

    parameter_truth = _feature(
        "available" if route_available else "gated",
        "Explicit user Parameters remain authoritative on this executable route."
        if route_available else (route.blockers[0] if route.blockers else "No executable compiler route."),
    )
    parameter_integrity = _feature(
        "available" if route_available else "gated",
        "Final local Comfy graph values are verified before /prompt."
        if route_available else "No executable local graph exists to verify.",
    )

    core_ksampler = route_available and architecture == "core_ksampler" and engine == "native"
    multi = _feature(
        "available" if core_ksampler else "gated",
        "Compiler owns a standard KSampler anchor suitable for sequential latent refinement."
        if core_ksampler else (
            "LanPaint owns a route-native sampler graph; Multi-KSampler requires a dedicated LanPaint adapter."
            if architecture == "lanpaint_route_native"
            else "This route does not expose a standard compiler-owned KSampler anchor."
        ),
        sampler_architecture=architecture,
    )

    if not core_ksampler:
        res = _feature("gated", multi["reason"], sampler_architecture=architecture)
    elif not live_known:
        res = _feature("requires_discovery", "Connect/Test ComfyUI to verify the installed RES4LYF ClownsharKSampler node signature.", sampler_architecture=architecture)
    elif not res_diag.get("installed"):
        res = _feature("missing_dependency", "RES4LYF / ClownsharKSampler is not installed on this ComfyUI backend.", sampler_architecture=architecture)
    elif not res_diag.get("compatible_signature"):
        res = _feature("incompatible_dependency", "Installed ClownsharKSampler does not expose the required Neo sampler inputs.", sampler_architecture=architecture)
    else:
        res = _feature("available", "Live RES4LYF ClownsharKSampler signature is compatible with this core-KSampler route.", sampler_architecture=architecture)

    if not core_ksampler:
        latent_upscale = _feature("gated", multi["reason"], sampler_architecture=architecture)
    elif not live_known:
        latent_upscale = _feature("requires_discovery", "Connect/Test ComfyUI to verify the core LatentUpscaleBy node before using an inter-stage upscale.")
    elif "LatentUpscaleBy" not in info:
        latent_upscale = _feature("missing_core_node", "The connected ComfyUI backend does not expose core LatentUpscaleBy.")
    else:
        latent_upscale = _feature("available", "ComfyUI core LatentUpscaleBy is available for inter-stage refinement.")

    native_masked = route_available and engine == "native" and mode in MASKED_MODES
    native_masked_feature = _feature(
        "available" if native_masked else "gated",
        "Native masked workflow has an executable family compiler route."
        if native_masked else (
            "This family/mode is LanPaint-only or otherwise has no verified Native masked compiler."
            if mode in MASKED_MODES else "Native masked editing applies only to Inpaint/Outpaint."
        ),
    )
    lanpaint_feature = _feature(
        "available" if route_available and engine == "lanpaint" else "gated",
        "Exact family/loader LanPaint adapter is bound for this masked mode."
        if route_available and engine == "lanpaint" else (
            route.blockers[0] if route.blockers else "No exact LanPaint adapter is bound for this route."
        ),
    )

    if mode not in MASKED_MODES or engine != "native" or not native_masked:
        crop_native = _feature("gated", "Native Crop & Stitch requires an executable Native Inpaint/Outpaint route.")
    elif not live_known:
        crop_native = _feature("requires_discovery", "Connect/Test ComfyUI to verify InpaintCropImproved + InpaintStitchImproved.")
    elif not all(name in info for name in ("InpaintCropImproved", "InpaintStitchImproved")):
        crop_native = _feature("missing_dependency", "Install ComfyUI-Inpaint-CropAndStitch (InpaintCropImproved + InpaintStitchImproved).")
    else:
        crop_native = _feature("available", "ComfyUI-Inpaint-CropAndStitch is available for the Native masked route.")

    crop_lanpaint = _feature(
        "available" if route_available and engine == "lanpaint" and mode in MASKED_MODES else "gated",
        "LanPaint uses its family-owned crop/restore/stitch path when the toggle is enabled."
        if route_available and engine == "lanpaint" and mode in MASKED_MODES else "No executable LanPaint masked route is selected.",
    )

    return {
        "schema": SCHEMA_ID,
        "phase7_state": PHASE7_STATE,
        "key": f"{family}:{loader}:{mode}:{engine}",
        "provider_id": provider_id,
        "family": family,
        "loader": loader,
        "mode": mode,
        "engine": engine,
        "manifest_state": manifest_state,
        "manifest_executable": manifest_state_executable(manifest_state),
        "route": route.as_dict(),
        "route_available": route_available,
        "compiler_id": compiler_id,
        "sampler_architecture": architecture,
        "backend_reachable": bool(backend_reachable),
        "live_object_info_checked": live_known,
        "features": {
            "parameter_truth": parameter_truth,
            "parameter_integrity": parameter_integrity,
            "native_masked_edit": native_masked_feature,
            "lanpaint": lanpaint_feature,
            "native_crop_stitch": crop_native,
            "lanpaint_crop_stitch": crop_lanpaint,
            "multi_ksampler": multi,
            "res4lyf_clownshark": res,
            "inter_stage_latent_upscale": latent_upscale,
        },
    }


def build_image_family_compatibility_matrix(
    *,
    provider_id: str,
    object_info: Mapping[str, Any] | None = None,
    backend_reachable: bool = True,
) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for family_id, family in _family_map().items():
        if "image" not in (family.get("surfaces") or []):
            continue
        loaders = list(family.get("supported_loaders") or [])
        modes: set[str] = set(family.get("supported_modes") or [])
        for loader_modes in _mapping(family.get("loader_mode_support")).values():
            modes.update(_mapping(loader_modes).keys())
        modes.intersection_update({"txt2img", "img2img", "inpaint", "outpaint", "edit"})
        for loader in loaders:
            for mode in sorted(modes):
                engines = ("native", "lanpaint") if mode in MASKED_MODES else ("native",)
                for engine in engines:
                    entry = route_family_compatibility(
                        provider_id=provider_id,
                        family=family_id,
                        loader=loader,
                        mode=mode,
                        engine=engine,
                        object_info=object_info,
                        backend_reachable=backend_reachable,
                    )
                    entries[entry["key"]] = entry
    return {
        "schema": SCHEMA_ID,
        "phase7_state": PHASE7_STATE,
        "provider_id": provider_id,
        "backend_reachable": bool(backend_reachable),
        "entry_count": len(entries),
        "entries": entries,
    }


def _multi_requested(params: Mapping[str, Any] | None) -> bool:
    return bool(_mapping(_mapping(params).get("multi_ksampler")).get("enabled"))


def _latent_upscale_requested(params: Mapping[str, Any] | None) -> bool:
    multi = _mapping(_mapping(params).get("multi_ksampler"))
    if not bool(multi.get("enabled")):
        return False
    try:
        count = int(multi.get("stage_count", 2))
    except (TypeError, ValueError):
        count = 2
    for stage in range(2, min(max(count, 2), 3) + 1):
        row = _mapping(multi.get(f"stage{stage}"))
        transition = _mapping(row.get("transition"))
        operation = str(transition.get("operation") or "none").strip().lower().replace("-", "_")
        if operation in {"latent_upscale", "latentupscale", "upscale", "latent"}:
            return True
    return False


def validate_image_feature_request(
    job: Any,
    *,
    object_info: Mapping[str, Any] | None = None,
    backend_reachable: bool = True,
) -> dict[str, Any]:
    surface = str(getattr(job, "surface", "image") or "image").strip().lower()
    if surface != "image":
        return {
            "schema": SCHEMA_ID,
            "phase7_state": PHASE7_STATE,
            "ok": True,
            "errors": [],
            "warnings": [],
            "compatibility": {
                "schema": SCHEMA_ID,
                "phase7_state": PHASE7_STATE,
                "state": "not_applicable",
                "surface": surface,
                "reason": "Image family compatibility hardening applies only to the Image surface.",
            },
        }
    params = _mapping(job.params)
    mode = str(job.mode or "txt2img").strip()
    engine = "lanpaint" if mode in MASKED_MODES and str(params.get("masked_edit_engine") or params.get("inpaint_engine") or "native").strip().lower() == "lanpaint" else "native"
    entry = route_family_compatibility(
        provider_id=job.provider_id,
        family=str(job.family or "sdxl"),
        loader=str(job.loader or "checkpoint"),
        mode=mode,
        engine=engine,
        object_info=object_info,
        backend_reachable=backend_reachable,
    )
    errors: list[str] = []
    warnings: list[str] = []
    features = _mapping(entry.get("features"))

    if not entry.get("route_available"):
        route = _mapping(entry.get("route"))
        blockers = list(route.get("blockers") or [])
        reason = str(blockers[0] if blockers else "No executable Comfy compiler route is bound.")
        errors.append(
            f"Selected Image route {entry.get('family')} + {entry.get('loader')} + {entry.get('mode')} + {entry.get('engine')} is not executable: {reason}"
        )

    if _multi_requested(params) and not _mapping(features.get("multi_ksampler")).get("available"):
        errors.append("Multi-KSampler is not compatible with this family/loader/mode/engine: " + str(_mapping(features.get("multi_ksampler")).get("reason") or "unsupported sampler architecture"))
    from neo_app.providers.comfy_workflows.res4lyf_sampler import res4lyf_sampler_requested
    if res4lyf_sampler_requested(params):
        res = _mapping(features.get("res4lyf_clownshark"))
        if res.get("state") not in {"available", "requires_discovery"}:
            errors.append("ClownsharKSampler is not compatible with this route: " + str(res.get("reason") or "unsupported sampler architecture"))
        elif object_info is not None and not res.get("available"):
            errors.append("ClownsharKSampler cannot run on the connected backend: " + str(res.get("reason") or "RES4LYF discovery failed"))
    if _latent_upscale_requested(params):
        up = _mapping(features.get("inter_stage_latent_upscale"))
        if up.get("state") not in {"available", "requires_discovery"}:
            errors.append("Multi-KSampler latent upscale is not compatible with this route: " + str(up.get("reason") or "LatentUpscaleBy unavailable"))
        elif object_info is not None and not up.get("available"):
            errors.append("Multi-KSampler latent upscale cannot run on the connected backend: " + str(up.get("reason") or "LatentUpscaleBy unavailable"))
    if mode in MASKED_MODES and engine == "native" and bool(params.get("crop_stitch_enabled") or params.get("masked_crop_stitch_enabled")):
        crop = _mapping(features.get("native_crop_stitch"))
        if crop.get("state") not in {"available", "requires_discovery"}:
            errors.append("Native Crop & Stitch is not compatible with this route: " + str(crop.get("reason") or "unsupported"))
        elif object_info is not None and not crop.get("available"):
            errors.append("Native Crop & Stitch cannot run on the connected backend: " + str(crop.get("reason") or "missing custom nodes"))

    # A route that the runtime can compile but the model manifest still marks as
    # non-executable is configuration drift. Surface it loudly; Phase 7 tests keep
    # shipped routes free of this drift.
    if entry.get("route_available") and not entry.get("manifest_executable"):
        warnings.append(
            f"Family manifest drift detected for {entry.get('family')}+{entry.get('loader')}+{entry.get('mode')}: "
            f"manifest_state={entry.get('manifest_state')} but compiler route is available."
        )

    return {
        "schema": SCHEMA_ID,
        "phase7_state": PHASE7_STATE,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "compatibility": deepcopy(entry),
    }


__all__ = [
    "SCHEMA_ID",
    "PHASE7_STATE",
    "CORE_KSAMPLER_COMPILERS",
    "EXECUTABLE_MANIFEST_STATES",
    "family_manifest_state",
    "manifest_state_executable",
    "sampler_architecture",
    "route_family_compatibility",
    "build_image_family_compatibility_matrix",
    "validate_image_feature_request",
]
