from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from typing import Any

from neo_app.video.lora_patch_profiles import build_single_model_lora_patch_profile
from neo_app.video.video_lora_runtime import (
    H3_MODEL_ONLY_LOADER,
    apply_h3_model_only_lora_stack,
    extract_video_lora_rows,
    h3_speed_lora_candidates,
    merge_h3_legacy_turbo,
)

_PHASE5_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_h3_phase5_payload", default=None)
_INSTALLED = False


def _actual_class(object_info: dict[str, Any], class_name: str) -> str:
    folded = {str(key).casefold(): str(key) for key in (object_info or {})}
    return folded.get(class_name.casefold(), "")


def _speed_notes(compiled: dict[str, Any], req: Any, speed_count: int) -> None:
    if speed_count <= 0:
        return
    notes = compiled.setdefault("normalization_notes", [])
    parameters = compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}
    steps = int(parameters.get("steps") or req.steps or 20)
    sampler = str(parameters.get("sampler") or req.sampler or "res_multistep")
    scheduler = str(parameters.get("scheduler") or req.scheduler or "simple")
    if steps > 8:
        notes.append("H3 speed LoRA is active with more than 8 steps. Neo preserves the user value; few-step H3 accelerators are commonly tuned around 4-8 steps.")
    if sampler.casefold() != "euler" or scheduler.casefold() != "beta":
        notes.append("H3 speed LoRA is active outside the common Euler + Beta few-step recipe. Neo preserves the requested sampler/scheduler.")
    if not (4.0 <= float(req.h3_shift_audio) <= 6.0):
        notes.append("H3 speed LoRA audio shift is outside the commonly tested 4-6 range; incompatible few-step scheduling can destabilize audio.")


