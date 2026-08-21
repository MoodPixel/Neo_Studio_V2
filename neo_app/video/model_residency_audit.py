from __future__ import annotations

from typing import Any, Final, Mapping

from neo_app.video.route_matrix import normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.comfy_model_residency_audit.v1"
PHASE: Final[str] = "4.7.2"


def _bool(mapping: Mapping[str, Any], key: str) -> bool:
    return bool(mapping.get(key, False))


def _extract_vram(system_stats: Mapping[str, Any] | None) -> dict[str, Any]:
    stats = dict(system_stats or {})
    devices = stats.get("devices")
    if not isinstance(devices, list):
        return {"available": False, "devices": []}
    rows: list[dict[str, Any]] = []
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        total = device.get("vram_total")
        free = device.get("vram_free")
        try:
            total_i = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_i = None
        try:
            free_i = int(free) if free is not None else None
        except (TypeError, ValueError):
            free_i = None
        pressure = None
        if total_i and free_i is not None and total_i > 0:
            pressure = round(1.0 - (free_i / total_i), 4)
        rows.append({
            "name": str(device.get("name") or device.get("type") or "GPU"),
            "type": str(device.get("type") or ""),
            "vram_total": total_i,
            "vram_free": free_i,
            "vram_pressure": pressure,
        })
    return {"available": bool(rows), "devices": rows}


def video_model_residency_audit_payload(
    *,
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    performance_values: Mapping[str, Any] | None = None,
    system_stats: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe who owns Video model residency and whether Neo requested offload.

    Phase 4.7.2 is intentionally observational. Core Video generation compilers
    queue ComfyUI through /prompt and do not call Comfy's /free endpoint before or
    after ordinary generation. ComfyUI/model-loader nodes therefore own residency.
    Route-specific CPU offload/block-swap controls are reported separately because
    they can make a model appear to reload without a Neo cache flush.
    """

    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    values = dict(performance_values or {})
    explicit = {
        "cpu_offload": _bool(values, "enable_cpu_offload"),
        "vae_offload": _bool(values, "enable_vae_offload"),
        "block_swap": _bool(values, "enable_block_swap"),
        "torch_compile": _bool(values, "enable_torch_compile"),
        "teacache": _bool(values, "enable_teacache"),
        "sage_attention": _bool(values, "enable_sage_attention"),
    }
    offload_requested = any(explicit[key] for key in ("cpu_offload", "vae_offload", "block_swap"))
    reload_classification = "explicit_route_offload_requested" if offload_requested else "comfy_or_loader_managed"
    notes = [
        "Neo Video core generation queues ComfyUI through /prompt and does not issue /free as part of ordinary generation.",
        "ComfyUI and the selected loader/custom nodes own whether loaded model objects remain resident, are CPU-offloaded, or are reloaded under memory pressure.",
        "Post-processing tools can have their own cache controls; this audit describes core Video generation model residency only.",
    ]
    if offload_requested:
        notes.append("One or more explicit low-VRAM offload/block-swap controls are enabled for the selected route.")
    else:
        notes.append("Neo is not requesting CPU offload/block swap for this probe; repeated reloads should be investigated in Comfy/model-loader behavior or VRAM pressure.")

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": nf,
        "loader": nl,
        "generation_type": nt,
        "owner": "comfyui_and_loader_nodes",
        "neo_forced_unload": False,
        "neo_free_endpoint_in_normal_generation": False,
        "neo_post_run_model_cleanup": False,
        "keep_resident_intent": not offload_requested,
        "offload_requested": offload_requested,
        "reload_classification": reload_classification,
        "controls": explicit,
        "system_memory": _extract_vram(system_stats),
        "notes": notes,
        "physical_validation": {
            "required": True,
            "method": "Run the same Video route/model twice and compare Comfy terminal load/offload logs. If Neo reports no offload request but the model reloads, capture the loader/custom-node log around prompt completion and the next prompt start.",
        },
    }
