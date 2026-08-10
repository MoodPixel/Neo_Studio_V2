from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID

SCHEMA_ID = "neo.image.adetailer.prequeue_diagnostics.v1"
GRAPH_INVARIANT_SCHEMA_ID = "neo.image.adetailer.graph_invariants.v1"

STAGE_ORDER = (
    "request", "replay_recipe", "route_support", "node_inventory", "node_signatures", "pass_payload",
    "model_source", "lora_branch", "identity_policy", "family_preset", "route_contract",
    "sampling_lineage", "source_ownership", "output_ownership", "detector_provider",
    "detector_assets", "graph_invariants",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(STAGE_ORDER)}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _object_info(available_nodes: Any) -> Mapping[str, Any]:
    if not isinstance(available_nodes, Mapping):
        return {}
    nested = available_nodes.get("object_info")
    return nested if isinstance(nested, Mapping) else available_nodes


def _input_names(node_schema: Any) -> set[str] | None:
    if not isinstance(node_schema, Mapping):
        return None
    raw_input = node_schema.get("input")
    if not isinstance(raw_input, Mapping):
        return None
    names: set[str] = set()
    saw_section = False
    for section_name in ("required", "optional", "hidden"):
        section = raw_input.get(section_name)
        if isinstance(section, Mapping):
            saw_section = True
            names.update(str(key) for key in section)
    return names if saw_section and names else None


def _issue(code: str, message: str, *, stage: str, level: str = "error", blocked: bool = True,
           field: str | None = None, remediation: str = "", **evidence: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "extension_id": EXTENSION_ID, "level": level, "code": code, "message": message,
        "stage": stage, "ok": not blocked, "blocked": blocked,
    }
    if field:
        item["field"] = field
    if remediation:
        item["remediation"] = remediation
    item.update({key: value for key, value in evidence.items() if value is not None})
    return item


def diagnostic_stage_for_code(code: Any) -> str:
    token = _clean(code).casefold()
    if token == "adetailer_not_requested": return "request"
    if token.startswith("adetailer_replay_"): return "replay_recipe"
    if token in {"adetailer_no_enabled_detailer_passes", "adetailer_runtime_passes_empty", "adetailer_no_runnable_passes"}: return "pass_payload"
    if token.startswith("adetailer_route_contract") or token.startswith("adetailer_lora_base_contract"): return "route_contract"
    if "sampling" in token and ("identity" in token or "preset" in token): return "sampling_lineage"
    if token.startswith("adetailer_identity") or token.startswith("adetailer_qwen_edit"): return "identity_policy"
    if token.startswith("adetailer_family_preset"): return "family_preset"
    if token.startswith("adetailer_detailer_lora") or token.startswith("adetailer_lora"): return "lora_branch"
    if token.startswith("adetailer_dedicated") or token.startswith("adetailer_model_source"): return "model_source"
    if token.startswith("adetailer_explicit_source"): return "source_ownership"
    if token.startswith("adetailer_output"): return "output_ownership"
    if token.startswith("adetailer_detector_not_accepted"): return "detector_provider"
    if token.startswith("adetailer_execution_bridge"): return "detector_assets"
    if token.startswith("adetailer_graph_") or token.startswith("adetailer_main_sampler_"): return "graph_invariants"
    if token in {"nodes_missing", "nodes_unchecked"} or token.startswith("adetailer_node_"):
        return "node_signatures" if "signature" in token else "node_inventory"
    if token.startswith(("adetailer_provider_gated", "adetailer_planned_gated", "adetailer_unsupported")): return "route_support"
    if token.startswith(("adetailer_payload", "adetailer_multi_pass", "adetailer_stale", "adetailer_params")): return "pass_payload"
    return "request"