def install_minimax_h3_lora_integration() -> None:
    """Install the Phase-5 H3 LoRA integration once per Python process.

    The H3 compiler remains the graph authority. This adapter wraps its
    discovery/build entrypoints so the compiler publishes a patch profile and
    the universal Video LoRA stack applies only through that declared anchor.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import minimax_h3_compiler as h3
    from neo_app.video import model_discovery as discovery

    if getattr(h3, "_neo_phase5_video_lora_installed", False):
        _INSTALLED = True
        return

    original_h3_catalogs = discovery._h3_dynamic_catalogs
    original_discover_bindings = h3.discover_minimax_h3_bindings
    original_build_workflow = h3.build_minimax_h3_workflow
    original_compile_payload = h3.video_minimax_h3_compile_payload

    def phase5_h3_catalogs(object_info: dict[str, Any], loader: str, generation_type: str) -> dict[str, Any]:
        result = deepcopy(original_h3_catalogs(object_info, loader, generation_type))
        lora_values = discovery._node_combo_values(
            object_info or {},
            (H3_MODEL_ONLY_LOADER,),
            ("lora_name",),
        )
        result["loras"] = lora_values
        result["turbo_loras"] = h3_speed_lora_candidates(lora_values)
        result["lora_loader_model_only_available"] = bool(_actual_class(object_info or {}, H3_MODEL_ONLY_LOADER))
        return result

    def phase5_discover_bindings(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        result = deepcopy(original_discover_bindings(req, info))
        model_only_class = _actual_class(info, H3_MODEL_ONLY_LOADER)
        classes = result.setdefault("classes", {})
        classes["lora"] = model_only_class
        lora_values = h3._combo_values(info, model_only_class, "lora_name") if model_only_class else []
        speed_values = h3_speed_lora_candidates(lora_values)
        catalogs = result.setdefault("catalogs", {})
        catalogs["loras"] = lora_values
        catalogs["turbo_loras"] = speed_values
        models = result.setdefault("models", {})
        models["turbo_lora"] = h3._first_matching(speed_values, ("4step", "4steps", "turbo", "lightx2v", "lightning", "8step", "distilled"), "") if speed_values else ""
        result["lora_loader_topology"] = {
            "loader_type": "model_only",
            "required_class": H3_MODEL_ONLY_LOADER,
            "selected_class": model_only_class,
            "available": bool(model_only_class),
            "generic_fallback_allowed": False,
        }
        return result

    def phase5_build_workflow(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        payload = _PHASE5_PAYLOAD.get() or req.payload()
        requested_rows = extract_video_lora_rows(payload)

        base_req = replace(req, h3_turbo_enabled=False, h3_turbo_lora="")
        compiled = original_build_workflow(base_req, object_info=info)
        route_id = str(compiled.get("route_id") or "")
        bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
        classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
        catalogs = bindings.get("catalogs") if isinstance(bindings.get("catalogs"), dict) else {}
        speed_candidates = h3_speed_lora_candidates(catalogs.get("loras") or catalogs.get("turbo_loras") or [])
        rows, legacy_bridge = merge_h3_legacy_turbo(
            requested_rows,
            enabled=bool(req.h3_turbo_enabled),
            selected_name=str(req.h3_turbo_lora or ""),
            discovered_candidates=speed_candidates,
            strength=float(req.h3_turbo_strength),
        )

        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        sigma_class = str(classes.get("sigma_shift") or "")
        sigma_nodes = [
            (str(node_id), node)
            for node_id, node in workflow.items()
            if isinstance(node, dict)
            and str(node.get("class_type") or "") == sigma_class
            and isinstance(node.get("inputs"), dict)
            and isinstance(node["inputs"].get("model"), list)
        ]
        if len(sigma_nodes) != 1:
            raise ValueError("MiniMax H3 compiler did not expose exactly one sigma-shift model consumer for the Phase-5 LoRA anchor.")
        sigma_node_id, sigma_node = sigma_nodes[0]
        model_ref = list(sigma_node["inputs"]["model"])

        profile = build_single_model_lora_patch_profile(
            route_id=route_id,
            compiler="neo_app.video.minimax_h3_compiler",
            model_ref=model_ref,
            model_consumers=[{"node_id": sigma_node_id, "input": "model"}],
            loader_type="model_only",
            loader_node_class=H3_MODEL_ONLY_LOADER,
            validated=".unet." in route_id,
            notes=[
                "Phase 5 H3 anchor is the model input immediately upstream of MiniMaxH3SigmaShift.",
                "All standard LoRAs are applied before role=speed LoRAs; the resulting model then continues through H3 sigma/Sage/Spectrum/BlockCache processing.",
            ],
        )
        compiled["lora_patch_profile"] = profile

        if rows:
            if ".unet." not in route_id:
                raise ValueError("MiniMax H3 GGUF Video LoRA/Turbo remains fail-closed until GGUF LoRA-loader compatibility is validated.")
            loader_available = str(classes.get("lora") or "") == H3_MODEL_ONLY_LOADER
            patched_workflow, runtime = apply_h3_model_only_lora_stack(
                workflow,
                profile,
                rows,
                route_id=route_id,
                loader_available=loader_available,
            )
            compiled["workflow"] = patched_workflow
            prompt_payload = compiled.get("prompt_api_payload") if isinstance(compiled.get("prompt_api_payload"), dict) else {}
            prompt_payload["prompt"] = patched_workflow
            compiled["prompt_api_payload"] = prompt_payload
        else:
            runtime = {
                "schema_version": "neo.video.lora_stack.h3.runtime.v1",
                "active": False,
                "route_id": route_id,
                "requested_count": 0,
                "applied_count": 0,
                "standard_count": 0,
                "speed_count": 0,
                "loader_node_class": H3_MODEL_ONLY_LOADER,
                "applied": [],
                "warnings": [],
            }

        visible_loras = {str(item).casefold() for item in catalogs.get("loras", [])}
        runtime_warnings = runtime.setdefault("warnings", [])
        for row in rows:
            if visible_loras and str(row.get("name") or "").casefold() not in visible_loras:
                runtime_warnings.append(f"Selected LoRA is not present in the live LoraLoaderModelOnly catalog: {row.get('name')}")

        runtime["legacy_turbo_bridge"] = legacy_bridge
        runtime["speed_candidates"] = speed_candidates
        runtime["manual_selection_classifier_gate"] = False
        runtime["generic_lora_loader_fallback"] = False
        compiled["video_lora_stack"] = runtime
        _speed_notes(compiled, req, int(runtime.get("speed_count") or 0))

        h3_meta = compiled.get("h3") if isinstance(compiled.get("h3"), dict) else {}
        h3_meta["turbo"] = bool(runtime.get("speed_count"))
        h3_meta["video_lora_stack_active"] = bool(runtime.get("active"))
        h3_meta["video_lora_count"] = int(runtime.get("applied_count") or 0)
        compiled["h3"] = h3_meta

        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules = [rule for rule in rules if str(rule) != "Turbo LoRA is optional and separate from the base quality path."]
        rules.extend([
            "MiniMax H3 UNET LoRAs use the universal Video LoRA stack and a compiler-owned model-only patch profile.",
            "Legacy H3 Turbo controls are bridged into role=speed without double-applying a LoRA already present in the universal stack.",
            "Turbo/LightX2V filename classification is recommendation-only; explicit manual LoRA selection is never blocked by the classifier.",
        ])
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    def phase5_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
        token = _PHASE5_PAYLOAD.set(dict(payload or {}))
        try:
            return original_compile_payload(payload, object_info_override=object_info_override)
        finally:
            _PHASE5_PAYLOAD.reset(token)

    discovery._h3_dynamic_catalogs = phase5_h3_catalogs
    h3.discover_minimax_h3_bindings = phase5_discover_bindings
    h3.build_minimax_h3_workflow = phase5_build_workflow
    h3.video_minimax_h3_compile_payload = phase5_compile_payload
    h3._neo_phase5_video_lora_installed = True
    h3._neo_phase5_video_lora_originals = {
        "discover_minimax_h3_bindings": original_discover_bindings,
        "build_minimax_h3_workflow": original_build_workflow,
        "video_minimax_h3_compile_payload": original_compile_payload,
        "h3_dynamic_catalogs": original_h3_catalogs,
    }
    _INSTALLED = True
