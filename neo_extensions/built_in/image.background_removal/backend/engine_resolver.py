from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any

from .constants import NATIVE_PRESET_MODELS, PRESET_MODEL_CANDIDATES
from .public_hygiene import portable_model_identifiers


ENGINE_CATALOG_SCHEMA_ID = "neo.image.background_removal.engine_catalog.v1"
ENGINE_CATALOG_SCHEMA_VERSION = 1
ENGINE_IDS = (
    "smart",
    "comfy_birefnet",
    "comfy_rmbg",
    "native_rembg",
    "comfy_sam",
    "native_sam",
    "commercial_api",
    "comfy_matting",
)
FALLBACK_POLICIES = {
    "never": {
        "label": "Never fallback",
        "allows_unavailable": False,
        "allows_queue_failure": False,
    },
    "on_unavailable": {
        "label": "Fallback when unavailable",
        "allows_unavailable": True,
        "allows_queue_failure": False,
    },
    "on_unavailable_or_queue_failure": {
        "label": "Fallback when unavailable or after queue failure",
        "allows_unavailable": True,
        "allows_queue_failure": True,
    },
}
_ABSOLUTE_PATH_FRAGMENT = re.compile(r"(?:[A-Za-z]:[/\\]|/(?:Users|home|root|workspace|mnt|opt)/|\\\\)", re.IGNORECASE)


@dataclass(frozen=True)
class EngineResolution:
    requested_engine: str
    resolved_engine: str
    preset: str
    model: str
    fallback_used: bool
    fallback_reason: str
    ready: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["errors"] = list(self.errors)
        return data


def _catalog_models(values: Any, role: str) -> list[str]:
    return portable_model_identifiers(values if isinstance(values, (list, tuple, set)) else [], role)


def _engine_row(
    engine_id: str,
    *,
    label: str,
    backend: str,
    workflows: tuple[str, ...],
    available: bool,
    status: str,
    blockers: list[str] | tuple[str, ...] = (),
    models: list[str] | tuple[str, ...] = (),
    fallback_targets: tuple[str, ...] = (),
    external_upload: bool = False,
    profile_required: bool = False,
) -> dict[str, Any]:
    safe_blockers = []
    for item in blockers:
        text = str(item).strip()
        if not text:
            continue
        if _ABSOLUTE_PATH_FRAGMENT.search(text):
            text = "The engine reported a server-side runtime error; inspect the server log for details."
        if text not in safe_blockers:
            safe_blockers.append(text)
    return {
        "id": engine_id,
        "label": label,
        "backend": backend,
        "workflows": list(workflows),
        "available": bool(available),
        "status": status,
        "blockers": safe_blockers,
        "models": list(models),
        "fallback_targets": list(fallback_targets),
        "external_upload": bool(external_upload),
        "profile_required": bool(profile_required),
        "path_policy": "portable_identifiers_only",
    }


