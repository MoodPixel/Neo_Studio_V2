from __future__ import annotations

from typing import Any, Mapping

from .comfy_llamacpp_compat import pick_auto_vision_model, resolve_caption_route

LOADER_NODE_ALIASES = ("llama_cpp_model_loader",)
INSTRUCT_NODE_ALIASES = ("llama_cpp_instruct_adv",)
PARAMETER_NODE_ALIASES = ("llama_cpp_parameters",)
UNLOAD_NODE_ALIASES = ("llama_cpp_unload_model",)
CLEAN_STATE_NODE_ALIASES = ("llama_cpp_clean_states",)
NEO_TEXT_OUTPUT_NODE_ALIASES = ("NeoPromptCaptionTextOutput",)
NEO_IMAGE_INPUT_NODE_ALIASES = ("NeoPromptCaptionImageInput",)
NEO_GENERIC_MTMD_LOADER_NODE_ALIASES = ("NeoGenericMTMDModelLoader",)
NEO_GENERIC_MTMD_INSTRUCT_NODE_ALIASES = ("NeoGenericMTMDInstruct",)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _find_node(object_info: Mapping[str, Any], aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        if alias in object_info:
            return alias
    folded = {str(key).casefold(): str(key) for key in object_info}
    for alias in aliases:
        match = folded.get(alias.casefold())
        if match:
            return match
    return ""


def _node_inputs(object_info: Mapping[str, Any], node_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    node = _mapping(object_info.get(node_name)) if node_name else {}
    inputs = _mapping(node.get("input"))
    return _mapping(inputs.get("required")), _mapping(inputs.get("optional"))


def _choice_values(spec: Any) -> list[str]:
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    values = spec[0]
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def _records(values: list[str], *, kind: str, source: str) -> list[dict[str, Any]]:
    return [{"kind": kind, "name": value, "source": source} for value in values]


def discover_comfy_llamacpp(object_info: Mapping[str, Any] | None, *, discovery_error: str = "") -> dict[str, Any]:
    """Parse Comfy ``/object_info`` for the llama.cpp/VLM custom-node pack.

    Phase 3 discovers node/model capability truth plus the Neo-owned Comfy
    bridge nodes required for stable Prompt/Caption execution and history handoff.
    """
    catalog = _mapping(object_info)
    loader_node = _find_node(catalog, LOADER_NODE_ALIASES)
    instruct_node = _find_node(catalog, INSTRUCT_NODE_ALIASES)
    parameters_node = _find_node(catalog, PARAMETER_NODE_ALIASES)
    unload_node = _find_node(catalog, UNLOAD_NODE_ALIASES)
    clean_state_node = _find_node(catalog, CLEAN_STATE_NODE_ALIASES)
    neo_text_output_node = _find_node(catalog, NEO_TEXT_OUTPUT_NODE_ALIASES)
    neo_image_input_node = _find_node(catalog, NEO_IMAGE_INPUT_NODE_ALIASES)
    neo_generic_mtmd_loader_node = _find_node(catalog, NEO_GENERIC_MTMD_LOADER_NODE_ALIASES)
    neo_generic_mtmd_instruct_node = _find_node(catalog, NEO_GENERIC_MTMD_INSTRUCT_NODE_ALIASES)

    loader_required, loader_optional = _node_inputs(catalog, loader_node)
    instruct_required, instruct_optional = _node_inputs(catalog, instruct_node)
    parameter_required, parameter_optional = _node_inputs(catalog, parameters_node)

    models = _choice_values(loader_required.get("model") or loader_optional.get("model"))
    mmproj = [value for value in _choice_values(loader_required.get("mmproj") or loader_optional.get("mmproj")) if value.casefold() not in {"none", "null", "off"}]
    chat_handlers = _choice_values(loader_required.get("chat_handler") or loader_optional.get("chat_handler"))
    usable_chat_handlers = [value for value in chat_handlers if value.casefold() not in {"none", "null", "off"}]

    instruct_input_names = set(instruct_required) | set(instruct_optional)
    image_input_names = sorted(name for name in instruct_input_names if name == "images" or name.startswith("image_"))
    accepts_images = bool(image_input_names)

    node_pack_ready = bool(loader_node and instruct_node)
    text_ready = bool(node_pack_ready and models)
    generic_mtmd_ready = bool(neo_generic_mtmd_loader_node and neo_generic_mtmd_instruct_node)
    dedicated_vision_ready = bool(text_ready and accepts_images and mmproj and usable_chat_handlers)
    generic_vision_ready = bool(text_ready and mmproj and generic_mtmd_ready)
    vision_ready = bool(dedicated_vision_ready or generic_vision_ready)
    caption_ready = vision_ready
    text_execution_ready = bool(text_ready and neo_text_output_node)
    caption_execution_ready = bool(caption_ready and neo_text_output_node and neo_image_input_node)

    warnings: list[str] = []
    if discovery_error:
        warnings.append(f"ComfyUI object_info discovery failed: {discovery_error}")
    if catalog and not loader_node:
        warnings.append("llama_cpp_model_loader was not detected. Install/update ComfyUI-llama-cpp_vlm, then restart ComfyUI.")
    if catalog and not instruct_node:
        warnings.append("llama_cpp_instruct_adv was not detected. Install/update ComfyUI-llama-cpp_vlm, then restart ComfyUI.")
    if node_pack_ready and not models:
        warnings.append("The llama.cpp node pack is installed but no main LLM/VLM models were exposed by its model dropdown.")
    if text_ready and not mmproj:
        warnings.append("Text inference is discoverable, but no mmproj projector is available for VLM image input.")
    if text_ready and not accepts_images and not generic_mtmd_ready:
        warnings.append("The detected llama.cpp instruct node does not expose an image input and Neo Generic MTMD is unavailable, so Caption Studio has no image route.")
    if text_ready and not usable_chat_handlers and not generic_mtmd_ready:
        warnings.append("No dedicated vision chat handler or Neo Generic MTMD fallback is available for VLM image input.")
    if text_ready and not neo_text_output_node:
        warnings.append("NeoPromptCaptionTextOutput was not detected. Copy Neo Studio's bundled neo_prompt_captioning folder into ComfyUI/custom_nodes and restart ComfyUI.")
    if vision_ready and not neo_image_input_node:
        warnings.append("NeoPromptCaptionImageInput was not detected. Copy Neo Studio's bundled neo_prompt_captioning folder into ComfyUI/custom_nodes and restart ComfyUI.")

    if not catalog:
        discovery_status = "object_info_unavailable"
    elif not node_pack_ready:
        discovery_status = "missing_required_nodes"
    elif not models:
        discovery_status = "nodes_ready_no_models"
    elif caption_execution_ready:
        discovery_status = "text_and_vision_execution_ready"
    elif text_execution_ready:
        discovery_status = "text_execution_ready_vision_incomplete"
    elif vision_ready:
        discovery_status = "text_and_vision_ready_missing_neo_handoff"
    else:
        discovery_status = "text_ready_vision_incomplete"

    gguf_models = [name for name in models if name.casefold().endswith(".gguf")]
    node_inputs = {}
    for node_name, required, optional in (
        (loader_node, loader_required, loader_optional),
        (instruct_node, instruct_required, instruct_optional),
        (parameters_node, parameter_required, parameter_optional),
        (neo_generic_mtmd_loader_node, *_node_inputs(catalog, neo_generic_mtmd_loader_node)),
        (neo_generic_mtmd_instruct_node, *_node_inputs(catalog, neo_generic_mtmd_instruct_node)),
    ):
        if node_name:
            node_inputs[node_name] = {"required": required, "optional": optional}

    return {
        "provider_id": "comfy_llamacpp",
        "backend": "comfyui",
        "phase": "prompt_captioning_comfy_llamacpp_phase10_1_mtmd",
        "discovery_status": discovery_status,
        "object_info_available": bool(catalog),
        "object_info_node_count": len(catalog),
        "node_pack_ready": node_pack_ready,
        "text_ready": text_ready,
        "vision_ready": vision_ready,
        "caption_ready": caption_ready,
        "text_execution_ready": text_execution_ready,
        "caption_execution_ready": caption_execution_ready,
        "generic_mtmd_ready": generic_mtmd_ready,
        "execution_ready": text_execution_ready,
        "nodes": {
            "loader": {"class_type": loader_node, "available": bool(loader_node)},
            "instruct": {"class_type": instruct_node, "available": bool(instruct_node), "accepts_images": accepts_images, "image_inputs": image_input_names},
            "parameters": {"class_type": parameters_node, "available": bool(parameters_node)},
            "unload": {"class_type": unload_node, "available": bool(unload_node)},
            "clean_states": {"class_type": clean_state_node, "available": bool(clean_state_node)},
            "neo_text_output": {"class_type": neo_text_output_node, "available": bool(neo_text_output_node)},
            "neo_image_input": {"class_type": neo_image_input_node, "available": bool(neo_image_input_node)},
            "neo_generic_mtmd_loader": {"class_type": neo_generic_mtmd_loader_node, "available": bool(neo_generic_mtmd_loader_node)},
            "neo_generic_mtmd_instruct": {"class_type": neo_generic_mtmd_instruct_node, "available": bool(neo_generic_mtmd_instruct_node)},
        },
        "models": {
            "all": models,
            "gguf": gguf_models,
            "mmproj": mmproj,
            "chat_handlers": chat_handlers,
            "usable_chat_handlers": usable_chat_handlers,
        },
        "model_records": {
            "text_models": _records(models, kind="llama_cpp_model", source=loader_node or "object_info"),
            "gguf_models": _records(gguf_models, kind="llama_cpp_gguf", source=loader_node or "object_info"),
            "mmproj": _records(mmproj, kind="llama_cpp_mmproj", source=loader_node or "object_info"),
        },
        "object_info_node_inputs": node_inputs,
        "warnings": warnings,
    }



READINESS_SCHEMA_ID = "neo.prompt_captioning.comfy_llamacpp.readiness.v1"


def _clean_setting(settings: Mapping[str, Any], key: str) -> str:
    value = settings.get(key)
    return str(value or "").strip()


def evaluate_comfy_llamacpp_readiness(
    discovery: Mapping[str, Any] | None,
    *,
    settings: Mapping[str, Any] | None = None,
    reachable: bool = False,
) -> dict[str, Any]:
    """Turn raw Comfy discovery into actionable Prompt/Caption readiness.

    Phase 7 keeps installation truth separate from execution compilation.  The
    readiness record names exactly what is missing, distinguishes stale saved
    selections from missing packages/models, and gives Prompt and Caption their
    own fail-closed readiness states.
    """
    clean = _mapping(discovery)
    cfg = _mapping(settings)
    nodes = _mapping(clean.get("nodes"))
    models = _mapping(clean.get("models"))
    main_models = [str(v) for v in (models.get("all") or []) if str(v).strip()]
    mmproj = [str(v) for v in (models.get("mmproj") or []) if str(v).strip()]
    handlers = [str(v) for v in (models.get("chat_handlers") or []) if str(v).strip()]
    usable_handlers = [str(v) for v in (models.get("usable_chat_handlers") or []) if str(v).strip()]

    selected_model = _clean_setting(cfg, "comfy_llamacpp_model")
    selected_mmproj = _clean_setting(cfg, "comfy_llamacpp_mmproj")
    selected_handler = _clean_setting(cfg, "comfy_llamacpp_chat_handler")
    object_info_available = bool(clean.get("object_info_available"))
    accepts_images = bool(_mapping(nodes.get("instruct")).get("accepts_images"))

    checks: list[dict[str, Any]] = []

    def add_check(
        check_id: str,
        label: str,
        *,
        ok: bool | None,
        required_for: tuple[str, ...],
        category: str,
        detail: str,
        next_action: str = "",
        code: str = "",
    ) -> None:
        status = "ready" if ok is True else ("warning" if ok is False and not required_for else "blocked") if ok is False else "not_checked"
        checks.append({
            "check_id": check_id,
            "label": label,
            "status": status,
            "ok": ok,
            "required_for": list(required_for),
            "category": category,
            "detail": detail,
            "next_action": next_action,
            "code": code or check_id,
        })

    add_check(
        "comfy_server",
        "ComfyUI server",
        ok=bool(reachable),
        required_for=("prompt", "caption"),
        category="connection",
        detail="Connected to the configured ComfyUI server." if reachable else "The configured ComfyUI server is not currently reachable/tested.",
        next_action="Start ComfyUI and run Connect/Test for this backend profile." if not reachable else "",
        code="comfy_not_reachable" if not reachable else "comfy_reachable",
    )
    add_check(
        "object_info",
        "Comfy node catalog",
        ok=object_info_available if reachable else None,
        required_for=("prompt", "caption"),
        category="connection",
        detail="ComfyUI /object_info is available." if object_info_available else "Neo could not inspect ComfyUI /object_info.",
        next_action="Run Connect/Test again after ComfyUI finishes loading custom nodes." if reachable and not object_info_available else "",
        code="object_info_unavailable" if reachable and not object_info_available else "object_info_ready",
    )

    can_inspect = bool(reachable and object_info_available)
    loader_ok = bool(_mapping(nodes.get("loader")).get("available")) if can_inspect else None
    instruct_ok = bool(_mapping(nodes.get("instruct")).get("available")) if can_inspect else None
    params_ok = bool(_mapping(nodes.get("parameters")).get("available")) if can_inspect else None
    neo_output_ok = bool(_mapping(nodes.get("neo_text_output")).get("available")) if can_inspect else None
    neo_image_ok = bool(_mapping(nodes.get("neo_image_input")).get("available")) if can_inspect else None
    generic_loader_ok = bool(_mapping(nodes.get("neo_generic_mtmd_loader")).get("available")) if can_inspect else None
    generic_instruct_ok = bool(_mapping(nodes.get("neo_generic_mtmd_instruct")).get("available")) if can_inspect else None
    generic_mtmd_ok = bool(generic_loader_ok and generic_instruct_ok) if can_inspect else None

    third_party_action = "Install/update lihaoyun6/ComfyUI-llama-cpp_vlm in ComfyUI/custom_nodes, install its requirements, restart ComfyUI, then Connect/Test again."
    bridge_action = "Copy Neo Studio's bundled neo_prompt_captioning folder into ComfyUI/custom_nodes/neo_prompt_captioning, restart ComfyUI, then Connect/Test again."

    add_check("llama_loader", "llama.cpp model loader", ok=loader_ok, required_for=("prompt", "caption"), category="dependency",
              detail="llama_cpp_model_loader detected." if loader_ok else "llama_cpp_model_loader is missing.",
              next_action=third_party_action if loader_ok is False else "", code="missing_llama_loader" if loader_ok is False else "llama_loader_ready")
    add_check("llama_instruct", "llama.cpp instruct node", ok=instruct_ok, required_for=("prompt",), category="dependency",
              detail="llama_cpp_instruct_adv detected." if instruct_ok else "llama_cpp_instruct_adv is missing.",
              next_action=third_party_action if instruct_ok is False else "", code="missing_llama_instruct" if instruct_ok is False else "llama_instruct_ready")
    add_check("llama_parameters", "llama.cpp parameters node", ok=params_ok, required_for=(), category="optional_dependency",
              detail="llama_cpp_parameters detected; Neo shared generation controls can be forwarded." if params_ok else "llama_cpp_parameters is optional; without it the node pack's own generation defaults are used.",
              next_action=third_party_action if params_ok is False else "", code="optional_parameters_missing" if params_ok is False else "llama_parameters_ready")
    add_check("neo_text_output", "Neo text handoff", ok=neo_output_ok, required_for=("prompt", "caption"), category="dependency",
              detail="NeoPromptCaptionTextOutput detected." if neo_output_ok else "NeoPromptCaptionTextOutput is missing.",
              next_action=bridge_action if neo_output_ok is False else "", code="missing_neo_text_output" if neo_output_ok is False else "neo_text_output_ready")
    add_check("neo_image_input", "Neo image handoff", ok=neo_image_ok, required_for=("caption",), category="dependency",
              detail="NeoPromptCaptionImageInput detected." if neo_image_ok else "NeoPromptCaptionImageInput is missing.",
              next_action=bridge_action if neo_image_ok is False else "", code="missing_neo_image_input" if neo_image_ok is False else "neo_image_input_ready")
    add_check("neo_generic_mtmd", "Neo Generic MTMD fallback", ok=generic_mtmd_ok, required_for=(), category="optional_capability",
              detail="Neo Generic MTMD loader/instruct detected; template-driven VLM fallback is available." if generic_mtmd_ok else "Neo Generic MTMD fallback nodes are not available in the installed Neo bridge.",
              next_action=bridge_action if generic_mtmd_ok is False else "", code="generic_mtmd_ready" if generic_mtmd_ok else "generic_mtmd_missing")

    model_catalog_ok = bool(main_models) if can_inspect else None
    stale_model = bool(selected_model and selected_model not in main_models) if can_inspect else False
    model_ok = (bool(main_models) and not stale_model) if can_inspect else None
    if stale_model:
        model_detail = f"Saved model '{selected_model}' is no longer in the current Comfy model catalog."
        model_action = "Choose an installed LLM/VLM model in the Comfy backend settings, or restore that GGUF file, then Connect/Test again."
        model_code = "stale_model_selection"
    elif main_models:
        model_detail = f"{len(main_models)} LLM/VLM model(s) detected." + (f" Saved selection: {selected_model}." if selected_model else " Auto model selection is available.")
        model_action = ""
        model_code = "text_model_ready"
    else:
        model_detail = "No main LLM/VLM model is exposed by the llama.cpp loader."
        model_action = "Place at least one compatible GGUF model in ComfyUI/models/LLM, restart ComfyUI, then Connect/Test again."
        model_code = "missing_text_model"
    add_check("text_model", "LLM / VLM model", ok=model_ok, required_for=("prompt", "caption"), category="selection" if stale_model else "model",
              detail=model_detail, next_action=model_action, code=model_code)

    handler_stale = bool(selected_handler and selected_handler not in handlers) if can_inspect else False
    prompt_handler_ok = (not handler_stale) if can_inspect else None
    if handler_stale:
        handler_detail = f"Saved chat handler '{selected_handler}' is no longer exposed by the installed llama.cpp node pack."
        handler_action = "Choose a currently detected Chat Handler or switch it back to Auto, then save the backend profile."
        handler_code = "stale_chat_handler_selection"
    else:
        handler_detail = "Saved/Auto chat-handler selection is valid for text execution."
        handler_action = ""
        handler_code = "chat_handler_selection_ready"
    add_check("chat_handler_selection", "Saved chat-handler selection", ok=prompt_handler_ok, required_for=("prompt", "caption"), category="selection",
              detail=handler_detail, next_action=handler_action, code=handler_code)

    route_model = selected_model or pick_auto_vision_model(main_models)
    caption_route = resolve_caption_route(
        model=route_model,
        available_handlers=handlers,
        explicit_handler=selected_handler if not handler_stale else "",
        generic_mtmd_available=bool(generic_mtmd_ok),
    ) if can_inspect and route_model else {
        "mode": "blocked",
        "ready": False,
        "family": "unknown",
        "handler": "",
        "label": "Select/resolve a VLM model",
        "reason": "model_unresolved",
    }
    if can_inspect:
        route_mode = str(caption_route.get("mode") or "")
        route_backend_ready = bool(generic_mtmd_ok) if route_mode == "generic_mtmd" else bool(instruct_ok and accepts_images)
        route_ready = bool(caption_route.get("ready")) and not handler_stale and route_backend_ready
    else:
        route_ready = None
    if route_ready:
        route_mode = str(caption_route.get("mode") or "")
        if route_mode == "generic_mtmd":
            route_detail = f"{caption_route.get('label')} is available for {route_model}."
        else:
            route_detail = f"Compatible dedicated vision handler resolved: {caption_route.get('label')}."
        route_action = ""
        route_code = "compatible_vision_route"
    else:
        route_detail = "Neo could not resolve a compatible dedicated vision handler or Generic MTMD fallback for the selected VLM."
        route_action = bridge_action if can_inspect and not generic_mtmd_ok else "Choose a compatible Chat Handler, or use Auto with an updated Neo Generic MTMD bridge."
        route_code = "no_compatible_vision_route"
    add_check("vision_chat_handler", "Vision execution route", ok=route_ready, required_for=("caption",), category="capability",
              detail=route_detail, next_action=route_action, code=route_code)

    dedicated_image_ok = bool(accepts_images)
    image_route_ok = bool(dedicated_image_ok or generic_mtmd_ok) if can_inspect else None
    image_detail = "A compatible image-input route is available." if image_route_ok else "Neither llama_cpp_instruct_adv image input nor Neo Generic MTMD image inference is available."
    add_check("vision_input", "VLM image input", ok=image_route_ok, required_for=("caption",), category="capability",
              detail=image_detail, next_action=bridge_action if can_inspect and not image_route_ok else "", code="vlm_image_input_ready" if image_route_ok else "missing_vlm_image_input")

    stale_mmproj = bool(selected_mmproj and selected_mmproj not in mmproj) if can_inspect else False
    mmproj_ok = (bool(mmproj) and not stale_mmproj) if can_inspect else None
    if stale_mmproj:
        mmproj_detail = f"Saved vision projector '{selected_mmproj}' is no longer in the current Comfy model catalog."
        mmproj_action = "Choose an installed Vision Projector (mmproj), or restore the matching mmproj file, then Connect/Test again."
        mmproj_code = "stale_mmproj_selection"
    elif mmproj:
        mmproj_detail = f"{len(mmproj)} mmproj projector(s) detected." + (f" Saved selection: {selected_mmproj}." if selected_mmproj else " Auto projector selection is available.")
        mmproj_action = ""
        mmproj_code = "mmproj_ready"
    else:
        mmproj_detail = "No VLM mmproj projector is available."
        mmproj_action = "Place the matching mmproj file for your VLM in ComfyUI/models/LLM, restart ComfyUI, then Connect/Test again."
        mmproj_code = "missing_mmproj"
    add_check("mmproj", "Vision projector (mmproj)", ok=mmproj_ok, required_for=("caption",), category="selection" if stale_mmproj else "model",
              detail=mmproj_detail, next_action=mmproj_action, code=mmproj_code)

    def route_ready(route: str) -> bool:
        for check in checks:
            if route not in check.get("required_for", []):
                continue
            if check.get("ok") is not True:
                return False
        return True

    prompt_ready = route_ready("prompt")
    caption_ready = route_ready("caption")

    def first_blocker(route: str) -> dict[str, Any] | None:
        for check in checks:
            if route in check.get("required_for", []) and check.get("ok") is not True:
                return check
        return None

    prompt_blocker = first_blocker("prompt")
    caption_blocker = first_blocker("caption")
    stale = {}
    if stale_model:
        stale["comfy_llamacpp_model"] = selected_model
    if stale_mmproj:
        stale["comfy_llamacpp_mmproj"] = selected_mmproj
    if handler_stale:
        stale["comfy_llamacpp_chat_handler"] = selected_handler

    actions: list[str] = []
    for check in checks:
        action = str(check.get("next_action") or "").strip()
        if action and action not in actions and check.get("ok") is not True:
            actions.append(action)
    if reachable and object_info_available and not actions:
        actions.append("Backend readiness is complete. Prompt and Caption routes may run according to the ready states below.")

    if not reachable:
        overall = "offline_or_not_tested"
    elif not object_info_available:
        overall = "catalog_unavailable"
    elif prompt_ready and caption_ready:
        overall = "prompt_and_caption_ready"
    elif prompt_ready:
        overall = "prompt_ready_caption_blocked"
    else:
        overall = "blocked"

    return {
        "schema_id": READINESS_SCHEMA_ID,
        "phase": "prompt_captioning_comfy_llamacpp_phase10_1_mtmd",
        "overall_status": overall,
        "prompt_ready": prompt_ready,
        "caption_ready": caption_ready,
        "text_ready": prompt_ready,
        "vision_ready": caption_ready,
        "checks": checks,
        "prompt_blocker": prompt_blocker or {},
        "caption_blocker": caption_blocker or {},
        "stale_selections": stale,
        "actions": actions,
        "selected": {
            "model": selected_model,
            "mmproj": selected_mmproj,
            "chat_handler": selected_handler,
        },
        "caption_route": caption_route,
        "catalog_counts": {
            "models": len(main_models),
            "mmproj": len(mmproj),
            "usable_chat_handlers": len(usable_handlers),
            "generic_mtmd": 1 if generic_mtmd_ok else 0,
        },
        "install_paths": {
            "third_party_nodes": "ComfyUI/custom_nodes/ComfyUI-llama-cpp_vlm",
            "neo_bridge_nodes": "ComfyUI/custom_nodes/neo_prompt_captioning",
            "models": "ComfyUI/models/LLM",
        },
    }

def comfy_llamacpp_models_payload(discovery: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "models": [], "diffusion_models": [], "text_encoders": [], "qwen_text_encoders": [], "vaes": [],
        "samplers": [], "schedulers": [], "gguf_models": [], "gguf_text_encoders": [],
        "gguf_text_encoder_primary": [], "gguf_text_encoder_secondary": [], "gguf_vaes": [], "mmproj": [],
        "loras": [], "embeddings": [], "ip_adapter_models": [], "clip_vision_models": [],
        "ip_adapter_faceid_models": [], "upscalers": [], "text_models": [], "vision_models": [],
    }
    clean = _mapping(discovery)
    records = _mapping(clean.get("model_records"))
    result["text_models"] = list(records.get("text_models") or [])
    result["gguf_models"] = list(records.get("gguf_models") or [])
    result["mmproj"] = list(records.get("mmproj") or [])
    # Keep generic `models` useful for Admin summaries without pretending every
    # main model has a matching vision projector.
    result["models"] = list(result["text_models"])
    return result


__all__ = ["discover_comfy_llamacpp", "evaluate_comfy_llamacpp_readiness", "comfy_llamacpp_models_payload"]
