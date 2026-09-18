from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

SCHEMA_ID = "neo.image.krea2_anypaint_runtime.v1"
AUTHORITY = "neo_app.image.krea2_anypaint_runtime"
ENGINE_ID = "krea2_anypaint"

EXPECTED_NODE_CLASSES = {
    "prepare": "Krea2AnyPaintPrepare",
    "encode": "Krea2AnyPaintEncode",
    "adapter": "LoraLoaderModelOnly",
    "model_patch": "Krea2AnyPaintModelPatch",
    "sampler": "KSampler",
    "decode": "VAEDecode",
    "output": "PreviewImage",
}

FORBIDDEN_NATIVE_NODE_CLASSES = {
    "ImageCompositeMasked",
    "DifferentialDiffusion",
    "InpaintModelConditioning",
    "SetLatentNoiseMask",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _ref(value: Any) -> list[Any] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [str(value[0]), value[1]]
    return None


def _same_ref(value: Any, expected: Any) -> bool:
    return _ref(value) == _ref(expected)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _normalized_asset(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().casefold()


def is_krea2_anypaint_runtime(actual_params: Mapping[str, Any] | None) -> bool:
    params = _mapping(actual_params)
    engine = str(params.get("masked_edit_engine") or params.get("inpaint_engine") or "").strip().lower().replace("-", "_")
    compiler = str(params.get("krea2_anypaint_compiler_id") or "").strip().lower()
    return engine == ENGINE_ID or "krea2_anypaint" in compiler


def _issue(code: str, message: str, *, severity: str = "error", node_id: str = "", field: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message, "severity": severity}
    if node_id:
        payload["node_id"] = str(node_id)
    if field:
        payload["field"] = field
    return payload


def validate_krea2_anypaint_submitted_graph(
    prompt_graph: Mapping[str, Any] | None,
    actual_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the final post-extension AnyPaint graph immediately before queue.

    The compiler already validates its own graph. This validator intentionally runs
    later, on the exact graph sent to ComfyUI, so an extension or future graph patch
    cannot silently inject Native masked-edit nodes or disconnect AnyPaint wiring.
    """

    graph = _mapping(prompt_graph)
    params = _mapping(actual_params)
    if not is_krea2_anypaint_runtime(params):
        return {
            "schema_id": SCHEMA_ID,
            "authority": AUTHORITY,
            "active": False,
            "stage": "prequeue_submitted_graph",
            "status": "not_applicable",
            "ok": True,
            "errors": [],
            "warnings": [],
        }

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    contract = _mapping(params.get("krea2_anypaint_contract"))
    canvas = _mapping(params.get("_neo_krea2_anypaint_canvas_contract") or params.get("krea2_anypaint_canvas_contract"))
    nodes_by_class: dict[str, list[str]] = {}
    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        nodes_by_class.setdefault(class_type, []).append(str(node_id))

    forbidden_found = sorted(class_type for class_type in FORBIDDEN_NATIVE_NODE_CLASSES if nodes_by_class.get(class_type))
    for class_type in forbidden_found:
        errors.append(_issue(
            "forbidden_native_masked_node",
            f"AnyPaint submitted graph contains forbidden Native masked node {class_type}.",
            node_id=nodes_by_class[class_type][0],
        ))

    id_map = {
        "prepare": str(contract.get("prepare_node_id") or ""),
        "encode": str(contract.get("encode_node_id") or ""),
        "adapter": str(contract.get("adapter_node_id") or ""),
        "model_patch": str(contract.get("model_patch_node_id") or ""),
        "sampler": str(contract.get("sampler_node_id") or ""),
        "decode": str(contract.get("decode_node_id") or ""),
        "output": str(contract.get("output_node_id") or ""),
        "source": str(contract.get("source_node_id") or ""),
        "mask": str(contract.get("mask_node_id") or ""),
    }

    for role, expected_class in EXPECTED_NODE_CLASSES.items():
        node_id = id_map.get(role) or ""
        if not node_id:
            errors.append(_issue("missing_contract_node_id", f"AnyPaint runtime contract is missing the {role} node id.", field=role))
            continue
        node = graph.get(node_id)
        if not isinstance(node, Mapping):
            errors.append(_issue("missing_submitted_node", f"AnyPaint {role} node {node_id} is missing from the submitted graph.", node_id=node_id, field=role))
            continue
        actual_class = str(node.get("class_type") or "")
        if actual_class != expected_class:
            errors.append(_issue("submitted_node_class_mismatch", f"AnyPaint {role} node {node_id} expected {expected_class} but found {actual_class or 'unknown'}.", node_id=node_id, field=role))

    source_id = id_map.get("source") or ""
    if source_id:
        source_node = _mapping(graph.get(source_id))
        if source_node.get("class_type") != "LoadImage":
            errors.append(_issue("source_node_invalid", f"AnyPaint source node {source_id} is not LoadImage.", node_id=source_id))
        expected_source = str(params.get("source_image_name") or "")
        observed_source = str(_mapping(source_node.get("inputs")).get("image") or "")
        if expected_source and observed_source != expected_source:
            errors.append(_issue("source_handoff_mismatch", f"AnyPaint source handoff expected {expected_source!r} but submitted {observed_source!r}.", node_id=source_id, field="source_image_name"))

    mask_id = id_map.get("mask") or ""
    expected_mask = str(params.get("mask_image_name") or "")
    if expected_mask:
        if not mask_id:
            errors.append(_issue("mask_contract_missing", "AnyPaint expected a generated mask but the runtime contract has no mask node id.", field="mask_image_name"))
        else:
            mask_node = _mapping(graph.get(mask_id))
            observed_mask = str(_mapping(mask_node.get("inputs")).get("image") or "")
            if mask_node.get("class_type") != "LoadImageMask":
                errors.append(_issue("mask_node_invalid", f"AnyPaint mask node {mask_id} is not LoadImageMask.", node_id=mask_id))
            if observed_mask != expected_mask:
                errors.append(_issue("mask_handoff_mismatch", f"AnyPaint mask handoff expected {expected_mask!r} but submitted {observed_mask!r}.", node_id=mask_id, field="mask_image_name"))

    prepare_id = id_map.get("prepare") or ""
    prepare = _mapping(graph.get(prepare_id))
    prepare_inputs = _mapping(prepare.get("inputs"))
    padding = _mapping(params.get("krea2_anypaint_canvas_padding"))
    for field in ("left", "top", "right", "bottom"):
        expected = _int(padding.get(field), 0)
        observed = _int(prepare_inputs.get(field), -1)
        if observed != expected:
            errors.append(_issue("prepare_padding_mismatch", f"AnyPaint Prepare {field} expected {expected} but submitted {observed}.", node_id=prepare_id, field=field))
    expected_boundary = _int(params.get("krea2_anypaint_boundary_redraw_px"), 32)
    if _int(prepare_inputs.get("boundary_redraw_px"), -1) != expected_boundary:
        errors.append(_issue("prepare_boundary_mismatch", f"AnyPaint boundary redraw expected {expected_boundary}px but submitted {_int(prepare_inputs.get('boundary_redraw_px'), -1)}px.", node_id=prepare_id, field="boundary_redraw_px"))
    expected_reference_edge = _int(params.get("krea2_anypaint_reference_max_edge"), 384)
    if _int(prepare_inputs.get("reference_max_edge"), -1) != expected_reference_edge:
        errors.append(_issue("prepare_reference_edge_mismatch", f"AnyPaint reference max edge expected {expected_reference_edge} but submitted {_int(prepare_inputs.get('reference_max_edge'), -1)}.", node_id=prepare_id, field="reference_max_edge"))
    if source_id and not _same_ref(prepare_inputs.get("source"), [source_id, 0]):
        errors.append(_issue("prepare_source_wiring_mismatch", f"AnyPaint Prepare source is not wired to LoadImage node {source_id}.", node_id=prepare_id, field="source"))
    if expected_mask and mask_id and not _same_ref(prepare_inputs.get("generated_mask"), [mask_id, 0]):
        errors.append(_issue("prepare_mask_wiring_mismatch", f"AnyPaint Prepare generated_mask is not wired to LoadImageMask node {mask_id}.", node_id=prepare_id, field="generated_mask"))

    encode_id = id_map.get("encode") or ""
    encode = _mapping(graph.get(encode_id))
    encode_inputs = _mapping(encode.get("inputs"))
    expected_vlm = bool(params.get("krea2_anypaint_vlm_reference", True))
    if bool(encode_inputs.get("vlm_reference")) != expected_vlm:
        errors.append(_issue("encode_vlm_reference_mismatch", f"AnyPaint VLM reference expected {expected_vlm} but submitted {bool(encode_inputs.get('vlm_reference'))}.", node_id=encode_id, field="vlm_reference"))
    if prepare_id:
        expected_refs = {
            "semantic_reference": [prepare_id, 0],
            "known_image": [prepare_id, 1],
            "keep_mask": [prepare_id, 3],
        }
        for field, expected_ref in expected_refs.items():
            if not _same_ref(encode_inputs.get(field), expected_ref):
                errors.append(_issue("encode_prepare_wiring_mismatch", f"AnyPaint Encode {field} is not wired to {expected_ref}.", node_id=encode_id, field=field))

    adapter_id = id_map.get("adapter") or ""
    adapter = _mapping(graph.get(adapter_id))
    adapter_inputs = _mapping(adapter.get("inputs"))
    expected_adapter = str(params.get("krea2_anypaint_adapter") or "")
    observed_adapter = str(adapter_inputs.get("lora_name") or "")
    if expected_adapter and _normalized_asset(observed_adapter) != _normalized_asset(expected_adapter):
        errors.append(_issue("adapter_asset_mismatch", f"AnyPaint adapter expected {expected_adapter!r} but submitted {observed_adapter!r}.", node_id=adapter_id, field="lora_name"))
    expected_strength = _number(params.get("krea2_anypaint_lora_strength"), 1.0)
    if abs(_number(adapter_inputs.get("strength_model"), -999.0) - expected_strength) > 1e-6:
        errors.append(_issue("adapter_strength_mismatch", f"AnyPaint adapter strength expected {expected_strength} but submitted {_number(adapter_inputs.get('strength_model'), -999.0)}.", node_id=adapter_id, field="strength_model"))

    patch_id = id_map.get("model_patch") or ""
    patch = _mapping(graph.get(patch_id))
    patch_inputs = _mapping(patch.get("inputs"))
    expected_kv = bool(params.get("krea2_anypaint_kv_cache", True))
    if bool(patch_inputs.get("kv_cache")) != expected_kv:
        errors.append(_issue("model_patch_kv_mismatch", f"AnyPaint K/V cache expected {expected_kv} but submitted {bool(patch_inputs.get('kv_cache'))}.", node_id=patch_id, field="kv_cache"))
    if adapter_id and not _same_ref(patch_inputs.get("model"), [adapter_id, 0]):
        # Global user LoRAs are allowed upstream of the AnyPaint adapter. The
        # AnyPaint adapter itself must still be the direct input to ModelPatch.
        errors.append(_issue("model_patch_adapter_wiring_mismatch", f"AnyPaint ModelPatch is not wired directly to adapter node {adapter_id}.", node_id=patch_id, field="model"))

    sampler_id = id_map.get("sampler") or ""
    sampler = _mapping(graph.get(sampler_id))
    sampler_inputs = _mapping(sampler.get("inputs"))
    if patch_id and not _same_ref(sampler_inputs.get("model"), [patch_id, 0]):
        errors.append(_issue("sampler_model_wiring_mismatch", f"AnyPaint KSampler model is not wired to ModelPatch node {patch_id}.", node_id=sampler_id, field="model"))
    if encode_id:
        if not _same_ref(sampler_inputs.get("positive"), [encode_id, 0]):
            errors.append(_issue("sampler_positive_wiring_mismatch", f"AnyPaint KSampler positive conditioning is not wired to Encode node {encode_id}.", node_id=sampler_id, field="positive"))
        latent_ref = sampler_inputs.get("latent_image")
        # Batch >1 inserts RepeatLatentBatch, so only require the Encode latent
        # to be upstream when the sampler consumes it directly.
        if _ref(latent_ref) and str(_ref(latent_ref)[0]) == encode_id and not _same_ref(latent_ref, [encode_id, 1]):
            errors.append(_issue("sampler_latent_output_mismatch", "AnyPaint KSampler references the Encode node but not its LATENT output slot.", node_id=sampler_id, field="latent_image"))

    decode_id = id_map.get("decode") or ""
    decode = _mapping(graph.get(decode_id))
    if sampler_id and not _same_ref(_mapping(decode.get("inputs")).get("samples"), [sampler_id, 0]):
        errors.append(_issue("decode_sampler_wiring_mismatch", f"AnyPaint VAEDecode is not wired to KSampler node {sampler_id}.", node_id=decode_id, field="samples"))

    output_id = id_map.get("output") or ""
    output_node = _mapping(graph.get(output_id))
    if decode_id and not _same_ref(_mapping(output_node.get("inputs")).get("images"), [decode_id, 0]):
        errors.append(_issue("output_decode_wiring_mismatch", f"AnyPaint PreviewImage is not wired to VAEDecode node {decode_id}.", node_id=output_id, field="images"))

    if canvas and canvas.get("authoritative") is not True:
        warnings.append(_issue(
            "canvas_runtime_verification_required",
            "AnyPaint source dimensions were not authoritative at submission time; completion diagnostics must verify the physical output canvas.",
            severity="warning",
        ))

    status = "ready" if not errors else "blocked_graph_mismatch"
    return {
        "schema_id": SCHEMA_ID,
        "authority": AUTHORITY,
        "active": True,
        "stage": "prequeue_submitted_graph",
        "status": status,
        "ok": not errors,
        "fail_closed": True,
        "errors": errors,
        "warnings": warnings,
        "forbidden_native_nodes_found": forbidden_found,
        "expected_nodes": deepcopy(EXPECTED_NODE_CLASSES),
        "node_ids": id_map,
        "adapter": {
            "expected": expected_adapter,
            "observed": observed_adapter,
            "strength_expected": expected_strength,
            "strength_observed": _number(adapter_inputs.get("strength_model"), 0.0),
        },
        "prepare": {
            "padding": {field: _int(prepare_inputs.get(field), 0) for field in ("left", "top", "right", "bottom")},
            "boundary_redraw_px": _int(prepare_inputs.get("boundary_redraw_px"), 0),
            "reference_max_edge": _int(prepare_inputs.get("reference_max_edge"), 0),
            "source_image": str(params.get("source_image_name") or ""),
            "mask_image": str(params.get("mask_image_name") or ""),
        },
        "model_patch": {"kv_cache": bool(patch_inputs.get("kv_cache"))},
        "encode": {"vlm_reference": bool(encode_inputs.get("vlm_reference"))},
        "canvas_contract": canvas,
        "submitted_node_count": len(graph),
    }


def finalize_krea2_anypaint_runtime_diagnostics(
    submission_diagnostics: Mapping[str, Any] | None,
    actual_params: Mapping[str, Any] | None,
    outputs: list[Mapping[str, Any]] | None,
    dimension_probe: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    submission = _mapping(submission_diagnostics)
    params = _mapping(actual_params)
    if not is_krea2_anypaint_runtime(params) and not submission.get("active"):
        return {
            "schema_id": SCHEMA_ID,
            "authority": AUTHORITY,
            "active": False,
            "stage": "completed_output",
            "status": "not_applicable",
            "ok": True,
            "errors": [],
            "warnings": [],
        }

    errors = [dict(item) for item in (submission.get("errors") or []) if isinstance(item, Mapping)]
    warnings = [dict(item) for item in (submission.get("warnings") or []) if isinstance(item, Mapping)]
    canvas = _mapping(params.get("_neo_krea2_anypaint_canvas_contract") or params.get("krea2_anypaint_canvas_contract") or submission.get("canvas_contract"))
    final_size = _mapping(canvas.get("final_size"))
    expected_width = _int(final_size.get("width"), 0)
    expected_height = _int(final_size.get("height"), 0)
    authoritative = canvas.get("authoritative") is True

    output_rows: list[dict[str, Any]] = []
    for output in outputs or []:
        if not isinstance(output, Mapping) or str(output.get("kind") or "image") != "image":
            continue
        probe = _mapping(dimension_probe(output) if dimension_probe else output.get("dimensions"))
        width = _int(probe.get("width"), 0)
        height = _int(probe.get("height"), 0)
        row = {
            "node_id": str(output.get("node_id") or ""),
            "filename": str(output.get("filename") or ""),
            "width": width,
            "height": height,
            "dimension_probe_state": str(probe.get("state") or ("available" if width and height else "unavailable")),
            "dimension_probe_error": str(probe.get("error") or ""),
        }
        if width and height and expected_width and expected_height:
            row["matches_expected_canvas"] = width == expected_width and height == expected_height
        else:
            row["matches_expected_canvas"] = None
        output_rows.append(row)

    dimension_rows = [row for row in output_rows if row.get("width") and row.get("height")]
    if dimension_rows:
        warnings = [item for item in warnings if str(item.get("code") or "") != "canvas_runtime_verification_required"]
    if not output_rows:
        warnings.append(_issue("no_image_outputs_for_runtime_validation", "AnyPaint completed without image outputs available for dimension validation.", severity="warning"))
    elif not dimension_rows:
        warnings.append(_issue("output_dimensions_unavailable", "AnyPaint output exists but Neo could not read its physical dimensions from ComfyUI /view.", severity="warning"))
    elif expected_width and expected_height:
        mismatched = [row for row in dimension_rows if row.get("matches_expected_canvas") is False]
        if mismatched and authoritative:
            first = mismatched[0]
            errors.append(_issue(
                "output_canvas_mismatch",
                f"AnyPaint output canvas {first['width']}x{first['height']} does not match the authoritative Phase 6 contract {expected_width}x{expected_height}.",
                field="output_dimensions",
            ))
        elif mismatched:
            first = mismatched[0]
            warnings.append(_issue(
                "output_canvas_prediction_mismatch",
                f"AnyPaint output canvas {first['width']}x{first['height']} differs from the non-authoritative submission prediction {expected_width}x{expected_height}; runtime output is treated as physical truth.",
                severity="warning",
                field="output_dimensions",
            ))
            # When source dimensions were unknown at submission time, the first
            # physical output becomes the observed runtime canvas rather than a
            # hard failure.
            canvas = {**canvas, "runtime_observed_final_size": {"width": first["width"], "height": first["height"]}, "runtime_verified": True}
        else:
            canvas = {**canvas, "runtime_observed_final_size": {"width": dimension_rows[0]["width"], "height": dimension_rows[0]["height"]}, "runtime_verified": True}

    ok = not errors
    status = "runtime_mismatch" if not ok else ("verified_with_warnings" if warnings else ("verified" if dimension_rows else "verified_with_warnings"))
    return {
        **submission,
        "schema_id": SCHEMA_ID,
        "authority": AUTHORITY,
        "active": True,
        "stage": "completed_output",
        "status": status,
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "canvas_contract": canvas,
        "expected_canvas": {
            "width": expected_width,
            "height": expected_height,
            "authoritative": authoritative,
        },
        "outputs": output_rows,
        "output_dimension_verified": bool(dimension_rows),
        "runtime_proof": {
            "comfy_history_completed": True,
            "output_dependency_chain_completed": bool(output_rows),
            "prepare_execution_state": "inferred_executed_or_cached" if output_rows else "not_proven",
            "adapter_load_state": "inferred_executed_or_cached" if output_rows else "not_proven",
            "model_patch_state": "inferred_executed_or_cached" if output_rows else "not_proven",
            "kv_cache_requested": bool(params.get("krea2_anypaint_kv_cache", True)),
            "forbidden_native_nodes_absent": not bool(submission.get("forbidden_native_nodes_found")),
            "proof_note": "Comfy history does not emit a separate success record for non-output dependency nodes. Successful final output proves the submitted dependency chain executed or was satisfied from cache; adapter/model-patch execution is therefore inferred, not independently measured.",
        },
    }


__all__ = [
    "AUTHORITY",
    "ENGINE_ID",
    "EXPECTED_NODE_CLASSES",
    "FORBIDDEN_NATIVE_NODE_CLASSES",
    "SCHEMA_ID",
    "finalize_krea2_anypaint_runtime_diagnostics",
    "is_krea2_anypaint_runtime",
    "validate_krea2_anypaint_submitted_graph",
]
