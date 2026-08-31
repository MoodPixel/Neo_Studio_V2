from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable

from neo_app.video.lora_patch_profiles import (
    build_multi_branch_lora_patch_profile,
    build_single_model_lora_patch_profile,
)
from neo_app.video.video_lora_adapter import (
    LIGHTX2V_MODE,
    build_wan22_video_lora_plan,
)
from neo_app.video.video_lora_runtime import extract_video_lora_rows, normalize_video_lora_rows

WAN_MODEL_ONLY_LOADER = "LoraLoaderModelOnly"
_SINGLE_RUNTIME_SCHEMA = "neo.video.lora_stack.wan22.single.runtime.v1"
_DUAL_RUNTIME_SCHEMA = "neo.video.lora_stack.wan22.dual.runtime.v1"
_PHASE8_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("neo_video_wan_phase8_payload", default=None)
_INSTALLED = False


def _actual_class(object_info: dict[str, Any], class_name: str) -> str:
    folded = {str(key).casefold(): str(key) for key in (object_info or {})}
    return folded.get(class_name.casefold(), "")


def _lora_catalog(object_info: dict[str, Any], loader_class: str) -> list[str]:
    if not loader_class:
        return []
    entry = object_info.get(loader_class, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    optional = inputs.get("optional", {}) if isinstance(inputs, dict) else {}
    for group in (required, optional):
        if not isinstance(group, dict):
            continue
        spec = group.get("lora_name")
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return [str(item) for item in spec[0] if str(item)]
    return []


def _validate_live_catalog(rows: list[dict[str, Any]], live_values: list[str], loader_available: bool) -> None:
    if not rows:
        return
    if not loader_available:
        raise ValueError("WAN Video LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")
    if not live_values:
        raise ValueError("WAN Video LoRA rows were requested but LoraLoaderModelOnly exposes no LoRA files in its live catalog.")
    live_folded = {value.casefold() for value in live_values}
    missing = [str(row.get("name") or "") for row in rows if str(row.get("name") or "").casefold() not in live_folded]
    if missing:
        raise ValueError(
            "Selected WAN Video LoRA is not visible in the live LoraLoaderModelOnly catalog: " + ", ".join(missing)
        )


def _next_numeric_node_id(workflow: dict[str, Any]) -> int:
    numeric = [int(key) for key in workflow if str(key).isdigit()]
    return max(numeric, default=0) + 1


def _direct_model_consumers(workflow: dict[str, Any], model_ref: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for node_id, node in workflow.items():
        inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}
        for input_name, value in inputs.items():
            if value == model_ref:
                result.append({"node_id": str(node_id), "input": str(input_name)})
    return result


def _find_single_anchor(compiled: dict[str, Any]) -> tuple[list[Any], str]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
    sampling_class = str(classes.get("sampling_patch") or "")
    matches: list[tuple[str, list[Any]]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or str(node.get("class_type") or "") != sampling_class:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        model_ref = inputs.get("model")
        if isinstance(model_ref, list) and len(model_ref) == 2:
            matches.append((str(node_id), list(model_ref)))
    if len(matches) != 1:
        raise ValueError("WAN UNET compiler did not expose exactly one ModelSampling model consumer for the Video LoRA anchor.")
    node_id, model_ref = matches[0]
    return model_ref, node_id


def _find_dual_roots(compiled: dict[str, Any]) -> tuple[tuple[list[Any], list[dict[str, str]]], tuple[list[Any], list[dict[str, str]]]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    high_nodes: list[str] = []
    low_nodes: list[str] = []
    for node_id, node in workflow.items():
        meta = node.get("_meta") if isinstance(node, dict) and isinstance(node.get("_meta"), dict) else {}
        title = str(meta.get("title") or "").casefold()
        if "high-noise model" in title:
            high_nodes.append(str(node_id))
        if "low-noise model" in title:
            low_nodes.append(str(node_id))
    if len(high_nodes) != 1 or len(low_nodes) != 1:
        raise ValueError("WAN dual-noise compiler did not expose exactly one high-noise and one low-noise model loader anchor.")
    high_ref = [high_nodes[0], 0]
    low_ref = [low_nodes[0], 0]
    high_consumers = _direct_model_consumers(workflow, high_ref)
    low_consumers = _direct_model_consumers(workflow, low_ref)
    if not high_consumers or not low_consumers:
        raise ValueError("WAN dual-noise compiler model loaders are not connected to patchable downstream consumers.")
    return (high_ref, high_consumers), (low_ref, low_consumers)


def _target_branches(target: str) -> set[str]:
    if target == "all":
        return {"high", "low"}
    if target in {"high", "low"}:
        return {target}
    return set()


def _merge_legacy_rows(
    universal_rows: list[dict[str, Any]],
    legacy_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged = normalize_video_lora_rows(universal_rows)
    meta = {
        "requested": bool(legacy_rows),
        "bridged_count": 0,
        "duplicate_branch_suppressed": 0,
        "existing_role_promoted": 0,
        "source": "legacy_wan_video_lora_fields",
    }

    for legacy in normalize_video_lora_rows(legacy_rows):
        name = str(legacy.get("name") or "")
        wanted = _target_branches(str(legacy.get("target") or "all"))
        if not wanted:
            continue
        covered: set[str] = set()
        for existing in merged:
            if str(existing.get("name") or "").casefold() != name.casefold():
                continue
            overlap = _target_branches(str(existing.get("target") or "all")) & wanted
            if not overlap:
                continue
            covered.update(overlap)
            if legacy.get("role") == "speed" and existing.get("role") != "speed":
                existing["role"] = "speed"
                meta["existing_role_promoted"] += 1
        missing = wanted - covered
        meta["duplicate_branch_suppressed"] += len(wanted) - len(missing)
        if not missing:
            continue
        target = "all" if missing == {"high", "low"} else next(iter(missing))
        row = dict(legacy)
        row["target"] = target
        merged.append(row)
        meta["bridged_count"] += 1

    normalized = normalize_video_lora_rows(merged)
    standard = [deepcopy(row) for row in normalized if row.get("role") != "speed"]
    speed = [deepcopy(row) for row in normalized if row.get("role") == "speed"]
    return [*standard, *speed], meta


def _legacy_dual_rows(req: Any, compiled: dict[str, Any], info: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    plan = build_wan22_video_lora_plan(
        object_info=info,
        adapter_bindings=bindings,
        enable_video_lora=bool(req.enable_video_lora),
        video_lora_mode=req.video_lora_mode,
        video_lora_model=req.video_lora_model,
        video_lora_strength=req.video_lora_strength,
        video_lora_target=req.video_lora_target,
        enable_lightx2v=bool(req.enable_lightx2v),
        high_noise_lora=req.high_noise_lora,
        low_noise_lora=req.low_noise_lora,
        high_noise_lora_strength=req.high_noise_lora_strength,
        low_noise_lora_strength=req.low_noise_lora_strength,
    )
    payload = plan.payload()
    if plan.errors:
        raise ValueError("WAN legacy LoRA bridge rejected the request: " + "; ".join(plan.errors))
    if not plan.enabled:
        return [], payload

    rows: list[dict[str, Any]] = []
    if plan.mode == LIGHTX2V_MODE:
        for branch in plan.branches:
            rows.append(
                {
                    "uid": f"legacy_wan_{branch.role}",
                    "enabled": True,
                    "name": branch.lora_name,
                    "strength_model": branch.strength_model,
                    "role": "speed",
                    "target": "high" if branch.role == "high_noise" else "low",
                }
            )
    else:
        selected = str(payload.get("selected", {}).get("video_lora_model") or "")
        strength = float(payload.get("selected", {}).get("video_lora_strength") or 0.8)
        target = {"both": "all", "high": "high", "low": "low"}.get(str(plan.target), "all")
        if selected:
            rows.append(
                {
                    "uid": "legacy_wan_video_lora",
                    "enabled": True,
                    "name": selected,
                    "strength_model": strength,
                    "role": "standard",
                    "target": target,
                }
            )
    return rows, payload


def _legacy_speed_requested(req: Any) -> bool:
    mode = str(getattr(req, "video_lora_mode", "") or "").casefold().replace("-", "_")
    return bool(getattr(req, "enable_lightx2v", False)) or mode in {
        "lightx2v",
        "lightx2v_4step",
        "lightning",
        "lightning_fast",
        "4step",
        "4_step",
    }


def _base_dual_request(req: Any) -> Any:
    updates: dict[str, Any] = {
        "enable_lightx2v": False,
        "enable_video_lora": False,
        "video_lora_mode": "off",
        "video_lora_model": None,
        "video_lora_strength": None,
        "high_noise_lora": None,
        "low_noise_lora": None,
        "high_noise_lora_strength": None,
        "low_noise_lora_strength": None,
    }
    if _legacy_speed_requested(req) and not bool(getattr(req, "preserve_user_overrides", False)):
        updates["steps"] = min(int(req.steps) if req.steps is not None else 4, 4)
        updates["guidance"] = min(float(req.guidance) if req.guidance is not None else 1.0, 1.0)
        updates["split_step"] = min(max(1, int(req.split_step) if req.split_step is not None else 2), 2)
    return replace(req, **updates)


def _validate_single_profile(workflow: dict[str, Any], profile: dict[str, Any], route_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if profile.get("owner") != "compiler" or str(profile.get("route_id") or "") != route_id:
        raise ValueError("WAN single-model LoRA patch profile is not compiler-owned for the active route.")
    if profile.get("loader_type") != "model_only" or profile.get("loader_node_class") != WAN_MODEL_ONLY_LOADER:
        raise ValueError("WAN single-model LoRA integration requires LoraLoaderModelOnly.")
    if profile.get("targets") != ["all"] or not bool(profile.get("validated", False)):
        raise ValueError("WAN single-model LoRA patch profile is not validated for target='all'.")
    branches = profile.get("branches") if isinstance(profile.get("branches"), list) else []
    if len(branches) != 1:
        raise ValueError("WAN single-model LoRA profile must expose exactly one model branch.")
    branch = branches[0]
    model_ref = branch.get("model_ref") if isinstance(branch.get("model_ref"), list) else []
    consumers = branch.get("model_consumers") if isinstance(branch.get("model_consumers"), list) else []
    if len(model_ref) != 2 or not consumers or str(model_ref[0]) not in workflow:
        raise ValueError("WAN single-model LoRA profile is missing its model reference or consumers.")
    for consumer in consumers:
        node = workflow.get(str(consumer.get("node_id") or ""), {})
        inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}
        if inputs.get(str(consumer.get("input") or "")) != model_ref:
            raise ValueError("WAN single-model LoRA patch profile is stale against the compiled workflow.")
    return branch, consumers


def apply_wan_single_lora_stack(
    workflow: dict[str, Any],
    profile: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    route_id: str,
    loader_available: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = deepcopy(workflow)
    normalized = normalize_video_lora_rows(rows)
    if not normalized:
        return graph, {
            "schema_version": _SINGLE_RUNTIME_SCHEMA,
            "active": False,
            "route_id": route_id,
            "requested_count": 0,
            "applied_count": 0,
            "standard_count": 0,
            "speed_count": 0,
            "loader_node_class": WAN_MODEL_ONLY_LOADER,
            "applied": [],
            "warnings": [],
        }
    if not loader_available:
        raise ValueError("WAN UNET LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")
    if any(row.get("role") == "speed" for row in normalized):
        raise ValueError("WAN UNET Phase 8 supports standard Video LoRAs only; role='speed' remains unvalidated for the single-model route.")
    if any(row.get("target") != "all" for row in normalized):
        raise ValueError("WAN UNET single-model routes support only target='all'.")

    branch, consumers = _validate_single_profile(graph, profile, route_id)
    upstream = list(branch["model_ref"])
    next_id = _next_numeric_node_id(graph)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in normalized:
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {
            "class_type": WAN_MODEL_ONLY_LOADER,
            "inputs": {"model": upstream, "lora_name": row["name"], "strength_model": float(row["strength_model"])},
            "_meta": {"title": "Video LoRA · WAN 2.2 · standard"},
        }
        upstream = [node_id, 0]
        applied.append({**row, "target": "all", "node_id": node_id})
        if row.get("strength_clip") is not None:
            warnings.append(f"{row['name']}: strength_clip is ignored because WAN Phase 8 uses a model-only LoRA loader.")
    for consumer in consumers:
        graph[str(consumer["node_id"])]["inputs"][str(consumer["input"])] = list(upstream)
    return graph, {
        "schema_version": _SINGLE_RUNTIME_SCHEMA,
        "active": True,
        "route_id": route_id,
        "requested_count": len(normalized),
        "applied_count": len(applied),
        "standard_count": len(applied),
        "speed_count": 0,
        "loader_node_class": WAN_MODEL_ONLY_LOADER,
        "final_model_ref": upstream,
        "applied": applied,
        "warnings": warnings,
    }


def _validate_dual_profile(workflow: dict[str, Any], profile: dict[str, Any], route_id: str) -> list[dict[str, Any]]:
    if profile.get("owner") != "compiler" or str(profile.get("route_id") or "") != route_id:
        raise ValueError("WAN dual-noise LoRA patch profile is not compiler-owned for the active route.")
    if profile.get("loader_type") != "model_only_multi_branch" or profile.get("loader_node_class") != WAN_MODEL_ONLY_LOADER:
        raise ValueError("WAN dual-noise LoRA integration requires a model_only_multi_branch LoraLoaderModelOnly profile.")
    if profile.get("targets") != ["all", "high", "low"] or not bool(profile.get("validated", False)):
        raise ValueError("WAN dual-noise LoRA patch profile must expose validated all/high/low targets.")
    branches = profile.get("branches") if isinstance(profile.get("branches"), list) else []
    if {str(branch.get("target") or "") for branch in branches if isinstance(branch, dict)} != {"high", "low"}:
        raise ValueError("WAN dual-noise LoRA profile must contain distinct high and low branches.")
    for branch in branches:
        model_ref = branch.get("model_ref") if isinstance(branch.get("model_ref"), list) else []
        consumers = branch.get("model_consumers") if isinstance(branch.get("model_consumers"), list) else []
        if len(model_ref) != 2 or not consumers or str(model_ref[0]) not in workflow:
            raise ValueError("WAN dual-noise LoRA profile is missing a branch model reference or consumer declaration.")
        for consumer in consumers:
            node = workflow.get(str(consumer.get("node_id") or ""), {})
            inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}
            if inputs.get(str(consumer.get("input") or "")) != model_ref:
                raise ValueError("WAN dual-noise LoRA patch profile is stale against the compiled workflow.")
    return branches


def apply_wan_dual_lora_stack(
    workflow: dict[str, Any],
    profile: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    route_id: str,
    loader_available: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = deepcopy(workflow)
    normalized = normalize_video_lora_rows(rows)
    standard = [deepcopy(row) for row in normalized if row.get("role") != "speed"]
    speed = [deepcopy(row) for row in normalized if row.get("role") == "speed"]
    ordered = [*standard, *speed]
    if not ordered:
        return graph, {
            "schema_version": _DUAL_RUNTIME_SCHEMA,
            "active": False,
            "route_id": route_id,
            "requested_count": 0,
            "applied_count": 0,
            "applied_node_count": 0,
            "standard_count": 0,
            "speed_count": 0,
            "loader_node_class": WAN_MODEL_ONLY_LOADER,
            "applied": [],
            "warnings": [],
        }
    if not loader_available:
        raise ValueError("WAN dual-noise LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")
    if any(row.get("target") not in {"all", "high", "low"} for row in ordered):
        raise ValueError("WAN dual-noise Video LoRA target must be all, high, or low.")

    branches = _validate_dual_profile(graph, profile, route_id)
    branch_map = {str(branch["target"]): branch for branch in branches}
    upstream = {target: list(branch_map[target]["model_ref"]) for target in ("high", "low")}
    next_id = _next_numeric_node_id(graph)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in ordered:
        targets = ["high", "low"] if row.get("target") == "all" else [str(row.get("target"))]
        node_ids: dict[str, str] = {}
        for target in targets:
            node_id = str(next_id)
            next_id += 1
            graph[node_id] = {
                "class_type": WAN_MODEL_ONLY_LOADER,
                "inputs": {"model": upstream[target], "lora_name": row["name"], "strength_model": float(row["strength_model"])},
                "_meta": {"title": f"Video LoRA · WAN 2.2 · {row['role']} · {target}"},
            }
            upstream[target] = [node_id, 0]
            node_ids[target] = node_id
        applied.append({**row, "node_ids": node_ids})
        if row.get("strength_clip") is not None:
            warnings.append(f"{row['name']}: strength_clip is ignored because WAN Phase 8 uses model-only LoRA branches.")

    for target in ("high", "low"):
        for consumer in branch_map[target]["model_consumers"]:
            graph[str(consumer["node_id"])]["inputs"][str(consumer["input"])] = list(upstream[target])
    return graph, {
        "schema_version": _DUAL_RUNTIME_SCHEMA,
        "active": True,
        "route_id": route_id,
        "requested_count": len(ordered),
        "applied_count": len(applied),
        "applied_node_count": sum(len(item.get("node_ids", {})) for item in applied),
        "standard_count": len(standard),
        "speed_count": len(speed),
        "loader_node_class": WAN_MODEL_ONLY_LOADER,
        "final_model_refs": {"high": upstream["high"], "low": upstream["low"]},
        "applied": applied,
        "warnings": warnings,
    }


def _wrap_payload_context(module: Any, name: str) -> None:
    original: Callable[..., dict[str, Any]] = getattr(module, name)
    if getattr(original, "_neo_phase8_payload_wrapper", False):
        return

    def wrapped(payload: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _PHASE8_PAYLOAD.set(dict(payload or {}))
        try:
            return original(payload, *args, **kwargs)
        finally:
            _PHASE8_PAYLOAD.reset(token)

    wrapped._neo_phase8_payload_wrapper = True  # type: ignore[attr-defined]
    setattr(module, name, wrapped)


def _install_single_model_wan(module: Any) -> None:
    original_build = module.build_wan22_txt2vid_workflow

    def phase8_build(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        payload = _PHASE8_PAYLOAD.get() or req.payload()
        rows = extract_video_lora_rows(payload)
        compiled = original_build(req, object_info=info)
        route_id = str(compiled.get("route_id") or "")
        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        model_ref, consumer_id = _find_single_anchor(compiled)
        profile = build_single_model_lora_patch_profile(
            route_id=route_id,
            compiler="neo_app.video.wan_txt2vid_compiler",
            model_ref=model_ref,
            model_consumers=[{"node_id": consumer_id, "input": "model"}],
            loader_type="model_only",
            loader_node_class=WAN_MODEL_ONLY_LOADER,
            validated=route_id in {"wan22.unet.txt2vid", "wan22.unet.img2vid"},
            notes=["Phase 8 WAN UNET anchor is the model input immediately upstream of ModelSamplingSD3."],
        )
        compiled["lora_patch_profile"] = profile
        loader_class = _actual_class(info, WAN_MODEL_ONLY_LOADER)
        live = _lora_catalog(info, loader_class)
        _validate_live_catalog(rows, live, bool(loader_class))
        patched, runtime = apply_wan_single_lora_stack(
            workflow,
            profile,
            rows,
            route_id=route_id,
            loader_available=bool(loader_class),
        )
        compiled["workflow"] = patched
        compiled["prompt_api_payload"]["prompt"] = patched
        runtime["live_catalog_validated"] = bool(rows)
        runtime["generic_lora_loader_fallback"] = False
        runtime["legacy_bridge"] = {"requested": False, "bridged_count": 0}
        runtime["phase"] = "phase_8"
        compiled["video_lora_stack"] = runtime
        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules.extend([
            "WAN 2.2 UNET Txt2Vid/Img2Vid standard LoRAs use the universal Video LoRA stack and a compiler-owned model-only patch profile.",
            "WAN single-model routes accept target='all' only and reject speed LoRAs until that exact topology is validated.",
            "Selected WAN LoRA files must exist in the live LoraLoaderModelOnly catalog; generic LoraLoader fallback is forbidden.",
        ])
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    module.build_wan22_txt2vid_workflow = phase8_build
    for name in (
        "video_wan22_txt2vid_compile_payload",
        "video_wan22_img2vid_compile_payload",
        "video_wan22_txt2vid_generate_payload",
        "video_wan22_img2vid_generate_payload",
    ):
        _wrap_payload_context(module, name)
    module._neo_phase8_video_lora_installed = True
    module._neo_phase8_video_lora_original_build = original_build


def _install_dual_noise_wan(module: Any) -> None:
    original_build = module.build_wan22_gguf_i2v14_workflow

    def phase8_build(req: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        info = object_info or {}
        payload = _PHASE8_PAYLOAD.get() or req.payload()
        universal = extract_video_lora_rows(payload)
        base_req = _base_dual_request(req)
        compiled = original_build(base_req, object_info=info)
        route_id = str(compiled.get("route_id") or "")
        legacy_rows, legacy_payload = _legacy_dual_rows(req, compiled, info)
        rows, bridge_meta = _merge_legacy_rows(universal, legacy_rows)

        workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
        (high_ref, high_consumers), (low_ref, low_consumers) = _find_dual_roots(compiled)
        profile = build_multi_branch_lora_patch_profile(
            route_id=route_id,
            compiler="neo_app.video.wan_gguf_i2v14_compiler",
            high_model_ref=high_ref,
            high_model_consumers=high_consumers,
            low_model_ref=low_ref,
            low_model_consumers=low_consumers,
            loader_type="model_only_multi_branch",
            loader_node_class=WAN_MODEL_ONLY_LOADER,
            validated=route_id == "wan22.gguf.img2vid_14b_dual_noise",
            notes=[
                "Phase 8 WAN dual-noise anchors are the compiler-emitted high/low model loader outputs and their direct downstream consumers.",
                "Standard rows are applied before role=speed rows independently on each selected branch.",
            ],
        )
        compiled["lora_patch_profile"] = profile
        loader_class = _actual_class(info, WAN_MODEL_ONLY_LOADER)
        live = _lora_catalog(info, loader_class)
        _validate_live_catalog(rows, live, bool(loader_class))
        patched, runtime = apply_wan_dual_lora_stack(
            workflow,
            profile,
            rows,
            route_id=route_id,
            loader_available=bool(loader_class),
        )
        compiled["workflow"] = patched
        compiled["prompt_api_payload"]["prompt"] = patched
        runtime["live_catalog_validated"] = bool(rows)
        runtime["generic_lora_loader_fallback"] = False
        runtime["legacy_bridge"] = bridge_meta
        runtime["legacy_adapter_snapshot"] = legacy_payload
        runtime["phase"] = "phase_8"
        compiled["video_lora_stack"] = runtime

        legacy_snapshot = dict(legacy_payload)
        legacy_snapshot["migration_bridge"] = True
        legacy_snapshot["graph_mutation_authority"] = "video_lora_stack"
        compiled["video_lora_adapter"] = legacy_snapshot

        speed_requested = _legacy_speed_requested(req)
        if speed_requested:
            recommendation = {"steps": 4, "guidance": 1.0, "split_step": 2}
            report = {
                "mode": "recommendation_only" if bool(req.preserve_user_overrides) else "legacy_cap_via_phase8_bridge",
                "recommended": recommendation,
                "applied": {},
                "preserved": {},
            }
            parameters = compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}
            if bool(req.preserve_user_overrides):
                report["preserved"] = {key: parameters.get(key) for key in recommendation}
            else:
                report["applied"] = {key: parameters.get(key) for key in recommendation}
            compiled["sampling_override_report"] = report

        selected_models = compiled.get("selected_models") if isinstance(compiled.get("selected_models"), dict) else {}
        selected_models["enable_video_lora"] = bool(runtime.get("active"))
        selected_models["enable_lightx2v"] = bool(runtime.get("speed_count"))
        selected_models["video_lora_mode"] = LIGHTX2V_MODE if runtime.get("speed_count") else "normal" if runtime.get("active") else "off"
        compiled["selected_models"] = selected_models

        rules = compiled.get("rules") if isinstance(compiled.get("rules"), list) else []
        rules.extend([
            "WAN 2.2 dual-noise GGUF LoRAs are now applied through the universal Video LoRA stack and a compiler-owned high/low patch profile.",
            "Legacy WAN Normal LoRA and LightX2V fields remain load-compatible through a migration bridge; they no longer own graph node ids.",
            "WAN dual-noise supports target='all', 'high', and 'low'; standard rows run before role=speed rows on each branch.",
            "Selected WAN LoRA files must exist in the live LoraLoaderModelOnly catalog; generic LoraLoader fallback is forbidden.",
        ])
        compiled["rules"] = list(dict.fromkeys(str(rule) for rule in rules if str(rule)))
        return compiled

    module.build_wan22_gguf_i2v14_workflow = phase8_build
    for name in ("video_wan22_gguf_i2v14_compile_payload", "video_wan22_gguf_i2v14_generate_payload"):
        if hasattr(module, name):
            _wrap_payload_context(module, name)
    module._neo_phase8_video_lora_installed = True
    module._neo_phase8_video_lora_original_build = original_build


def install_wan_lora_integration() -> None:
    """Install Phase-8 WAN single-model and dual-noise Video LoRA migration/runtime once."""
    global _INSTALLED
    if _INSTALLED:
        return

    from neo_app.video import wan_gguf_i2v14_compiler as dual
    from neo_app.video import wan_txt2vid_compiler as single

    if not getattr(single, "_neo_phase8_video_lora_installed", False):
        _install_single_model_wan(single)
    if not getattr(dual, "_neo_phase8_video_lora_installed", False):
        _install_dual_noise_wan(dual)
    _INSTALLED = True
