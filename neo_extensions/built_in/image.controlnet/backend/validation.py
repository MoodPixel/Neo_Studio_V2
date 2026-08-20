from __future__ import annotations

from copy import deepcopy
from typing import Any

from .asset_resolver import resolve_controlnet_task_assets
from .capability_registry import control_intent_from_unit, resolve_route_capability, validate_model_selection_for_route
from .node_discovery import PROVIDER_GATED, UNSUPPORTED, inspect_nodes, preprocessor_status
from .payload_schema import EXTENSION_ID, normalize_block
from .support_matrix import (
    ACTIVE_STATES,
    TASK_MAP_CONTROL,
    TASK_INPAINT_CONTROL,
    TASK_OUTPAINT_CONTROL,
    controlnet_task_state,
    normalize_controlnet_task,
    task_allowed_for_mode,
    task_route_reason,
    route_reason,
    route_state,
    route_profile_for_route,
)


def _note(level: str, field: str, message: str, **extra: Any) -> dict[str, Any]:
    note: dict[str, Any] = {"level": level, "field": field, "message": message}
    note.update(extra)
    return note


def _has_unit_asset(raw_assets: dict[str, Any] | None, uid: str, key: str) -> bool:
    assets = raw_assets or {}
    bucket = assets.get(key) or {}
    if isinstance(bucket, dict):
        return bool(bucket.get(uid) or bucket.get("default") or bucket.get("primary"))
    if isinstance(bucket, list):
        return bool(bucket)
    return bool(bucket)


def _provider_gated_result(
    *,
    state: str,
    reason: str,
    route: dict[str, Any],
    notes: list[dict[str, Any]],
    node_status: dict[str, Any] | None = None,
    active_units: list[dict[str, Any]] | None = None,
    asset_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "enabled": False,
        "state": state,
        "reason": reason,
        "route": route,
        "validation": notes,
        "node_status": node_status or {},
        "active_units": active_units or [],
        "asset_resolution": asset_resolution or {},
        "extension_id": EXTENSION_ID,
    }



