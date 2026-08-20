from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.providers.schema import CompiledJob, NeoJob

MASKED_MODES = {"inpaint", "outpaint"}
NATIVE_ENGINE = "native"
LANPAINT_ENGINE = "lanpaint"
CROP_NODE = "InpaintCropImproved"
STITCH_NODE = "InpaintStitchImproved"
CROP_STITCH_PACK = "ComfyUI-Inpaint-CropAndStitch"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled", "none"}


def masked_edit_engine(params: Mapping[str, Any] | None, mode: str) -> str:
    values = _mapping(params)
    if str(mode or "").strip().lower() not in MASKED_MODES:
        return NATIVE_ENGINE
    raw = values.get("masked_edit_engine", values.get("inpaint_engine", values.get("engine", NATIVE_ENGINE)))
    value = str(raw or NATIVE_ENGINE).strip().lower().replace("-", "_")
    return LANPAINT_ENGINE if value in {"lanpaint", "lan_paint"} else NATIVE_ENGINE


def crop_stitch_enabled(params: Mapping[str, Any] | None) -> bool:
    values = _mapping(params)
    return _truthy(values.get("crop_stitch_enabled", values.get("masked_crop_stitch_enabled")), False)


def _next_id(workflow: Mapping[str, Any]) -> int:
    ints = []
    for key in workflow:
        try:
            ints.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return (max(ints) + 1) if ints else 1


def _node_ref(value: Any) -> list[Any] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2 and isinstance(value[0], (str, int)):
        return [str(value[0]), value[1]]
    return None


def _find_nodes(workflow: Mapping[str, Any], class_type: str) -> list[str]:
    return [str(node_id) for node_id, node in workflow.items() if isinstance(node, Mapping) and str(node.get("class_type") or "") == class_type]


def _find_sampler_id(workflow: Mapping[str, Any], actual: Mapping[str, Any]) -> str:
    preferred = str(actual.get("_neo_sampler_node_id") or "").strip()
    if preferred and preferred in workflow:
        return preferred
    priorities = ("KSampler", "KSamplerAdvanced", "LanPaint_KSampler")
    for class_type in priorities:
        nodes = _find_nodes(workflow, class_type)
        if nodes:
            return nodes[-1]
    return ""


def _find_decode_for_sampler(workflow: Mapping[str, Any], sampler_id: str) -> tuple[str, list[Any] | None]:
    target = [str(sampler_id), 0]
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping) or str(node.get("class_type") or "") != "VAEDecode":
            continue
        inputs = _mapping(node.get("inputs"))
        if _node_ref(inputs.get("samples")) == target:
            return str(node_id), _node_ref(inputs.get("vae"))
    # Some workflows insert a small latent wrapper after the sampler. Prefer the
    # final VAEDecode as a conservative fallback and keep its explicit VAE ref.
    decoders = _find_nodes(workflow, "VAEDecode")
    if decoders:
        node_id = decoders[-1]
        return node_id, _node_ref(_mapping(workflow[node_id].get("inputs")).get("vae"))
    return "", None