def remediation_for_code(code: Any) -> str:
    token = _clean(code).casefold()
    if token.startswith("adetailer_replay_"):
        return "Reload the saved ADetailer recipe unchanged, select its recorded route, and revalidate the current backend before enabling it."
    if token in {"nodes_missing", "nodes_unchecked"} or token.startswith("adetailer_node_"):
        return "Install or update Impact Pack/Impact Subpack, restart ComfyUI, then reconnect the backend so Neo receives a fresh /object_info snapshot."
    if token.startswith("adetailer_route_contract") or token.startswith("adetailer_lora_base_contract"):
        return "Use a compiler route that publishes the complete ADetailer route contract; refresh route capabilities after updating Neo."
    if token.startswith("adetailer_detector_not_accepted"):
        return "Install or stage the selected detector in the active ComfyUI model folders, restart ComfyUI, then refresh ADetailer models."
    if token.startswith("adetailer_execution_bridge"):
        return "Verify the selected detector file and configured ComfyUI model roots, then refresh the backend model catalog."
    if token.startswith("adetailer_detailer_lora") or token.startswith("adetailer_lora"):
        return "Select a LoRA accepted by the active ComfyUI catalog and compatible with the route loader, then revalidate."
    if token.startswith("adetailer_identity") or token.startswith("adetailer_qwen_edit"):
        return "Correct the Qwen Edit revision/identity mode or use the dedicated SDXL/SD1.5 detailer-model fallback."
    if token.startswith("adetailer_family_preset"):
        return "Select a registered family preset or switch to manual sampling values valid for the active detailer model."
    if token.startswith("adetailer_dedicated") or token.startswith("adetailer_model_source"):
        return "Select a live SDXL/SD1.5 checkpoint and VAE from the active ComfyUI catalog, with a positive repair prompt."
    if token.startswith("adetailer_explicit_source"):
        return "Restore the declared Img2Img/Inpaint source lane before running ADetailer as a post-output action."
    if token.startswith("adetailer_output"):
        return "Use a compiler graph with an explicit SaveImage/PreviewImage output consumer owned by the current image route."
    if token.startswith("adetailer_graph_") or token.startswith("adetailer_main_sampler_"):
        return "Do not queue this graph. Refresh Neo/backend capabilities and rebuild a consistent workflow."
    if token.startswith(("adetailer_planned_gated", "adetailer_provider_gated", "adetailer_unsupported")):
        return "Choose an ADetailer route marked Available or Experimental Available in the exact support matrix."
    return "Review the ADetailer diagnostics, correct the selected route or assets, and revalidate before queueing."


def annotate_issue(issue: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(issue))
    code = _clean(item.get("code") or "adetailer_validation_issue")
    stage = _clean(item.get("stage")) or diagnostic_stage_for_code(code)
    if stage not in _STAGE_INDEX: stage = "request"
    item.update({"extension_id": EXTENSION_ID, "code": code, "stage": stage})
    item["level"] = _clean(item.get("level") or ("error" if item.get("blocked") else "warning")).lower()
    item["blocked"] = bool(item.get("blocked") is True or (item.get("ok") is False and item["level"] == "error"))
    item["ok"] = False if item["blocked"] else bool(item.get("ok", True))
    item["message"] = _clean(item.get("message") or "ADetailer validation issue.")
    item["remediation"] = _clean(item.get("remediation")) or remediation_for_code(code)
    return item


def append_diagnostic_issue(validation: dict[str, Any], *, code: str, message: str, stage: str,
                            field: str | None = None, remediation: str = "", level: str = "error",
                            blocked: bool = True, **evidence: Any) -> dict[str, Any]:
    item = _issue(code, message, stage=stage, field=field, remediation=remediation,
                  level=level, blocked=blocked, **evidence)
    existing = validation.setdefault("validation", [])
    for current in existing:
        if isinstance(current, Mapping) and _clean(current.get("code")) == code and _clean(current.get("field")) == _clean(field):
            return annotate_issue(current)
    existing.append(item)
    if blocked:
        validation["runtime_ready"] = False
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
    return item


