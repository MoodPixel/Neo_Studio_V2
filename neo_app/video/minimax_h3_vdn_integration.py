from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from neo_app.video.vdn_h3_capability import (
    build_vdn_h3_patch_inputs,
    classify_vdn_stage,
    discover_vdn_h3_capability,
    validate_vdn_h3_selection,
)

_VDN_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_h3_vdn_payload", default=None)
_INSTALLED = False


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mode_key(payload: dict[str, Any]) -> str:
    value = payload.get("h3_acceleration_mode")
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _vdn_requested(payload: dict[str, Any]) -> bool:
    if _mode_key(payload) in {"vdn", "vdn_h3", "video_deltanet", "video_delta_net"}:
        return True
    return _bool(payload.get("h3_vdn_enabled", False))


def _select_checkpoint(capability: dict[str, Any], payload: dict[str, Any]) -> str:
    explicit = str(payload.get("h3_vdn_checkpoint") or payload.get("vdn_checkpoint") or "").strip()
    if explicit:
        return explicit

    catalogs = capability.get("catalogs") if isinstance(capability.get("catalogs"), dict) else {}
    mode = str(payload.get("h3_vdn_mode") or "fast").strip().casefold()
    if mode in {"quality", "stage_b", "50", "50_step", "50step"}:
        rows = [str(item) for item in (catalogs.get("stage_b_stages") or []) if str(item)]
        return rows[0] if rows else ""

    recommended = capability.get("recommended") if isinstance(capability.get("recommended"), dict) else {}
    return str(recommended.get("vdn_checkpoint") or "")


def _next_node_id(workflow: dict[str, Any]) -> str:
    numeric: list[int] = []
    for key in workflow:
        try:
            numeric.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str(max(numeric, default=0) + 1)


def _set_schedule_defaults(
    compiled: dict[str, Any],
    payload: dict[str, Any],
    *,
    recommended_steps: int | None,
    dmd: bool,
) -> None:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
    parameters = compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}

    requested_steps = payload.get("steps")
    if dmd and requested_steps not in (None, ""):
        try:
            if int(float(requested_steps)) != 8:
                raise ValueError("VDN DMD/Turbo is the released 8-step route; set steps=8 or leave steps unset so Neo can apply the VDN preset.")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("VDN DMD/Turbo"):
                raise
            raise ValueError("VDN DMD/Turbo requires steps=8.") from exc

    if recommended_steps is not None and requested_steps in (None, ""):
        scheduler_class = str(classes.get("scheduler") or "")
        for node in workflow.values():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != scheduler_class:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            inputs["steps"] = int(recommended_steps)
        parameters["steps"] = int(recommended_steps)

    if dmd and payload.get("sampler") in (None, ""):
        sampler_class = str(classes.get("sampler_select") or "")
        for node in workflow.values():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != sampler_class:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            inputs["sampler_name"] = "er_sde"
        parameters["sampler"] = "er_sde"

    if dmd and payload.get("scheduler") in (None, ""):
        scheduler_class = str(classes.get("scheduler") or "")
        for node in workflow.values():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != scheduler_class:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            inputs["scheduler"] = "beta"
        parameters["scheduler"] = "beta"

    compiled["parameters"] = parameters