def _find_existing_source_mask_refs(workflow: Mapping[str, Any], actual: Mapping[str, Any], mode: str) -> tuple[list[Any] | None, list[Any] | None]:
    # Existing family compilers already own source scaling, target inversion,
    # mask growth and outpaint padding. Reuse those refs rather than rebuilding
    # family-specific preprocessing from guesses.
    if mode == "outpaint":
        pads = _find_nodes(workflow, "ImagePadForOutpaint")
        if pads:
            pad = pads[-1]
            return [pad, 0], [pad, 1]

    conditioners = _find_nodes(workflow, "InpaintModelConditioning")
    if conditioners:
        inputs = _mapping(workflow[conditioners[-1]].get("inputs"))
        pixels = _node_ref(inputs.get("pixels"))
        mask = _node_ref(inputs.get("mask"))
        if pixels and mask:
            return pixels, mask

    noise_masks = _find_nodes(workflow, "SetLatentNoiseMask")
    if noise_masks:
        inputs = _mapping(workflow[noise_masks[-1]].get("inputs"))
        mask = _node_ref(inputs.get("mask"))
        samples = _node_ref(inputs.get("samples"))
        if samples and samples[0] in workflow:
            encoder = workflow[samples[0]]
            if isinstance(encoder, Mapping) and str(encoder.get("class_type") or "") in {"VAEEncode", "VAEEncodeForInpaint"}:
                pixels = _node_ref(_mapping(encoder.get("inputs")).get("pixels"))
                if pixels and mask:
                    return pixels, mask

    encoders = _find_nodes(workflow, "VAEEncodeForInpaint")
    if encoders:
        inputs = _mapping(workflow[encoders[-1]].get("inputs"))
        pixels = _node_ref(inputs.get("pixels"))
        mask = _node_ref(inputs.get("mask"))
        if pixels and mask:
            return pixels, mask

    source_name = str(actual.get("comfy_source_image_name") or actual.get("source_image_name") or "").strip()
    source_ref: list[Any] | None = None
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping) or str(node.get("class_type") or "") != "LoadImage":
            continue
        image = str(_mapping(node.get("inputs")).get("image") or "").strip()
        if not source_name or image == source_name:
            source_ref = [str(node_id), 0]
            if source_name and image == source_name:
                break

    mask_ref: list[Any] | None = None
    masks = _find_nodes(workflow, "LoadImageMask")
    if masks:
        mask_ref = [masks[-1], 0]
    if not mask_ref:
        to_masks = _find_nodes(workflow, "ImageToMask")
        if to_masks:
            mask_ref = [to_masks[-1], 0]
    return source_ref, mask_ref


def _available_nodes(backend_capabilities: Mapping[str, Any] | None) -> set[str]:
    caps = _mapping(backend_capabilities)
    names = set(_mapping(caps.get("object_info_node_inputs")).keys())
    names.update(str(x) for x in (_mapping(caps.get("masked_edit_node_diagnostics")).get("available_nodes") or []) if x)
    return names


def _crop_inputs(params: Mapping[str, Any], image_ref: list[Any], mask_ref: list[Any]) -> dict[str, Any]:
    values = _mapping(params)
    width = max(64, int(values.get("crop_stitch_target_width") or values.get("width") or 1024))
    height = max(64, int(values.get("crop_stitch_target_height") or values.get("height") or 1024))
    max_resolution = max(16384, width, height)
    return {
        "image": list(image_ref),
        "mask": list(mask_ref),
        "downscale_algorithm": str(values.get("crop_stitch_downscale_algorithm") or "lanczos"),
        "upscale_algorithm": str(values.get("crop_stitch_upscale_algorithm") or "bicubic"),
        "preresize": bool(values.get("crop_stitch_preresize", False)),
        "preresize_mode": str(values.get("crop_stitch_preresize_mode") or "ensure minimum resolution"),
        "preresize_min_width": int(values.get("crop_stitch_preresize_min_width") or width),
        "preresize_min_height": int(values.get("crop_stitch_preresize_min_height") or height),
        "preresize_max_width": int(values.get("crop_stitch_preresize_max_width") or max_resolution),
        "preresize_max_height": int(values.get("crop_stitch_preresize_max_height") or max_resolution),
        "mask_fill_holes": bool(values.get("crop_stitch_fill_holes", True)),
        "mask_expand_pixels": max(0, int(values.get("crop_stitch_mask_expand") or 0)),
        # Target inversion is normalized by the family compiler before this wrapper.
        "mask_invert": False,
        "mask_blend_pixels": max(0, min(64, int(values.get("crop_stitch_blend_pixels") or 32))),
        "mask_hipass_filter": float(values.get("crop_stitch_hipass_filter") if values.get("crop_stitch_hipass_filter") is not None else 0.1),
        # Neo owns outpaint padding through ImagePadForOutpaint. The crop node only
        # sees the already padded canvas/mask so two outpaint systems cannot stack.
        "extend_for_outpainting": False,
        "extend_up_factor": 1.0,
        "extend_down_factor": 1.0,
        "extend_left_factor": 1.0,
        "extend_right_factor": 1.0,
        "context_from_mask_extend_factor": float(values.get("crop_stitch_context_factor") or 1.2),
        "output_resize_to_target_size": bool(values.get("crop_stitch_force_target_size", True)),
        "output_target_width": width,
        "output_target_height": height,
        "output_padding": str(int(values.get("crop_stitch_output_padding") or 32)),
        "device_mode": str(values.get("crop_stitch_device_mode") or "cpu (compatible)"),
    }