def build_engine_catalog(
    *,
    comfy_catalog: dict[str, Any] | None = None,
    native_status: dict[str, Any] | None = None,
    commercial_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one public readiness catalog for every background-removal engine.

    This is a discovery/readiness contract. It does not load models, mutate a
    workflow, or choose a route for a specific request. The execution route
    still revalidates the selected engine immediately before running.
    """

    comfy = comfy_catalog if isinstance(comfy_catalog, dict) else {}
    native = native_status if isinstance(native_status, dict) else {}
    birefnet_models = _catalog_models(comfy.get("models"), "birefnet")
    rmbg_node = comfy.get("rmbg_node") if isinstance(comfy.get("rmbg_node"), dict) else {}
    rmbg_models = _catalog_models(comfy.get("rmbg_models") or rmbg_node.get("model_choices"), "rmbg")
    shared_sam = comfy.get("shared_sam") if isinstance(comfy.get("shared_sam"), dict) else {}
    region_catalog = comfy.get("region_segmentation") if isinstance(comfy.get("region_segmentation"), dict) else {}
    region_adapters = [row for row in (region_catalog.get("adapters") or []) if isinstance(row, dict)]
    region_ready = any(bool(row.get("available")) for row in region_adapters)
    region_blockers = [] if region_ready else ["No verified face, clothes, fashion, or accessories segmentation adapter is available."]
    utility_catalog = comfy.get("mask_utilities") if isinstance(comfy.get("mask_utilities"), dict) else {}
    utility_rows = [row for row in (utility_catalog.get("operations") or []) if isinstance(row, dict)]
    utility_ready = any(bool(row.get("available")) for row in utility_rows)
    utility_blockers = [] if utility_ready else ["No verified RMBG mask or object utility is available."]
    matting_catalog = comfy.get("matting") if isinstance(comfy.get("matting"), dict) else {}
    matting_profiles = [row for row in (matting_catalog.get("profiles") or []) if isinstance(row, dict)]
    matting_ready = any(bool(row.get("available")) for row in matting_profiles)
    matting_blockers = [] if matting_ready else ["No verified advanced matting profile is available."]
    sam_models = _catalog_models(shared_sam.get("models"), "sam")
    comfy_ready = bool(comfy.get("nodes_ready") and birefnet_models)
    rmbg_ready = bool(rmbg_node.get("available") and rmbg_models)
    rmbg_blockers = [str(item) for item in (rmbg_node.get("blockers") or []) if str(item).strip()]
    if not comfy.get("object_info_available") and not rmbg_blockers:
        rmbg_blockers.append("Live ComfyUI /object_info is unavailable.")
    comfy_blockers: list[str] = []
    if not comfy.get("object_info_available"):
        comfy_blockers.append("Live ComfyUI /object_info is unavailable.")
    elif not comfy.get("nodes_ready"):
        comfy_blockers.extend([f"Missing Comfy BiRefNet node: {item}" for item in (comfy.get("missing_nodes") or [])])
    if not birefnet_models:
        comfy_blockers.append("No compatible BiRefNet model was discovered.")

    native_models = _catalog_models(native.get("models"), "native")
    native_ready = bool(native.get("available"))
    native_blockers = [] if native_ready else [str(native.get("reason") or "Native rembg is unavailable.")]
    native_sam = native.get("interactive_sam") if isinstance(native.get("interactive_sam"), dict) else {}
    native_sam_ready = bool(native_ready and native_sam.get("available") is not False)
    native_sam_blockers = [] if native_sam_ready else [str(native_sam.get("reason") or native.get("reason") or "Native ONNX SAM is unavailable.")]

    comfy_sam_ready = bool(shared_sam.get("ready") and sam_models)
    comfy_sam_blockers = [] if comfy_sam_ready else [
        str(shared_sam.get("reuse_note") or "Comfy Impact Pack SAM is unavailable.")
    ]
    if shared_sam.get("missing_nodes"):
        comfy_sam_blockers = ["Missing Comfy SAM node(s): " + ", ".join(str(item) for item in shared_sam.get("missing_nodes") or [])]
    if not sam_models:
        comfy_sam_blockers.append("No compatible Comfy SAM model was discovered.")

    commercial = commercial_status if isinstance(commercial_status, dict) else {}
    commercial_available = bool(commercial.get("available"))
    commercial_status_name = str(commercial.get("status") or ("available" if commercial_available else "profile_required"))
    commercial_blockers = list(commercial.get("blockers") or [])
    if not commercial_available and not commercial_blockers:
        commercial_blockers = ["Select an enabled commercial Background Removal profile."]
    commercial_models = [str(commercial.get("provider_id") or "")] if commercial.get("provider_id") else []

    rows = [
        _engine_row(
            "comfy_birefnet",
            label="Comfy BiRefNet",
            backend="comfyui",
            workflows=("segment", "refine_mask"),
            available=comfy_ready,
            status="available" if comfy_ready else ("catalog_unavailable" if not comfy.get("object_info_available") else "blocked"),
            blockers=comfy_blockers,
            models=birefnet_models,
            fallback_targets=("native_rembg",),
        ),
        _engine_row(
            "comfy_rmbg",
            label="ComfyUI-RMBG · RMBG Node",
            backend="comfyui",
            workflows=("segment",),
            available=rmbg_ready,
            status="available" if rmbg_ready else ("catalog_unavailable" if not comfy.get("object_info_available") else "blocked"),
            blockers=rmbg_blockers or ([] if rmbg_ready else ["No compatible ComfyUI-RMBG model was exposed by the live RMBG node."]),
            models=rmbg_models,
            fallback_targets=("comfy_birefnet", "native_rembg"),
        ),
        _engine_row(
            "native_rembg",
            label="Neo Native rembg",
            backend="neo_native",
            workflows=("segment",),
            available=native_ready,
            status="available" if native_ready else "blocked",
            blockers=native_blockers,
            models=native_models,
            fallback_targets=("comfy_birefnet",),
        ),
        _engine_row(
            "comfy_sam",
            label="Comfy Impact Pack SAM",
            backend="comfyui",
            workflows=("interactive_sam",),
            available=comfy_sam_ready,
            status="available" if comfy_sam_ready else "blocked",
            blockers=comfy_sam_blockers,
            models=sam_models,
            fallback_targets=("native_sam",),
        ),
        _engine_row(
            "native_sam",
            label="Neo Native ONNX SAM",
            backend="neo_native",
            workflows=("interactive_sam",),
            available=native_sam_ready,
            status="available" if native_sam_ready else "blocked",
            blockers=native_sam_blockers,
            models=[str(item) for item in (native_sam.get("variants") or []) if str(item).strip()],
            fallback_targets=("comfy_sam",),
        ),
        _engine_row(
            "commercial_api",
            label="Commercial Background Removal API",
            backend="commercial_api",
            workflows=("segment",),
            available=commercial_available,
            status=commercial_status_name,
            blockers=commercial_blockers,
            models=commercial_models,
            external_upload=True,
            profile_required=True,
        ),
        _engine_row(
            "comfy_region_segmentation",
            label="Comfy RMBG Face / Clothes / Fashion / Accessories Segmentation",
            backend="comfyui",
            workflows=("region_segmentation",),
            available=region_ready,
            status="available" if region_ready else "blocked",
            blockers=region_blockers,
            models=[str(row.get("id") or "") for row in region_adapters if row.get("available")],
        ),
        _engine_row(
            "comfy_mask_utility",
            label="Comfy RMBG Mask and Object Utilities",
            backend="comfyui",
            workflows=("mask_utility",),
            available=utility_ready,
            status="available" if utility_ready else "blocked",
            blockers=utility_blockers,
            models=[str(row.get("id") or "") for row in utility_rows if row.get("available")],
        ),
        _engine_row(
            "comfy_matting",
            label="Comfy RMBG Advanced Matting / High-Resolution Edges",
            backend="comfyui",
            workflows=("matting",),
            available=matting_ready,
            status="available" if matting_ready else "blocked",
            blockers=matting_blockers,
            models=[str(row.get("id") or "") for row in matting_profiles if row.get("available")],
        ),
    ]
    local_segment_ready = comfy_ready or native_ready
    rows.insert(
        0,
        _engine_row(
            "smart",
            label="Smart Routing",
            backend="policy",
            workflows=("segment",),
            available=local_segment_ready,
            status="available" if local_segment_ready else "blocked",
            blockers=[] if local_segment_ready else ["No local background-removal engine is ready."],
            fallback_targets=("comfy_birefnet", "comfy_rmbg", "native_rembg"),
        ),
    )
    by_id = {row["id"]: row for row in rows}
    refine_row = dict(by_id["comfy_birefnet"])
    refinement_ready = bool(comfy.get("object_info_available") and comfy.get("refinement_nodes_ready"))
    refine_row["available"] = refinement_ready
    refine_row["status"] = "available" if refinement_ready else "blocked"
    refine_row["blockers"] = [] if refinement_ready else [
        f"Missing reviewed-mask node: {item}" for item in (comfy.get("missing_refinement_nodes") or [])
    ] or ["Comfy mask refinement is unavailable."]
    return {
        "schema_id": ENGINE_CATALOG_SCHEMA_ID,
        "schema_version": ENGINE_CATALOG_SCHEMA_VERSION,
        "engines": rows,
        "by_workflow": {
            "segment": [by_id[item] for item in ("smart", "comfy_birefnet", "comfy_rmbg", "native_rembg", "commercial_api")],
            "refine_mask": [refine_row],
            "interactive_sam": [by_id["comfy_sam"], by_id["native_sam"]],
            "region_segmentation": [by_id["comfy_region_segmentation"]],
            "mask_utility": [by_id["comfy_mask_utility"]],
            "matting": [by_id["comfy_matting"]],
        },
        "fallback_policies": {key: dict(value) for key, value in FALLBACK_POLICIES.items()},
        "execution_revalidation": True,
        "path_policy": "portable_identifiers_only",
    }


def resolve_interactive_engine(
    settings: dict[str, Any],
    *,
    shared_resolution: Any,
    native_status: dict[str, Any] | None = None,
    comfy_profile_ready: bool = False,
    comfy_compatible: bool = True,
    comfy_reason: str = "",
    profile_error: str = "",
) -> EngineResolution:
    """Resolve Comfy/native SAM using the same explicit contract as segment mode."""

    native = native_status if isinstance(native_status, dict) else {}
    sam_status = native.get("interactive_sam") if isinstance(native.get("interactive_sam"), dict) else {}
    native_ready = bool(native.get("available")) and sam_status.get("available") is not False
    shared_ready = bool(getattr(shared_resolution, "ready", False))
    shared_model = str(getattr(shared_resolution, "model", "") or "")
    requested = str(settings.get("sam_execution") or "auto").strip().lower()
    if requested == "comfy_impact":
        if not comfy_profile_ready:
            return EngineResolution(requested, "", "interactive_select", "", False, "", False, (profile_error or "Shared Impact Pack SAM requires a connected ComfyUI profile.",))
        if not shared_ready:
            return EngineResolution(requested, "", "interactive_select", shared_model, False, "", False, (str(getattr(shared_resolution, "reason", "") or "Comfy SAM is unavailable."),))
        if not comfy_compatible:
            return EngineResolution(requested, "", "interactive_select", shared_model, False, "", False, (comfy_reason or "Selected subjects are not compatible with Comfy Impact SAM.",))
        return EngineResolution(requested, "comfy_sam", "interactive_select", shared_model, False, "", True, ())
    if requested == "native_onnx":
        if not native_ready:
            return EngineResolution(requested, "", "interactive_select", str(settings.get("sam_model_variant") or "sam_vit_b_01ec64"), False, "", False, (str(native.get("reason") or sam_status.get("reason") or "Neo Native ONNX SAM is unavailable."),))
        return EngineResolution(requested, "native_sam", "interactive_select", str(settings.get("sam_model_variant") or "sam_vit_b_01ec64"), False, "", True, ())
    if comfy_profile_ready and shared_ready and comfy_compatible:
        return EngineResolution(requested, "comfy_sam", "interactive_select", shared_model, False, "", True, ())
    if native_ready:
        return EngineResolution(
            requested,
            "native_sam",
            "interactive_select",
            str(settings.get("sam_model_variant") or "sam_vit_b_01ec64"),
            bool(comfy_profile_ready),
            comfy_reason if comfy_profile_ready else "",
            True,
            (),
        )
    detail = str(getattr(shared_resolution, "reason", "") or "No connected ComfyUI profile for Comfy SAM.") if comfy_profile_ready else (profile_error or "No connected ComfyUI profile for Comfy SAM.")
    return EngineResolution(
        requested,
        "",
        "interactive_select",
        "",
        False,
        "",
        False,
        (f"No Interactive SAM route is ready. Comfy SAM: {detail}; Native SAM: {native.get('reason') or sam_status.get('reason') or 'unavailable'}",),
    )


def _first_matching(candidates: tuple[str, ...], available: list[str]) -> str:
    """Return the exact catalog value while allowing an unambiguous basename match.

    Comfy preserves subfolder-qualified model names. Preset candidates intentionally
    use canonical basenames, so the resolver may match ``folder/model.safetensors``
    only when that basename is unique. It never guesses between duplicate names.
    """
    clean = [str(item).strip().replace("\\", "/") for item in available if str(item or "").strip()]
    by_full = {item.casefold(): item for item in clean}
    by_base: dict[str, list[str]] = {}
    for item in clean:
        by_base.setdefault(item.rsplit("/", 1)[-1].casefold(), []).append(item)
    for candidate in candidates:
        key = str(candidate or "").strip().replace("\\", "/").casefold()
        actual = by_full.get(key)
        if actual:
            return actual
        matches = by_base.get(key.rsplit("/", 1)[-1], [])
        if len(matches) == 1:
            return matches[0]
    return ""


def resolve_engine(
    settings: dict[str, Any],
    *,
    comfy_catalog: dict[str, Any] | None = None,
    native_status: dict[str, Any] | None = None,
) -> EngineResolution:
    """Resolve the execution engine without guessing unavailable assets.

    Smart routing is deterministic and capability-led. It never treats a missing
    model as installed and never reports fallback when the user selected a strict
    engine. Anime prefers the native ISNet specialization; all other presets prefer
    the existing Comfy BiRefNet route when it is ready.
    """

    clean = dict(settings or {})
    requested = str(clean.get("engine") or "smart").strip().lower()
    if requested not in {"smart", "comfy_birefnet", "comfy_rmbg", "native_rembg"}:
        requested = "smart"
    preset = str(clean.get("preset") or "smart_auto").strip().lower()
    fallback_policy = str(clean.get("fallback_policy") or "on_unavailable").strip().lower()
    allow_fallback = fallback_policy in {"on_unavailable", "on_unavailable_or_queue_failure"}

    catalog = comfy_catalog if isinstance(comfy_catalog, dict) else {}
    native = native_status if isinstance(native_status, dict) else {}
    comfy_models = [str(item) for item in catalog.get("models") or [] if str(item or "").strip()]
    comfy_nodes_ready = bool(catalog.get("nodes_ready"))
    selected_comfy = str(clean.get("model") or "").strip()
    if selected_comfy:
        selected_comfy = _first_matching((selected_comfy,), comfy_models)
    if not selected_comfy:
        selected_comfy = _first_matching(PRESET_MODEL_CANDIDATES.get(preset, PRESET_MODEL_CANDIDATES["smart_auto"]), comfy_models)
    comfy_ready = bool(comfy_nodes_ready and selected_comfy)
    rmbg_catalog = catalog.get("rmbg_node") if isinstance(catalog.get("rmbg_node"), dict) else {}
    rmbg_models = [str(item) for item in (catalog.get("rmbg_models") or rmbg_catalog.get("model_choices") or []) if str(item or "").strip()]
    selected_rmbg = str(clean.get("rmbg_model") or "").strip()
    if selected_rmbg:
        selected_rmbg = _first_matching((selected_rmbg,), rmbg_models)
    if not selected_rmbg and len(rmbg_models) == 1:
        selected_rmbg = rmbg_models[0]
    rmbg_ready = bool(rmbg_catalog.get("available") and selected_rmbg)

    native_available = bool(native.get("available"))
    native_models = [str(item) for item in native.get("models") or [] if str(item or "").strip()]
    preferred_native = str(clean.get("native_model") or NATIVE_PRESET_MODELS.get(preset) or NATIVE_PRESET_MODELS["smart_auto"]).strip()
    native_model = preferred_native if not native_models or preferred_native in native_models else ""
    native_ready = bool(native_available and native_model)

    if requested == "comfy_birefnet":
        if comfy_ready:
            return EngineResolution(requested, "comfy_birefnet", preset, selected_comfy, False, "", True, ())
        return EngineResolution(requested, "", preset, selected_comfy, False, "", False, ("Comfy BiRefNet is not ready for the selected preset.",))

    if requested == "comfy_rmbg":
        if rmbg_ready:
            return EngineResolution(requested, "comfy_rmbg", preset, selected_rmbg, False, "", True, ())
        return EngineResolution(requested, "", preset, selected_rmbg, False, "", False, ("ComfyUI-RMBG generic RMBG is not ready or has no selected live model.",))

    if requested == "native_rembg":
        if native_ready:
            return EngineResolution(requested, "native_rembg", preset, native_model, False, "", True, ())
        return EngineResolution(requested, "", preset, native_model, False, "", False, (str(native.get("reason") or "Native rembg is not installed or could not be loaded."),))

    # Smart routing: anime has a purpose-built ISNet model, while the established
    # Comfy BiRefNet route remains the quality-first default for all other presets.
    prefer_native = preset == "anime"
    primary = "native_rembg" if prefer_native else ("comfy_birefnet" if comfy_ready else "comfy_rmbg")
    secondary = "comfy_birefnet" if prefer_native else ("comfy_rmbg" if rmbg_ready else "native_rembg")
    primary_ready = native_ready if prefer_native else (comfy_ready or rmbg_ready)
    secondary_ready = comfy_ready if prefer_native else (rmbg_ready or native_ready)

    if primary_ready:
        resolved_model = native_model if prefer_native else (selected_comfy if comfy_ready else selected_rmbg)
        resolved = primary if prefer_native or comfy_ready else "comfy_rmbg"
        return EngineResolution(requested, resolved, preset, resolved_model, False, "", True, ())
    if allow_fallback and secondary_ready:
        reason = (
            "Anime specialization was unavailable; using the installed Comfy BiRefNet route."
            if prefer_native
            else "Comfy BiRefNet was unavailable for this preset; using Neo's native rembg fallback."
        )
        if prefer_native:
            return EngineResolution(requested, secondary, preset, selected_comfy if comfy_ready else selected_rmbg, True, reason, True, ())
        return EngineResolution(requested, secondary, preset, selected_rmbg if rmbg_ready else native_model, True, reason, True, ())

    errors: list[str] = []
    if not comfy_ready:
        errors.append("Comfy BiRefNet route is unavailable or has no compatible installed model.")
    if not rmbg_ready:
        errors.append("ComfyUI-RMBG generic RMBG route is unavailable or has no live model choice.")
    if not native_ready:
        errors.append(str(native.get("reason") or "Native rembg fallback is unavailable."))
    if not allow_fallback:
        errors.append("Fallback policy is set to Never fallback.")
    return EngineResolution(requested, "", preset, "", False, "", False, tuple(errors))