def validate_critical_node_signatures(available_nodes: Any, *, params: Mapping[str, Any] | None = None,
                                      model_source: Mapping[str, Any] | None = None,
                                      lora_branch: Mapping[str, Any] | None = None) -> dict[str, Any]:
    info = _object_info(available_nodes)
    settings = params if isinstance(params, Mapping) else {}
    source = model_source if isinstance(model_source, Mapping) else {}
    lora = lora_branch if isinstance(lora_branch, Mapping) else {}
    checks: list[dict[str, Any]] = []; errors: list[dict[str, Any]] = []; warnings: list[dict[str, Any]] = []
    required: list[tuple[str, set[str], set[str]]] = [
        ("FaceDetailer", {"image", "model", "clip", "vae", "positive", "negative", "bbox_detector"}, set())
    ]
    detector_type = _clean(settings.get("detector_type") or "bbox").lower()
    provider = "ONNXDetectorProvider" if detector_type.startswith("onnx") else "UltralyticsDetectorProvider"
    required.append((provider, set(), {"model_name", "model", "detector_model"}))
    if _clean(source.get("source")) == "dedicated_checkpoint":
        required += [("CheckpointLoaderSimple", {"ckpt_name"}, set()), ("CLIPTextEncode", {"text", "clip"}, set())]
        if _clean(source.get("vae")) and _clean(source.get("vae")).lower() not in {"automatic", "auto", "checkpoint"}:
            required.append(("VAELoader", {"vae_name"}, set()))
    if bool(lora.get("requested")) and bool(lora.get("requires_prompt_reencode")):
        required.append(("CLIPTextEncode", {"text", "clip"}, set()))

    schema_checked = 0; seen: set[str] = set()
    for node_class, required_all, required_any in required:
        key = f"{node_class}:{sorted(required_all)}:{sorted(required_any)}"
        if key in seen: continue
        seen.add(key)
        names = _input_names(info.get(node_class) if isinstance(info, Mapping) else None)
        if names is None:
            checks.append({"node_class": node_class, "status": "schema_unavailable", "required_all": sorted(required_all), "required_any": sorted(required_any)})
            continue
        # Choice-only snapshots are not complete node signatures.
        semantic_names = names - {"sampler_name", "scheduler", "model_name", "model", "detector_model"}
        if len(names) <= 3 and not semantic_names:
            checks.append({"node_class": node_class, "status": "partial_schema_unavailable", "available_inputs": sorted(names)})
            continue
        schema_checked += 1
        missing_all = sorted(required_all - names)
        any_ok = not required_any or bool(required_any & names)
        ok = not missing_all and any_ok
        check = {"node_class": node_class, "status": "ready" if ok else "invalid", "available_inputs": sorted(names),
                 "missing_inputs": missing_all, "required_any": sorted(required_any), "any_input_satisfied": any_ok}
        checks.append(check)
        if not ok:
            errors.append(_issue("adetailer_node_signature_invalid",
                f"The active {node_class} node signature is incompatible with the selected ADetailer execution path.",
                stage="node_signatures", field=node_class, missing_inputs=missing_all,
                required_any=sorted(required_any), available_inputs=sorted(names)))
    if schema_checked == 0:
        warnings.append(_issue("adetailer_node_signatures_unchecked",
            "The backend snapshot does not include complete node input signatures; node-name readiness remains authoritative for this run.",
            stage="node_signatures", level="warning", blocked=False))
    return {"schema_id": "neo.image.adetailer.node_signature_gate.v1", "checked": schema_checked > 0,
            "ready": not errors, "checks": checks, "errors": errors, "warnings": warnings}