def _rewire_image_outputs(workflow: dict[str, Any], old_ref: list[Any], new_ref: list[Any]) -> int:
    rewired = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for key in ("images", "image"):
            if _node_ref(inputs.get(key)) == old_ref and str(node.get("class_type") or "") in {"PreviewImage", "SaveImage", "ImageSave"}:
                inputs[key] = list(new_ref)
                rewired += 1
    return rewired


def _output_image_refs(workflow: Mapping[str, Any]) -> list[list[Any]]:
    refs: list[list[Any]] = []
    for node in workflow.values():
        if not isinstance(node, Mapping):
            continue
        if str(node.get("class_type") or "") not in {"PreviewImage", "SaveImage", "ImageSave"}:
            continue
        inputs = _mapping(node.get("inputs"))
        for key in ("images", "image"):
            ref = _node_ref(inputs.get(key))
            if ref:
                refs.append(ref)
                break
    return refs


def _primary_output_image_ref(workflow: Mapping[str, Any], fallback: list[Any] | None = None) -> list[Any] | None:
    refs = _output_image_refs(workflow)
    if not refs:
        return list(fallback) if fallback else None
    unique: list[list[Any]] = []
    for ref in refs:
        if ref not in unique:
            unique.append(ref)
    if len(unique) == 1:
        return list(unique[0])
    return list(unique[-1])


def _rewrite_workflow_input_refs(
    workflow: dict[str, Any],
    old_ref: list[Any],
    new_ref: list[Any],
    *,
    exclude_node_ids: set[str] | None = None,
) -> int:
    replaced = 0
    blocked = {str(node_id) for node_id in (exclude_node_ids or set())}
    for node_id, node in workflow.items():
        if str(node_id) in blocked or not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else None
        if not isinstance(inputs, dict):
            continue
        next_inputs = _replace_ref(deepcopy(inputs), old_ref, new_ref)
        if next_inputs != inputs:
            node["inputs"] = next_inputs
            replaced += 1
    return replaced


def _update_finish_image_refs(actual: dict[str, Any], old_ref: list[Any], new_ref: list[Any]) -> None:
    # Finish contracts may carry their image anchor at the top level today, but
    # extension contracts are intentionally allowed to grow nested metadata.
    # Replace the concrete ref recursively so Crop & Stitch cannot leave a stale
    # decode anchor hidden inside an otherwise-valid downstream contract.
    for key in ("_neo_adetailer_route_contract", "_neo_high_res_route_contract"):
        contract = actual.get(key)
        if isinstance(contract, dict):
            actual[key] = _replace_ref(deepcopy(contract), old_ref, new_ref)