def _extract_controlnet_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Extract ControlNet from all queue/extension envelopes used by V2/V1.

    Phase I keeps validation aligned with workflow_patch: validation must check
    the real ControlNet block, not the outer ``extensions``/``payloads`` wrapper.
    """
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get(EXTENSION_ID), dict):
        return deepcopy(payload.get(EXTENSION_ID) or {})
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return deepcopy(payloads.get(EXTENSION_ID) or {})
    nested = payload.get("extensions")
    if isinstance(nested, dict):
        if isinstance(nested.get(EXTENSION_ID), dict):
            return deepcopy(nested.get(EXTENSION_ID) or {})
        if isinstance(nested.get("controlnet"), dict):
            block = deepcopy(nested.get("controlnet") or {})
            block["metadata"] = {**(block.get("metadata") or {}), "legacy_extension_key": True}
            return block
    legacy_keys = {
        "controlnet_units",
        "controlnet_stack_enabled",
        "controlnet_stack_count",
        "controlnet_name",
        "controlnet_preprocessor",
        "controlnet_strength",
        "control_image_name",
    }
    if legacy_keys.intersection(payload):
        return deepcopy(payload)
    return deepcopy(payload)


def _has_workflow_asset(raw_assets: dict[str, Any] | None, uid: str) -> bool:
    return _has_unit_asset(raw_assets, uid, "generated_maps") or _has_unit_asset(raw_assets, uid, "control_images")

def _qwen_adapter_from_block(block: dict[str, Any]) -> str:
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    raw = str(params.get("qwen_controlnet_adapter") or params.get("controlnet_qwen_adapter") or params.get("qwen_cn_adapter") or "auto").strip().lower()
    if raw in {"instantx", "instant_x", "native_controlnet", "controlnet"}:
        return "instantx"
    if raw in {"diffsynth", "diff_synth", "model_patch", "model-patch", "patch"}:
        return "diffsynth"
    return "auto"

def _is_qwen_family_route(family: str, loader: str) -> bool:
    return (
        (family in {"qwen_image", "qwen_image_edit_2509"} and loader in {"diffusion_model", "gguf"})
        or (family == "qwen_rapid_aio" and loader in {"checkpoint_aio", "gguf"})
    )


def _is_qwen_adapter_route(family: str, loader: str, task: str) -> bool:
    return _is_qwen_family_route(family, loader) and task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL}


def _is_qwen_map_route(family: str, loader: str, task: str) -> bool:
    return _is_qwen_family_route(family, loader) and task == TASK_MAP_CONTROL



def _is_flux2_klein_params(params: dict[str, Any]) -> bool:
    variant = str(params.get("flux_variant") or params.get("variant") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if variant in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}:
        return True
    if params.get("_neo_effective_flux2_klein_gguf_route") is True:
        return True
    if isinstance(params.get("flux2_klein_gguf_profile"), dict):
        return True
    for key in ("gguf_unet", "gguf_model", "model", "model_name"):
        text = str(params.get(key) or "").strip().lower().replace("_", "-")
        if "klein" in text and ("flux-2" in text or "flux2" in text or text.startswith("klein")):
            return True
    return False

def _flux_adapter_from_block(block: dict[str, Any], image_params: dict[str, Any] | None = None) -> str:
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    merged = {**(image_params or {}), **params}
    raw = str(params.get("flux_controlnet_adapter") or params.get("controlnet_flux_adapter") or params.get("flux_cn_adapter") or params.get("flux_klein_controlnet_adapter") or "auto").strip().lower().replace("-", "_")
    if raw in {"fun_union", "flux2_fun_union", "flux_2_fun_union", "flux2", "klein", "klein_fun", "flux2_klein"}:
        return "fun_union"
    if raw in {"alimama", "flux_inpaint", "flux_controlnet_inpaint", "inpaint", "controlnet"}:
        return "alimama"
    if _is_flux2_klein_params(merged):
        return "fun_union"
    return "alimama"

def _is_flux_adapter_route(family: str, loader: str, task: str) -> bool:
    return family in {"flux", "flux2_klein"} and loader in {"diffusion_model", "gguf"} and task in {TASK_MAP_CONTROL, TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL}


def _is_krea2_control_route(family: str, loader: str, task: str, workflow_mode: str) -> bool:
    return family in {"krea2", "krea2_turbo"} and loader in {"diffusion_model", "gguf"} and task == TASK_MAP_CONTROL and str(workflow_mode or "") in {"generate", "txt2img"}


def validate_controlnet_payload(
    raw_payload: dict[str, Any] | None,
    *,
    backend: str = "comfyui",
    family: str = "sdxl",
    loader: str = "checkpoint",
    workflow_mode: str = "generate",
    object_info: dict[str, Any] | None = None,
    require_assets: bool = False,
    image_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_state = route_state(backend, family, loader, workflow_mode)
    route = {"backend": backend, "family": family, "loader": loader, "workflow_mode": workflow_mode, "route_state": base_state, "base_route_state": base_state}
    block, notes = normalize_block(_extract_controlnet_block(raw_payload), route=route)
    task = normalize_controlnet_task(((block.get("params") or {}) if isinstance(block, dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL, workflow_mode=workflow_mode)
    task_state = controlnet_task_state(backend, family, loader, workflow_mode, task)
    route_profile = route_profile_for_route(backend, family, loader, workflow_mode, task)
    route = {**route, "controlnet_task": task, "controlnet_task_state": task_state, "route_profile": route_profile, "route_profile_id": route_profile.get("profile_id"), "map_adapter": route_profile.get("map_adapter"), "inpaint_adapter": route_profile.get("inpaint_adapter"), "outpaint_adapter": route_profile.get("outpaint_adapter")}
    notes = [dict(note) for note in notes]
    asset_resolution = resolve_controlnet_task_assets(block, image_params=image_params, route=route) if task != TASK_MAP_CONTROL else {}

    if not block.get("enabled"):
        return {
            "ok": True,
            "enabled": False,
            "state": task_state,
            "reason": block.get("metadata", {}).get("reason", "disabled"),
            "route": route,
            "validation": notes,
            "node_status": {},
            "active_units": [],
            "asset_resolution": asset_resolution,
            "extension_id": EXTENSION_ID,
        }

    units = block.get("inputs", {}).get("units") or []
    if not task_allowed_for_mode(task, workflow_mode):
        return _provider_gated_result(
            state="unsupported",
            reason=task_route_reason(task, "unsupported"),
            route=route,
            notes=notes + [_note("error", "params.controlnet_task", "ControlNet task is not valid for this workflow mode.", controlnet_task=task, workflow_mode=workflow_mode)],
            active_units=units,
            asset_resolution=asset_resolution,
        )

    capability_method = "qwen_transfer" if any(str(unit.get("pose_method") or "").strip().lower() == "qwen_transfer" for unit in units if isinstance(unit, dict)) else ""
    capability = None
    if str(backend or "").strip().lower() != "forge":
        capability = resolve_route_capability(
            family=family, loader=loader, mode=workflow_mode, task=task, backend=backend, method=capability_method
        )
        if not capability.get("implemented"):
            return _provider_gated_result(
                state="implementation_target" if capability.get("known_family") else "unsupported",
                reason="No implemented Neo ControlNet capability matches the active family/loader/mode/task route.",
                route={**route, "capability": capability},
                notes=notes + [_note("error", "capability.route", "ControlNet is not implemented for this active route. Neo does not fall back to another family adapter.", family=family, loader=loader, workflow_mode=workflow_mode, controlnet_task=task, method=capability_method)],
                active_units=units,
                asset_resolution=asset_resolution,
            )
        allowed_intents = set(capability.get("implemented_intents") or [])
        invalid_units = []
        for index, unit in enumerate(units):
            intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
            if not intent and task != TASK_MAP_CONTROL:
                continue
            if intent not in allowed_intents:
                invalid_units.append({"index": index, "intent": intent or "(none)"})
        if invalid_units:
            return _provider_gated_result(
                state=task_state,
                reason="Selected ControlNet type is not implemented for the active route.",
                route={**route, "capability": capability},
                notes=notes + [_note("error", "inputs.units", "One or more ControlNet units use a control type that Neo has not implemented for this family/route.", invalid_units=invalid_units, allowed=sorted(allowed_intents))],
                active_units=units,
                asset_resolution=asset_resolution,
            )
        max_units = capability.get("max_active_units")
        if isinstance(max_units, int) and max_units > 0 and len(units) > max_units:
            return _provider_gated_result(
                state=task_state,
                reason=f"This ControlNet capability supports at most {max_units} active unit(s).",
                route={**route, "capability": capability},
                notes=notes + [_note("error", "inputs.units", "Too many active ControlNet units for the resolved capability.", max_units=max_units, active_units=len(units))],
                active_units=units,
                asset_resolution=asset_resolution,
            )
        route["capability"] = capability

    if capability_method == "qwen_transfer" and family == "qwen_image_edit_2511":
        node_status = inspect_nodes(object_info)
        pose_nodes = ((node_status.get("preprocessors") or {}).get("openpose") or []) if isinstance(node_status.get("preprocessors"), dict) else []
        object_names = {str(key) for key in (object_info or {}).keys()} if isinstance(object_info, dict) else set()
        errors = []
        if len(units) != 1:
            errors.append(_note("error", "inputs.units", "Qwen 2511 Pose Transfer supports exactly one active pose unit.", active_units=len(units)))
        unit = units[0] if units else {}
        if control_intent_from_unit(unit) != "openpose":
            errors.append(_note("error", "inputs.units[0].unit", "Qwen 2511 Pose Transfer requires the OpenPose control intent."))
        if not str(unit.get("pose_base_lora") or "").strip():
            errors.append(_note("error", "inputs.units[0].pose_base_lora", "Select the Pose Transfer base LoRA."))
        if not str(unit.get("pose_helper_lora") or "").strip():
            errors.append(_note("error", "inputs.units[0].pose_helper_lora", "Select the Pose Transfer helper LoRA."))
        if node_status.get("object_info_present"):
            if not pose_nodes:
                errors.append(_note("error", "nodes.openpose", "Qwen 2511 Pose Transfer requires a DWPose/OpenPose preprocessor node."))
            if "LoraLoaderModelOnly" not in object_names:
                errors.append(_note("error", "nodes.LoraLoaderModelOnly", "Qwen 2511 Pose Transfer requires LoraLoaderModelOnly."))
        route = {**route, "route_state": "experimental_available", "controlnet_task_state": "experimental_available", "capability": capability, "pose_transfer_method": "qwen_transfer"}
        return {
            "ok": not errors,
            "enabled": not errors,
            "state": "experimental_available" if not errors else ("provider_gated" if any(str(item.get("field") or "").startswith("nodes.") for item in errors) else "experimental_available"),
            "reason": "validated" if not errors else errors[0]["message"],
            "route": route,
            "validation": notes + errors,
            "node_status": node_status,
            "unit_statuses": [],
            "active_units": units if not errors else [],
            "asset_resolution": asset_resolution,
            "extension_id": EXTENSION_ID,
        }

    if family in {"krea2", "krea2_turbo"}:
        params_for_krea = image_params if isinstance(image_params, dict) else {}
        krea_edit_engine = str(params_for_krea.get("krea2_edit_engine") or params_for_krea.get("edit_engine") or "").strip().lower().replace("-", "_")
        if krea_edit_engine in {"identity_edit", "krea2_identity_edit", "identity"}:
            return _provider_gated_result(
                state="implementation_target",
                reason="Krea 2 Identity Edit + Control LoRA stacking is not enabled in Control Phase 1.",
                route=route,
                notes=notes + [_note("error", "params.krea2_edit_engine", "Use Krea 2 Control on Generate without Identity Edit. Identity Edit + Krea 2 Control will be validated in a later compatibility phase.", edit_engine=krea_edit_engine)],
                active_units=units,
                asset_resolution=asset_resolution,
            )

    if task_state not in ACTIVE_STATES:
        return _provider_gated_result(
            state=task_state,
            reason=task_route_reason(task, task_state),
            route=route,
            notes=notes,
            active_units=units,
            asset_resolution=asset_resolution,
        )

    node_status = inspect_nodes(object_info)
    if _is_krea2_control_route(family, loader, task, workflow_mode):
        intent = control_intent_from_unit(units[0] if units and isinstance(units[0], dict) else {}) if units else ""
        if intent == "composition_silhouette":
            status_key = "krea2_control_plus"
            required_nodes = ["Krea2ControlPlusLoRALoader", "Krea2ControlPlusImageEncode", "Krea2ControlPlusApply"]
            adapter_label = "Krea 2 Control Plus"
            adapter_id = "krea2_control_plus"
        elif intent == "openpose":
            status_key = "krea2_ostris"
            required_nodes = ["TextEncodeKrea2OstrisEdit", "Krea2OstrisEditModelPatch", "LoraLoaderModelOnly"]
            adapter_label = "Krea 2 Ostris OpenPose"
            adapter_id = "krea2_ostris_openpose"
        elif intent == "canny":
            status_key = "krea2_nk2e"
            required_nodes = ["NK2EInContextModelNode", "NK2ESetReferenceNode", "LoraLoaderModelOnly", "VAEEncode"]
            adapter_label = "Krea 2 NK2E Canny"
            adapter_id = "krea2_nk2e_canny"
        else:
            status_key = "krea2_control"
            required_nodes = ["Krea2ControlLoRALoader", "Krea2ControlImageEncode", "Krea2ControlApply"]
            adapter_label = "Krea 2 Control LoRA"
            adapter_id = "krea2_native_control_lora"
        krea_status = node_status.get(status_key) if isinstance(node_status.get(status_key), dict) else {}
        if node_status.get("object_info_present") and not krea_status.get("available"):
            missing = list(krea_status.get("missing") or required_nodes)
            return _provider_gated_result(
                state="provider_gated",
                reason=f"{adapter_label} nodes are missing.",
                route=route,
                notes=notes + [_note(
                    "error",
                    f"nodes.{status_key}",
                    f"Install/update the required {adapter_label} custom nodes so the active Krea intent can run.",
                    intent=intent,
                    missing=missing,
                )],
                node_status=node_status,
                active_units=units,
                asset_resolution=asset_resolution,
            )
        if units:
            model_name = str(units[0].get("model") or "").strip()
            live_loras = {str(item).strip() for item in krea_status.get("lora_models") or [] if str(item).strip()}
            model_catalog_key = model_name.replace("\\", "/").casefold()
            live_lora_keys = {item.replace("\\", "/").casefold() for item in live_loras}
            if model_name and node_status.get("object_info_present") and live_loras and model_catalog_key not in live_lora_keys:
                return _provider_gated_result(
                    state="provider_gated",
                    reason=f"Selected {adapter_label} is not in the current Comfy catalog.",
                    route=route,
                    notes=notes + [_note(
                        "error",
                        "inputs.units[0].model",
                        "The selected Krea 2 Control LoRA is no longer available in the active adapter's live models/loras catalog. Refresh nodes or choose an installed compatible LoRA.",
                        model=model_name,
                        intent=intent,
                        adapter=adapter_id,
                    )],
                    node_status=node_status,
                    active_units=units,
                    asset_resolution=asset_resolution,
                )
            if model_name:
                model_binding = validate_model_selection_for_route(
                    model_name,
                    family=family,
                    loader=loader,
                    mode=workflow_mode,
                    intent=intent,
                    task=task,
                    backend=backend,
                    method=capability_method,
                    node_status=node_status,
                )
                if not model_binding.get("valid"):
                    return _provider_gated_result(
                        state="provider_gated",
                        reason="Selected Krea 2 Control LoRA is incompatible with the active control intent.",
                        route={**route, "model_binding": model_binding.get("binding") or {}},
                        notes=notes + [_note(
                            "error",
                            "inputs.units[0].model",
                            "The selected LoRA is present in ComfyUI/models/loras but is not compatible with the active Krea 2 control intent. Choose a LoRA from Neo's filtered Control LoRA list.",
                            model=model_name,
                            intent=intent,
                            compatibility_status=model_binding.get("status"),
                            compatible_models=list(model_binding.get("compatible_models") or []),
                        )],
                        node_status=node_status,
                        active_units=units,
                        asset_resolution=asset_resolution,
                    )
    elif _is_qwen_adapter_route(family, loader, task):
        qwen_status = node_status.get("qwen") if isinstance(node_status.get("qwen"), dict) else {}
        adapter = _qwen_adapter_from_block(block)
        if adapter == "auto":
            adapter = "diffsynth" if (qwen_status.get("diffsynth_available") or not node_status.get("object_info_present")) else "instantx"
        if node_status.get("object_info_present") and adapter == "diffsynth" and not qwen_status.get("diffsynth_available"):
            return _provider_gated_result(
                state="provider_gated",
                reason="Qwen DiffSynth ControlNet nodes are missing.",
                route=route,
                notes=notes + [_note("error", "nodes.qwen_diffsynth", "Install/update ComfyUI Qwen DiffSynth ControlNet nodes: ModelPatchLoader + QwenImageDiffsynthControlnet.", missing=["ModelPatchLoader", "QwenImageDiffsynthControlnet"])],
                node_status=node_status,
                active_units=units,
                asset_resolution=asset_resolution,
            )
        if node_status.get("object_info_present") and adapter == "instantx" and not qwen_status.get("instantx_available"):
            return _provider_gated_result(
                state="provider_gated",
                reason="Qwen InstantX ControlNet nodes are missing.",
                route=route,
                notes=notes + [_note("error", "nodes.qwen_instantx", "Install/update native Qwen/InstantX ControlNet support or standard ControlNetLoader + ControlNetApplyAdvanced nodes.", missing=["ControlNetLoader", "ControlNetApplyAdvanced"])],
                node_status=node_status,
                active_units=units,
                asset_resolution=asset_resolution,
            )
    elif _is_qwen_map_route(family, loader, task):
        qwen_status = node_status.get("qwen") if isinstance(node_status.get("qwen"), dict) else {}
        if node_status.get("object_info_present") and not (qwen_status.get("instantx_available") or node_status.get("base_available")):
            return _provider_gated_result(
                state="provider_gated",
                reason="Qwen map ControlNet nodes are missing.",
                route=route,
                notes=notes + [_note("error", "nodes.qwen_map_control", "Install/update native Qwen/InstantX ControlNet support or standard ControlNetLoader + ControlNetApplyAdvanced nodes.", missing=["Qwen/InstantX ControlNet loader/apply or ControlNetLoader/ControlNetApplyAdvanced"])],
                node_status=node_status,
                active_units=units,
                asset_resolution=asset_resolution,
            )
    elif _is_flux_adapter_route(family, loader, task):
        flux_adapter = "fun_union" if family == "flux2_klein" else _flux_adapter_from_block(block, image_params=image_params)
        flux_key = "flux2_klein" if flux_adapter == "fun_union" else "flux"
        flux_status = node_status.get(flux_key) if isinstance(node_status.get(flux_key), dict) else {}
        if not flux_status and flux_key == "flux2_klein":
            flux_status = node_status.get("flux") if isinstance(node_status.get("flux"), dict) else {}
        if node_status.get("object_info_present") and not flux_status.get("available"):
            return _provider_gated_result(
                state="provider_gated",
                reason="Flux ControlNet nodes are missing.",
                route=route,
                notes=notes + [_note("error", f"nodes.{flux_key}_controlnet", "Install/update Flux-compatible ControlNet loader/apply nodes for the selected Flux ControlNet adapter.", missing=["Flux/ControlNet loader", "Flux/ControlNet apply"], adapter=flux_adapter, controlnet_task=task)],
                node_status=node_status,
                active_units=units,
                asset_resolution=asset_resolution,
            )
    elif node_status.get("provider_gated"):
        missing = ", ".join(node_status.get("missing") or [])
        return _provider_gated_result(
            state="provider_gated",
            reason=f"ControlNet base Comfy nodes are missing: {missing}.",
            route=route,
            notes=notes + [_note("error", "nodes.base", "Required ControlNet base nodes are missing.", missing=node_status.get("missing") or [])],
            node_status=node_status,
            active_units=units,
            asset_resolution=asset_resolution,
        )

    errors: list[dict[str, Any]] = [note for note in notes if note.get("level") == "error"]
    warnings: list[dict[str, Any]] = [note for note in notes if note.get("level") == "warning"]
    unit_statuses: list[dict[str, Any]] = []

    if not units:
        errors.append(_note("error", "inputs.units", "ControlNet is enabled but no active units were provided."))

    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    if task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL} and not asset_resolution.get("ready"):
        errors.append(_note(
            "error",
            "asset_resolution",
            "ControlNet inpaint/outpaint task needs the Image Tab source/mask or outpaint source/padding assets before queue.",
            controlnet_task=task,
            missing=asset_resolution.get("missing") or [],
        ))

    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        field_prefix = f"inputs.units[{index}]"
        if not unit.get("model"):
            if family == "flux2_klein" and task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL}:
                warnings.append(_note("warning", f"{field_prefix}.model", "Flux Klein Fun Union ControlNet model was not provided; Neo will use the route-profile default model name during workflow patching.", uid=uid, default_model="FLUX.2-dev-Fun-Controlnet-Union-2602.safetensors"))
            else:
                errors.append(_note("error", f"{field_prefix}.model", "Enabled ControlNet unit is missing a model.", uid=uid))
        if task == TASK_MAP_CONTROL and require_assets and not _has_workflow_asset(assets, uid):
            errors.append(_note("error", f"assets.control_images.{uid}", "Enabled ControlNet unit needs a generated map or control image before queue.", uid=uid))

        if task == TASK_MAP_CONTROL:
            prep_status = preprocessor_status(unit.get("preprocessor"), node_status, unit=unit.get("unit"))
            unit_statuses.append({"uid": uid, "unit": unit.get("unit"), "preprocessor": unit.get("preprocessor"), "preprocessor_status": prep_status})
            if prep_status["state"] == UNSUPPORTED:
                errors.append(_note("error", f"{field_prefix}.preprocessor", prep_status.get("reason", "Unsupported ControlNet preprocessor."), uid=uid, group=prep_status.get("group")))
            elif prep_status["state"] == PROVIDER_GATED:
                errors.append(_note("error", f"{field_prefix}.preprocessor", prep_status.get("reason", "ControlNet preprocessor node is missing."), uid=uid, group=prep_status.get("group")))
            elif prep_status["state"] == "experimental_available":
                warnings.append(_note("warning", f"{field_prefix}.preprocessor", prep_status.get("reason", "ControlNet preprocessor is using an experimental fallback path."), uid=uid, group=prep_status.get("group")))
        else:
            unit_statuses.append({"uid": uid, "unit": unit.get("unit"), "preprocessor": unit.get("preprocessor"), "preprocessor_status": {"state": "not_required", "reason": "Inpaint/outpaint ControlNet uses the Image Tab source/mask/canvas adapter, not a map preprocessor."}})

        if unit.get("advanced_enabled") and not _is_krea2_control_route(family, loader, task, workflow_mode) and not node_status.get("advanced_available"):
            errors.append(_note("error", f"{field_prefix}.advanced_enabled", "Advanced ControlNet was requested but no advanced apply node was detected.", uid=uid))

        mask_mode = unit.get("mask_mode")
        if task == TASK_MAP_CONTROL and mask_mode == "control_mask" and not _has_unit_asset(assets, uid, "control_masks"):
            warnings.append(_note("warning", f"{field_prefix}.mask_mode", "Control mask mode is selected but no control mask asset is attached yet.", uid=uid))

    final_notes = notes + warnings + [error for error in errors if error not in notes]
    provider_gated_errors = [error for error in errors if ".preprocessor" in error.get("field", "") or ".advanced_enabled" in error.get("field", "")]
    result_state = "provider_gated" if provider_gated_errors else task_state
    reason = "validated" if not errors else (provider_gated_errors[0]["message"] if provider_gated_errors else "validation_failed")

    return {
        "ok": not errors,
        "enabled": not errors,
        "state": result_state,
        "reason": reason,
        "route": route,
        "validation": final_notes,
        "node_status": node_status,
        "unit_statuses": unit_statuses,
        "active_units": units if not errors else [],
        "asset_resolution": asset_resolution,
        "extension_id": EXTENSION_ID,
    }