def refresh_prequeue_diagnostics(validation: dict[str, Any], *, runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = validation.get("validation") if isinstance(validation.get("validation"), list) else []
    annotated: list[dict[str, Any]] = []; seen: set[tuple[str, str, str]] = set()
    for value in raw:
        if not isinstance(value, Mapping): continue
        item = annotate_issue(value); key = (item["code"], _clean(item.get("field")), item["message"])
        if key in seen: continue
        seen.add(key); annotated.append(item)
    annotated.sort(key=lambda item: (_STAGE_INDEX.get(_clean(item.get("stage")), len(STAGE_ORDER)), 0 if item.get("blocked") else 1, _clean(item.get("code"))))
    enabled = bool(validation.get("enabled")); blockers = [i for i in annotated if i.get("blocked") and i.get("ok") is False]
    warnings = [i for i in annotated if not i.get("blocked") and i.get("level") == "warning"]
    infos = [i for i in annotated if i.get("level") == "info"]
    stages=[]
    for stage in STAGE_ORDER:
        issues=[i for i in annotated if i.get("stage")==stage]; bs=[i for i in issues if i.get("blocked")]; ws=[i for i in issues if i.get("level")=="warning" and not i.get("blocked")]
        state="blocked" if bs else "warning" if ws else "ready" if issues else "not_evaluated"
        stages.append({"stage":stage,"state":state,"issue_codes":[i.get("code") for i in issues],"blocker_count":len(bs),"warning_count":len(ws)})
    state = "not_requested" if not enabled else "blocked" if blockers else "ready" if validation.get("workflow_patch_allowed") else "not_ready"
    result={"schema_id":SCHEMA_ID,"state":state,"enabled":enabled,"queue_allowed":not blockers,
            "extension_execution_allowed":bool(validation.get("workflow_patch_allowed")) and not blockers,"fail_closed":True,
            "primary_blocker":deepcopy(blockers[0]) if blockers else None,"blocker_codes":[i.get("code") for i in blockers],
            "warning_codes":[i.get("code") for i in warnings],"counts":{"issues":len(annotated),"blockers":len(blockers),"warnings":len(warnings),"info":len(infos),"by_stage":dict(Counter(i.get("stage") for i in annotated))},
            "stages":stages,"issues":annotated,"runtime":deepcopy(dict(runtime)) if isinstance(runtime,Mapping) else {},
            "provider_queue_policy":"blocked_error_items_are_rejected_before_comfy_prompt_submission"}
    validation["validation"]=annotated; validation["prequeue_diagnostics"]=result
    return result


def _valid_ref(graph: Mapping[str, Any], value: Any) -> bool:
    if not isinstance(value,(list,tuple)) or len(value)!=2: return False
    node_id=_clean(value[0])
    try: index=int(value[1])
    except (TypeError,ValueError): return False
    return bool(node_id and node_id in graph and index>=0)


def validate_graph_invariants(before_graph: Mapping[str, Any], after_graph: Mapping[str, Any], *,
                              route_contract: Mapping[str, Any] | None,
                              output_consumers: Sequence[tuple[str,str]], previous_image_ref: Sequence[Any],
                              patched_image_ref: Sequence[Any], pass_summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    errors=[]; checks=[]; before=before_graph if isinstance(before_graph,Mapping) else {}; after=after_graph if isinstance(after_graph,Mapping) else {}; contract=route_contract if isinstance(route_contract,Mapping) else {}
    patched=list(patched_image_ref) if isinstance(patched_image_ref,(list,tuple)) else []
    ok=_valid_ref(after,patched); checks.append({"check":"patched_image_ref_exists","ok":ok,"ref":deepcopy(patched)})
    if not ok: errors.append(_issue("adetailer_graph_patched_image_ref_invalid","ADetailer produced an image reference that does not resolve to a node in the final graph.",stage="graph_invariants",field="patched_image_ref",patched_image_ref=deepcopy(patched)))
    failures=[]
    for node_id,input_name in output_consumers:
        node=after.get(str(node_id)); inputs=node.get("inputs") if isinstance(node,Mapping) and isinstance(node.get("inputs"),Mapping) else {}; actual=inputs.get(str(input_name))
        if not (isinstance(actual,(list,tuple)) and list(actual)==patched): failures.append({"node_id":str(node_id),"input":str(input_name),"actual":deepcopy(actual)})
    checks.append({"check":"output_consumers_rewired","ok":not failures,"consumer_count":len(output_consumers),"failures":failures})
    if failures: errors.append(_issue("adetailer_graph_output_rewire_failed","One or more declared output consumers do not point to the final ADetailer image.",stage="graph_invariants",field="output_consumers",failures=failures))
    before_ids={str(k) for k in before}; nodes=[str(k) for k,v in after.items() if str(k) not in before_ids and isinstance(v,Mapping) and v.get("class_type") in {"FaceDetailer","SEGSDetailer"}]
    count_ok=len(nodes)>=len(pass_summaries)>0; checks.append({"check":"detailer_nodes_match_runtime_passes","ok":count_ok,"new_detailer_nodes":nodes,"expected_passes":len(pass_summaries)})
    if not count_ok: errors.append(_issue("adetailer_graph_detailer_node_count_mismatch","The final graph does not contain a detailer node for every successful runtime pass.",stage="graph_invariants",expected_passes=len(pass_summaries),detailer_node_ids=nodes))
    bad=[]
    for node_id in nodes:
        node=after.get(node_id,{}); inputs=node.get("inputs") if isinstance(node,Mapping) and isinstance(node.get("inputs"),Mapping) else {}
        for field in ("image","model","clip","vae","positive","negative","bbox_detector","basic_pipe","segs"):
            if field in inputs and not _valid_ref(after,inputs[field]): bad.append({"node_id":node_id,"class_type":node.get("class_type"),"field":field,"ref":deepcopy(inputs[field])})
    checks.append({"check":"detailer_input_refs_resolve","ok":not bad,"failures":bad})
    if bad: errors.append(_issue("adetailer_graph_detailer_input_ref_invalid","A newly inserted detailer node contains an unresolved graph reference.",stage="graph_invariants",failures=bad))
    sampler=contract.get("sampler") if isinstance(contract.get("sampler"),Mapping) else {}; sid=_clean(sampler.get("node_id")); sinputs=sampler.get("inputs") if isinstance(sampler.get("inputs"),Mapping) else {}; model_name=_clean(sinputs.get("model") or "model")
    bnode=before.get(sid); anode=after.get(sid); bi=bnode.get("inputs") if isinstance(bnode,Mapping) and isinstance(bnode.get("inputs"),Mapping) else {}; ai=anode.get("inputs") if isinstance(anode,Mapping) and isinstance(anode.get("inputs"),Mapping) else {}
    bmodel=deepcopy(bi.get(model_name)); amodel=deepcopy(ai.get(model_name)); isolated=bool(sid and bmodel==amodel)
    checks.append({"check":"main_sampler_model_unchanged","ok":isolated,"sampler_node_id":sid,"input":model_name,"before":bmodel,"after":amodel})
    if not isolated: errors.append(_issue("adetailer_main_sampler_model_rewired","ADetailer changed the main generation sampler's MODEL input; the isolated finish branch was rolled back.",stage="graph_invariants",field="sampler.model",sampler_node_id=sid,input_name=model_name,before_model_ref=bmodel,after_model_ref=amodel))
    return {"schema_id":GRAPH_INVARIANT_SCHEMA_ID,"ready":not errors,"rollback_required":bool(errors),"previous_image_ref":list(previous_image_ref) if isinstance(previous_image_ref,(list,tuple)) else [],"patched_image_ref":patched,"checks":checks,"errors":errors}