def patch_native_masked_workflow(
    compiled: CompiledJob,
    *,
    job: NeoJob,
    route: Any,
    backend_capabilities: Mapping[str, Any] | None = None,
) -> CompiledJob:
    mode = str(getattr(route, "mode", None) or job.mode or "").strip().lower()
    if mode not in MASKED_MODES:
        return compiled
    backend_payload = dict(compiled.backend_payload or {})
    workflow = deepcopy(backend_payload.get("prompt")) if isinstance(backend_payload.get("prompt"), dict) else None
    if not isinstance(workflow, dict):
        return compiled
    actual = dict(backend_payload.get("actual_params") or job.params or {})
    if masked_edit_engine(actual, mode) != NATIVE_ENGINE:
        return compiled

    sampler_id = _find_sampler_id(workflow, actual)
    sampler = workflow.get(sampler_id) if sampler_id else None
    sampler_inputs = _mapping(sampler.get("inputs")) if isinstance(sampler, Mapping) else {}
    required_sampler_inputs = {"model", "positive", "negative", "latent_image"}
    if not sampler_id or not required_sampler_inputs.issubset(sampler_inputs):
        # Custom guider/sampler pipelines are left as their family compiler emitted
        # them until Phase 4 multi-sampler routing defines an equivalent masked hook.
        actual["masked_edit_engine"] = NATIVE_ENGINE
        actual["masked_edit_engine_state"] = "family_native_graph_preserved_nonstandard_sampler"
        backend_payload["actual_params"] = actual
        return compiled.model_copy(update={"backend_payload": backend_payload})

    source_ref, mask_ref = _find_existing_source_mask_refs(workflow, actual, mode)
    decode_id, vae_ref = _find_decode_for_sampler(workflow, sampler_id)
    if not source_ref or not mask_ref or not decode_id or not vae_ref:
        actual["masked_edit_engine"] = NATIVE_ENGINE
        actual["masked_edit_engine_state"] = "family_native_graph_preserved_missing_generic_anchor"
        backend_payload["actual_params"] = actual
        return compiled.model_copy(update={"backend_payload": backend_payload})

    use_crop = crop_stitch_enabled(actual)
    if use_crop:
        available = _available_nodes(backend_capabilities)
        if CROP_NODE not in available or STITCH_NODE not in available:
            validation = _mapping(backend_payload.get("validation"))
            errors = list(validation.get("errors") or [])
            errors.append(
                f"Crop & Stitch requires {CROP_STITCH_PACK} ({CROP_NODE} + {STITCH_NODE}) on the connected ComfyUI backend."
            )
            validation["errors"] = errors
            validation["ok"] = False
            backend_payload["validation"] = validation
            actual.update({
                "masked_edit_engine": NATIVE_ENGINE,
                "crop_stitch_enabled": True,
                "crop_stitch_state": "blocked_missing_custom_nodes",
                "crop_stitch_required_pack": CROP_STITCH_PACK,
                "crop_stitch_required_nodes": [CROP_NODE, STITCH_NODE],
            })
            backend_payload["actual_params"] = actual
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})

    next_id = _next_id(workflow)
    conditioning_image_ref = list(source_ref)
    conditioning_mask_ref = list(mask_ref)
    stitcher_ref: list[Any] | None = None
    crop_id = ""
    if use_crop:
        crop_id = str(next_id)
        workflow[crop_id] = {"class_type": CROP_NODE, "inputs": _crop_inputs(actual, source_ref, mask_ref)}
        next_id += 1
        stitcher_ref = [crop_id, 0]
        conditioning_image_ref = [crop_id, 1]
        conditioning_mask_ref = [crop_id, 2]

    existing_conditioners = _find_nodes(workflow, "InpaintModelConditioning")
    if existing_conditioners:
        conditioner_id = existing_conditioners[-1]
        conditioner = workflow[conditioner_id]
        cin = _mapping(conditioner.get("inputs"))
        cin["positive"] = deepcopy(sampler_inputs["positive"])
        cin["negative"] = deepcopy(sampler_inputs["negative"])
        cin["vae"] = list(vae_ref)
        cin["pixels"] = list(conditioning_image_ref)
        cin["mask"] = list(conditioning_mask_ref)
        conditioner["inputs"] = cin
    else:
        conditioner_id = str(next_id)
        workflow[conditioner_id] = {
            "class_type": "InpaintModelConditioning",
            "inputs": {
                "positive": deepcopy(sampler_inputs["positive"]),
                "negative": deepcopy(sampler_inputs["negative"]),
                "vae": list(vae_ref),
                "pixels": list(conditioning_image_ref),
                "mask": list(conditioning_mask_ref),
                "noise_mask": True,
            },
        }
        next_id += 1

    model_ref = _node_ref(sampler_inputs.get("model")) or deepcopy(sampler_inputs.get("model"))
    if isinstance(model_ref, list) and model_ref[0] in workflow and str(workflow[model_ref[0]].get("class_type") or "") == "DifferentialDiffusion":
        differential_ref = model_ref
    else:
        differential_id = str(next_id)
        workflow[differential_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": deepcopy(sampler_inputs["model"])}}
        next_id += 1
        differential_ref = [differential_id, 0]

    latent_ref: list[Any] = [conditioner_id, 2]
    batch_count = max(1, int(actual.get("batch_count", actual.get("batch_size", 1)) or 1))
    if batch_count > 1:
        repeat_id = str(next_id)
        workflow[repeat_id] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": list(latent_ref), "amount": batch_count}}
        next_id += 1
        latent_ref = [repeat_id, 0]

    sampler_inputs["model"] = list(differential_ref)
    sampler_inputs["positive"] = [conditioner_id, 0]
    sampler_inputs["negative"] = [conditioner_id, 1]
    sampler_inputs["latent_image"] = list(latent_ref)
    sampler["inputs"] = sampler_inputs

    decode_ref = [decode_id, 0]
    existing_output_ref = _primary_output_image_ref(workflow, decode_ref) or list(decode_ref)
    final_ref = list(decode_ref)
    stitch_id = ""
    output_rewire_count = 0
    workflow_rewire_count = 0
    if use_crop and stitcher_ref:
        stitch_id = str(next_id)
        workflow[stitch_id] = {"class_type": STITCH_NODE, "inputs": {"stitcher": list(stitcher_ref), "inpainted_image": list(decode_ref)}}
        final_ref = [stitch_id, 0]
        if existing_output_ref == decode_ref:
            output_rewire_count = _rewire_image_outputs(workflow, decode_ref, final_ref)
        else:
            workflow_rewire_count = _rewrite_workflow_input_refs(workflow, existing_output_ref, final_ref, exclude_node_ids={stitch_id})
        _update_finish_image_refs(actual, decode_ref, final_ref)
        if existing_output_ref != decode_ref:
            _update_finish_image_refs(actual, existing_output_ref, final_ref)
        output_refs_after = _output_image_refs(workflow)
        if final_ref not in output_refs_after:
            validation = _mapping(backend_payload.get("validation"))
            errors = list(validation.get("errors") or [])
            errors.append(
                "Native Crop & Stitch could not claim final image ownership; the stitched output is not wired to Preview/Save outputs."
            )
            validation["errors"] = errors
            validation["ok"] = False
            backend_payload["validation"] = validation
            actual.update({
                "masked_edit_engine": NATIVE_ENGINE,
                "crop_stitch_enabled": True,
                "crop_stitch_state": "blocked_output_handoff_unreachable",
                "crop_stitch_required_pack": CROP_STITCH_PACK,
                "crop_stitch_required_nodes": [CROP_NODE, STITCH_NODE],
                "_neo_masked_edit_previous_output_ref": existing_output_ref,
                "_neo_masked_edit_output_ref": final_ref,
            })
            backend_payload["prompt"] = workflow
            backend_payload["actual_params"] = actual
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})

    actual.update({
        "masked_edit_engine": NATIVE_ENGINE,
        "inpaint_engine": NATIVE_ENGINE,
        "masked_edit_mode": mode,
        "masked_edit_engine_state": "comfy_base_inpaint_model_conditioning",
        "crop_stitch_enabled": bool(use_crop),
        "crop_stitch_provider": CROP_STITCH_PACK if use_crop else "disabled",
        "crop_stitch_state": (
            "active_output_owner_replaced_direct_decode"
            if use_crop and existing_output_ref == decode_ref
            else ("active_output_owner_replaced_family_terminal" if use_crop else "disabled")
        ),
        "crop_stitch_nodes": ([crop_id, stitch_id] if use_crop else []),
        "_neo_masked_edit_conditioner_node_id": conditioner_id,
        "_neo_masked_edit_previous_output_ref": existing_output_ref,
        "_neo_masked_edit_output_ref": final_ref,
        "_neo_masked_edit_output_rewire_count": output_rewire_count,
        "_neo_masked_edit_workflow_rewire_count": workflow_rewire_count,
    })
    backend_payload["prompt"] = workflow
    backend_payload["actual_params"] = actual
    notes = list(backend_payload.get("phase_notes") or [])
    notes.append(
        f"Masked Edit Engine: Native Comfy InpaintModelConditioning + DifferentialDiffusion for {mode}; Crop & Stitch {'enabled' if use_crop else 'disabled'}."
    )
    backend_payload["phase_notes"] = notes
    return compiled.model_copy(update={"backend_payload": backend_payload})