def install_minimax_h3_vdn_integration() -> None:
    """Install VDN-H3 as a compiler-owned MiniMax H3 acceleration route.

    VDN is kept outside the core request dataclass for now so older Neo payloads
    and saved projects remain schema-compatible. The wrapper reads VDN-specific
    controls from the raw payload, augments live discovery, and inserts exactly
    one MODEL -> MODEL VDN patch immediately upstream of MiniMaxH3SigmaShift.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import minimax_h3_compiler as h3

    if getattr(h3, "_neo_vdn_h3_installed", False):
        _INSTALLED = True
        return

    original_discover_bindings = h3.discover_minimax_h3_bindings
    original_build_workflow = h3.build_minimax_h3_workflow
    original_compile_payload = h3.video_minimax_h3_compile_payload

    def vdn_discover_bindings(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        result = deepcopy(original_discover_bindings(req, info))
        capability = discover_vdn_h3_capability(info)
        classes = result.setdefault("classes", {})
        classes["vdn"] = str((capability.get("classes") or {}).get("selected") or "")
        catalogs = result.setdefault("catalogs", {})
        vdn_catalogs = capability.get("catalogs") if isinstance(capability.get("catalogs"), dict) else {}
        catalogs["vdn_checkpoints"] = list(vdn_catalogs.get("vdn_checkpoints") or [])
        catalogs["vdn_int8_convrot_stages"] = list(vdn_catalogs.get("int8_convrot_stages") or [])
        catalogs["vdn_dmd_stages"] = list(vdn_catalogs.get("dmd_stages") or [])
        catalogs["vdn_stage_b_stages"] = list(vdn_catalogs.get("stage_b_stages") or [])
        models = result.setdefault("models", {})
        recommended = capability.get("recommended") if isinstance(capability.get("recommended"), dict) else {}
        models["vdn_checkpoint"] = str(recommended.get("vdn_checkpoint") or "")
        result["vdn_capability"] = capability
        return result

    def vdn_build_workflow(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        payload = dict(_VDN_PAYLOAD.get() or {})
        compiled = original_build_workflow(req, object_info=info)
        capability = discover_vdn_h3_capability(info)
        compiled["vdn_capability"] = capability

        if not _vdn_requested(payload):
            return compiled

        if _mode_key(payload) in {"spectrum", "block_cache"}:
            raise ValueError("VDN-H3 cannot be stacked with Spectrum or T8 BlockCache in Neo's validated VDN route.")
        if bool(getattr(req, "enable_sage_attention", False)):
            raise ValueError("VDN-H3 + SageAttention is not enabled in Neo's validated VDN route yet. Disable SageAttention for VDN-H3.")
        if not bool(capability.get("ready")):
            warnings = capability.get("warnings") if isinstance(capability.get("warnings"), list) else []
            detail = f" {warnings[0]}" if warnings else ""
            raise ValueError(f"VDN-H3 was selected but the ComfyUI VDN capability is not ready.{detail}")

        checkpoint = _select_checkpoint(capability, payload)
        if not checkpoint:
            raise ValueError("VDN-H3 was selected but no VDN stage checkpoint is available.")
        stage = classify_vdn_stage(checkpoint)

        turbo_key_present = "h3_vdn_apply_turbo_adapter" in payload or "vdn_apply_turbo_adapter" in payload
        apply_turbo = (
            _bool(payload.get("h3_vdn_apply_turbo_adapter", payload.get("vdn_apply_turbo_adapter")))
            if turbo_key_present
            else bool(stage.turbo_adapter)
        )

        bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
        models = bindings.get("models") if isinstance(bindings.get("models"), dict) else {}
        base_model_name = str(
            getattr(req, "model_name", None)
            or getattr(req, "unet_name", None)
            or getattr(req, "gguf_name", None)
            or models.get("model_name")
            or ""
        )
        lora_runtime = compiled.get("video_lora_stack") if isinstance(compiled.get("video_lora_stack"), dict) else {}
        speed_count = int(lora_runtime.get("speed_count") or 0)
        errors = validate_vdn_h3_selection(
            capability=capability,
            base_model_name=base_model_name,
            vdn_checkpoint=checkpoint,
            apply_turbo_adapter=apply_turbo,
            external_speed_lora_count=speed_count,
        )
        if errors:
            raise ValueError(" ".join(errors))

        _set_schedule_defaults(
            compiled,
            payload,
            recommended_steps=stage.recommended_steps,
            dmd=stage.family == "dmd" and apply_turbo,
        )

        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
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
            raise ValueError("MiniMax H3 compiler did not expose exactly one sigma-shift model consumer for the VDN-H3 patch anchor.")

        vdn_class = str((capability.get("classes") or {}).get("selected") or classes.get("vdn") or "")
        if not vdn_class:
            raise ValueError("VDN-H3 node class could not be resolved from live ComfyUI /object_info.")

        _, sigma_node = sigma_nodes[0]
        model_ref = list(sigma_node["inputs"]["model"])
        patch_inputs = build_vdn_h3_patch_inputs(
            capability=capability,
            vdn_checkpoint=checkpoint,
            apply_turbo_adapter=apply_turbo,
            strength=_float(payload.get("h3_vdn_strength", payload.get("vdn_strength")), 1.0),
            branch_weights=str(payload.get("h3_vdn_branch_weights") or payload.get("vdn_branch_weights") or "auto"),
            attention_backend=str(payload.get("h3_vdn_attention_backend") or payload.get("vdn_attention_backend") or "grouped"),
            verbose=_bool(payload.get("h3_vdn_verbose", payload.get("vdn_verbose", False))),
        )
        node_id = _next_node_id(workflow)
        workflow[node_id] = {"class_type": vdn_class, "inputs": {"model": model_ref, **patch_inputs}}
        sigma_node["inputs"]["model"] = [node_id, 0]
        compiled["workflow"] = workflow

        prompt_payload = compiled.get("prompt_api_payload") if isinstance(compiled.get("prompt_api_payload"), dict) else {}
        prompt_payload["prompt"] = workflow
        compiled["prompt_api_payload"] = prompt_payload

        h3_meta = compiled.get("h3") if isinstance(compiled.get("h3"), dict) else {}
        h3_meta.update({
            "acceleration": "vdn",
            "vdn": True,
            "vdn_checkpoint": checkpoint,
            "vdn_stage_family": stage.family,
            "vdn_precision": stage.precision,
            "vdn_int8_convrot": stage.int8_convrot,
            "vdn_turbo_adapter": apply_turbo,
        })
        compiled["h3"] = h3_meta
        compiled["vdn"] = {
            "active": True,
            "node_class": vdn_class,
            "node_id": node_id,
            "base_model": base_model_name,
            "checkpoint": checkpoint,
            "stage": stage.payload(),
            "patch_inputs": patch_inputs,
            "external_speed_lora_count": speed_count,
        }

        notes = compiled.setdefault("normalization_notes", [])
        if stage.int8_convrot:
            notes.append("VDN-H3 is using an INT8 ConvRot VDN branch stage; the MiniMax H3 base remains independently selectable and INT8 ConvRot H3 safetensors are supported.")
        if stage.family == "dmd" and apply_turbo:
            notes.append("VDN-H3 DMD/Turbo owns the 8-step acceleration adapter; external H3 speed/Turbo LoRAs are not allowed on this route.")
        notes.append("VDN-H3 uses lora_mode=merge and grouped attention by default; branch_weights=auto lets the Comfy node choose cache_gpu or streaming from live memory pressure.")

        rules = compiled.setdefault("rules", [])
        rules.extend([
            "VDN-H3 is a MiniMax H3 acceleration patch, not a separate Neo model family.",
            "VDN-H3 is inserted after the compiler-owned standard H3 LoRA stack and immediately before MiniMaxH3SigmaShift.",
            "INT8 ConvRot MiniMax H3 safetensors and INT8 ConvRot VDN stages are first-class supported VDN inputs.",
            "VDN DMD/Turbo cannot stack with external H3 speed LoRAs.",
            "VDN + GGUF, Spectrum, BlockCache, and SageAttention remain gated until separately validated.",
        ])
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    def vdn_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
        token = _VDN_PAYLOAD.set(dict(payload or {}))
        try:
            return original_compile_payload(payload, object_info_override=object_info_override)
        finally:
            _VDN_PAYLOAD.reset(token)

    h3.discover_minimax_h3_bindings = vdn_discover_bindings
    h3.build_minimax_h3_workflow = vdn_build_workflow
    h3.video_minimax_h3_compile_payload = vdn_compile_payload
    h3._neo_vdn_h3_installed = True
    h3._neo_vdn_h3_originals = {
        "discover_minimax_h3_bindings": original_discover_bindings,
        "build_minimax_h3_workflow": original_build_workflow,
        "video_minimax_h3_compile_payload": original_compile_payload,
    }
    _INSTALLED = True