def _role_id(actual: Mapping[str, Any], role: str) -> str:
    roles = _mapping(actual.get("lanpaint_node_roles"))
    for node_id, role_id in roles.items():
        if str(role_id) == role:
            return str(node_id)
    return ""


def _replace_ref(value: Any, old_ref: list[Any], new_ref: list[Any]) -> Any:
    ref = _node_ref(value)
    if ref == old_ref:
        return list(new_ref)
    if isinstance(value, dict):
        return {k: _replace_ref(v, old_ref, new_ref) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_ref(v, old_ref, new_ref) for v in value]
    return value


def _find_first_by_class(workflow: Mapping[str, Any], class_type: str) -> str:
    nodes = _find_nodes(workflow, class_type)
    return nodes[0] if nodes else ""


def _find_last_by_class(workflow: Mapping[str, Any], class_type: str) -> str:
    nodes = _find_nodes(workflow, class_type)
    return nodes[-1] if nodes else ""


def _prune_to_outputs(workflow: dict[str, Any]) -> dict[str, Any]:
    output_classes = {"PreviewImage", "SaveImage", "ImageSave", "SaveLatent"}
    roots = [str(i) for i, n in workflow.items() if isinstance(n, Mapping) and str(n.get("class_type") or "") in output_classes]
    if not roots:
        return workflow
    reachable: set[str] = set()
    stack = roots[:]
    while stack:
        node_id = stack.pop()
        if node_id in reachable or node_id not in workflow:
            continue
        reachable.add(node_id)
        node = workflow[node_id]
        inputs = _mapping(node.get("inputs")) if isinstance(node, Mapping) else {}
        pending = [inputs]
        while pending:
            current = pending.pop()
            if isinstance(current, Mapping):
                pending.extend(current.values())
            elif isinstance(current, list):
                ref = _node_ref(current)
                if ref and ref[0] in workflow:
                    stack.append(ref[0])
                else:
                    pending.extend(current)
    return {k: v for k, v in workflow.items() if str(k) in reachable}


def patch_lanpaint_masked_workflow(compiled: CompiledJob, *, job: NeoJob, route: Any) -> CompiledJob:
    mode = str(getattr(route, "mode", None) or job.mode or "inpaint").strip().lower()
    if mode not in MASKED_MODES:
        return compiled
    backend_payload = dict(compiled.backend_payload or {})
    workflow = deepcopy(backend_payload.get("prompt")) if isinstance(backend_payload.get("prompt"), dict) else None
    if not isinstance(workflow, dict):
        return compiled
    actual = dict(backend_payload.get("actual_params") or job.params or {})
    if masked_edit_engine(actual, mode) != LANPAINT_ENGINE and str(getattr(route, "engine", "") or "") != LANPAINT_ENGINE:
        return compiled

    source_id = _role_id(actual, "source_image") or _find_first_by_class(workflow, "LoadImage")
    crop_id = _role_id(actual, "crop_context") or _find_first_by_class(workflow, "CropByMask")
    resize_id = _role_id(actual, "processing_resize") or _find_first_by_class(workflow, "ImageResizeKJv2")
    sample_mask_id = _role_id(actual, "sampling_mask_refine") or _find_first_by_class(workflow, "GrowMaskWithBlur")
    encode_id = _role_id(actual, "latent_encode") or _find_first_by_class(workflow, "VAEEncode")
    noise_id = _role_id(actual, "latent_noise_mask") or _find_first_by_class(workflow, "SetLatentNoiseMask")
    decode_id = _role_id(actual, "latent_decode") or _find_last_by_class(workflow, "VAEDecode")
    composite_id = _role_id(actual, "stitch_composite") or _find_last_by_class(workflow, "ImageCompositeMasked")
    output_id = _role_id(actual, "output_handoff") or _find_last_by_class(workflow, "PreviewImage")

    if not all((source_id, encode_id, noise_id, decode_id, output_id)):
        actual["masked_edit_engine_state"] = "lanpaint_graph_preserved_missing_toggle_anchors"
        backend_payload["actual_params"] = actual
        return compiled.model_copy(update={"backend_payload": backend_payload})

    source_ref: list[Any] = [source_id, 0]
    # Resolve the original inpaint mask before the LanPaint crop/refine stages.
    mask_ref: list[Any] | None = None
    if crop_id and crop_id in workflow:
        mask_ref = _node_ref(_mapping(workflow[crop_id].get("inputs")).get("mask"))
    if not mask_ref and noise_id in workflow:
        mask_ref = _node_ref(_mapping(workflow[noise_id].get("inputs")).get("mask"))

    next_id = _next_id(workflow)
    if mode == "outpaint":
        outpaint = normalize_outpaint_payload(actual, default_width=int(actual.get("width") or 1024), default_height=int(actual.get("height") or 1024))
        if outpaint_padding_total(outpaint) <= 0:
            validation = _mapping(backend_payload.get("validation"))
            errors = list(validation.get("errors") or [])
            errors.append("LanPaint outpaint requires padding on at least one side.")
            validation["errors"] = errors
            validation["ok"] = False
            backend_payload["validation"] = validation
            backend_payload["actual_params"] = actual
            return compiled.model_copy(update={"compile_status": "mock_compiled", "backend_payload": backend_payload})
        padding = _mapping(outpaint.get("padding")); mask = _mapping(outpaint.get("mask"))
        pad_id = str(next_id); next_id += 1
        workflow[pad_id] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {
                "image": list(source_ref),
                "left": int(padding.get("left", 0) or 0), "top": int(padding.get("top", 0) or 0),
                "right": int(padding.get("right", 0) or 0), "bottom": int(padding.get("bottom", 0) or 0),
                "feathering": int(mask.get("feather", 16) or 16),
            },
        }
        source_ref = [pad_id, 0]
        mask_ref = [pad_id, 1]
        actual["outpaint_payload"] = outpaint
        actual["_neo_outpaint_contract"] = outpaint
        actual["_neo_lanpaint_outpaint_pad_node_id"] = pad_id
        if crop_id and crop_id in workflow:
            inputs = _mapping(workflow[crop_id].get("inputs")); inputs["image"] = list(source_ref); inputs["mask"] = list(mask_ref); workflow[crop_id]["inputs"] = inputs
        if composite_id and composite_id in workflow:
            inputs = _mapping(workflow[composite_id].get("inputs")); inputs["destination"] = list(source_ref); workflow[composite_id]["inputs"] = inputs

    if not mask_ref:
        actual["masked_edit_engine_state"] = "lanpaint_graph_preserved_missing_mask_anchor"
        backend_payload["actual_params"] = actual
        return compiled.model_copy(update={"backend_payload": backend_payload})

    use_crop = crop_stitch_enabled(actual)
    if not use_crop:
        old_image_ref = [resize_id, 0] if resize_id else None
        old_mask_ref = [sample_mask_id, 0] if sample_mask_id else None
        # Route every consumer of the processed crop/mask to the full source canvas.
        if old_image_ref:
            workflow = _replace_ref(workflow, old_image_ref, source_ref)
        if old_mask_ref:
            workflow = _replace_ref(workflow, old_mask_ref, mask_ref)
        if encode_id in workflow:
            inputs = _mapping(workflow[encode_id].get("inputs")); inputs["pixels"] = list(source_ref); workflow[encode_id]["inputs"] = inputs
        if noise_id in workflow:
            inputs = _mapping(workflow[noise_id].get("inputs")); inputs["mask"] = list(mask_ref); workflow[noise_id]["inputs"] = inputs
        # Crop/restore/composite are bypassed. The sampler still uses LanPaint,
        # but the decoded full-frame result becomes the output directly.
        if output_id in workflow:
            inputs = _mapping(workflow[output_id].get("inputs")); inputs["images"] = [decode_id, 0]; workflow[output_id]["inputs"] = inputs
        workflow = _prune_to_outputs(workflow)
        actual["crop_stitch_provider"] = "disabled"
    else:
        # Existing LanPaint uses its own proven crop/stitch graph; no additional
        # custom pack is injected or stacked on top of it.
        actual["crop_stitch_provider"] = "lanpaint_internal_crop_stitch"
        if mode == "outpaint":
            # The legacy family emitter may contain a disconnected placeholder
            # LoadImageMask used only to satisfy its old inpaint constructor.
            # Prune from actual output roots so Comfy never receives that fake file.
            workflow = _prune_to_outputs(workflow)

    if mode == "outpaint" and actual.pop("_neo_lanpaint_outpaint_mask_placeholder", False):
        actual.pop("comfy_mask_image_name", None)
        actual["mask_image_name"] = "generated_by_ImagePadForOutpaint"

    actual.update({
        "masked_edit_engine": LANPAINT_ENGINE,
        "inpaint_engine": LANPAINT_ENGINE,
        "masked_edit_mode": mode,
        "masked_edit_engine_state": "lanpaint_masked_generation",
        "crop_stitch_enabled": bool(use_crop),
        "workflow_type": f"image.{mode}.lanpaint",
    })
    route_meta = actual.get("lanpaint_route") if isinstance(actual.get("lanpaint_route"), dict) else {}
    route_meta = {**route_meta, "mode": mode, "engine": LANPAINT_ENGINE, "workflow_type": f"image.{mode}.lanpaint"}
    actual["lanpaint_route"] = route_meta
    lora_profile = actual.get("_neo_lora_patch_profile") if isinstance(actual.get("_neo_lora_patch_profile"), dict) else None
    if lora_profile:
        lora_profile = deepcopy(lora_profile)
        profile_route = _mapping(lora_profile.get("route"))
        old_key = str(lora_profile.get("compatibility_route_key") or profile_route.get("compatibility_route_key") or profile_route.get("route_key") or "")
        workflow_key = f"{str(getattr(route, 'family', '') or job.family or '').strip()}:{str(getattr(route, 'loader', '') or job.loader or '').strip()}:{mode}:lanpaint"
        profile_route.update({"mode": mode, "workflow_mode": mode, "engine": LANPAINT_ENGINE, "workflow_route_key": workflow_key})
        if old_key:
            profile_route["compatibility_route_key"] = old_key
            lora_profile["compatibility_route_key"] = old_key
        lora_profile["workflow_route_key"] = workflow_key
        lora_profile["workflow_engine"] = LANPAINT_ENGINE
        lora_profile["route"] = profile_route
        actual["_neo_lora_patch_profile"] = lora_profile
    if isinstance(actual.get("lanpaint_lora_route"), dict):
        lora_route = deepcopy(actual["lanpaint_lora_route"])
        lora_route.update({"mode": mode, "workflow_mode": mode, "engine": LANPAINT_ENGINE, "workflow_route_key": f"{str(getattr(route, 'family', '') or job.family or '').strip()}:{str(getattr(route, 'loader', '') or job.loader or '').strip()}:{mode}:lanpaint"})
        actual["lanpaint_lora_route"] = lora_route
    backend_payload["prompt"] = workflow
    backend_payload["actual_params"] = actual
    compile_route = _mapping(backend_payload.get("compile_route"))
    if compile_route:
        compile_route.update({"mode": mode, "engine": LANPAINT_ENGINE, "workflow_type": f"image.{mode}.lanpaint"})
        backend_payload["compile_route"] = compile_route
    notes = list(backend_payload.get("phase_notes") or [])
    notes.append(f"LanPaint {mode} active; existing LanPaint crop/stitch {'enabled' if use_crop else 'bypassed for full-frame masked sampling'}.")
    backend_payload["phase_notes"] = notes
    return compiled.model_copy(update={"backend_payload": backend_payload})


def patch_masked_edit_workflow(
    compiled: CompiledJob,
    *,
    job: NeoJob,
    route: Any,
    backend_capabilities: Mapping[str, Any] | None = None,
) -> CompiledJob:
    mode = str(getattr(route, "mode", None) or job.mode or "").strip().lower()
    if mode not in MASKED_MODES:
        return compiled
    params = _mapping(job.params)
    krea_edit_engine = str(params.get("krea2_edit_engine") or params.get("edit_engine") or "").strip().lower().replace("-", "_")
    if str(getattr(route, "family", "") or job.family or "").strip().lower() in {"krea2", "krea2_turbo"} and krea_edit_engine in {"identity_edit", "krea2_identity_edit", "identity"}:
        # The Krea2Edit node pack owns its own clean-target diffusion path. Injecting
        # InpaintModelConditioning/DifferentialDiffusion here would replace the
        # training-matched target latent and break the dual-conditioning recipe.
        return compiled
    engine = masked_edit_engine(job.params, mode)
    if str(getattr(route, "engine", "") or "").strip().lower() == LANPAINT_ENGINE or engine == LANPAINT_ENGINE:
        return patch_lanpaint_masked_workflow(compiled, job=job, route=route)
    return patch_native_masked_workflow(compiled, job=job, route=route, backend_capabilities=backend_capabilities)


__all__ = [
    "CROP_NODE", "STITCH_NODE", "CROP_STITCH_PACK", "MASKED_MODES",
    "crop_stitch_enabled", "masked_edit_engine", "patch_masked_edit_workflow",
    "patch_native_masked_workflow", "patch_lanpaint_masked_workflow",
]
